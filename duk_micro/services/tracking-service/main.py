# services/tracking-service/main.py
"""
Tracking Service — Full port of backend/routers/tracking.py.
Provides real-time GPS state, ETA, stops-ahead, and route geometry.
"""
import asyncio
import httpx
import logging
import math
import os
import sys
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.redis_client import get_redis
from libs.duk_common.cache.stops_cache import get_all_stops
from libs.duk_common.osrm_client import (
    get_osrm_duration_s, get_osrm_route_geometry, get_osrm_distance_matrix_m,
    haversine_m, BUS_FACTOR, get_osrm_segment_geometry, snap_live_gps, haversine_m as haversine_m_math
)

from database_local import get_db
from models_local import Trip, BusStop, GpsLog, Route

logging.basicConfig(level=logging.INFO)
logger   = logging.getLogger(__name__)
app      = FastAPI(title="tracking-service")

IST_OFFSET = timedelta(hours=5, minutes=30)
ETA_ML_URL = os.environ.get("ETA_ML_URL", "http://eta-ml-service:8000")

MORNING_START_MINS = 360  # 6:00 AM
MORNING_END_MINS = 660    # 11:00 AM
EVENING_START_MINS = 1020 # 5:00 PM
EVENING_END_MINS = 1230   # 8:30 PM
MORNING_WAIT_START = 330  # 5:30 AM
EVENING_WAIT_START = 990  # 4:30 PM

def to_ist(utc_dt: datetime) -> datetime:
    return utc_dt + IST_OFFSET

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_m(lat1, lon1, lat2, lon2) / 1000.0

def find_nearest_stop_math(lat, lon, stops, threshold_km=10.0):
    best_dist = float("inf")
    best_stop = None
    for s in stops:
        dist = haversine_km(lat, lon, float(s["lat"]), float(s["lon"]))
        if dist < best_dist and dist <= threshold_km:
            best_dist = dist
            best_stop = s
    return best_stop

async def get_stops_ahead(lat: float, lon: float, stops: list, direction: str, visited: list) -> list:
    route_stops = [s for s in stops if s["route_id"] == 1] # Fallback route_id logic if needed, but we assume route_id matches trip
    route_stops.sort(key=lambda x: x["order_index"] if direction in ("forward", "morning", "Morning") else -x["order_index"])
    
    visited_set = set(visited or [])
    unvisited = [s for s in route_stops if s["id"] not in visited_set]
    
    if unvisited:
        # Sort unvisited stops logically by their intended traversal order
        # For 'forward', order_index goes up. For 'reverse', order_index goes down.
        pass
    return unvisited

def cluster_gps_points(points: list, radius_km=0.030) -> list:
    if not points:
        return []
    clustered = [points[0]]
    for p in points[1:]:
        if haversine_km(clustered[-1][0], clustered[-1][1], p[0], p[1]) >= radius_km:
            clustered.append(p)
    return clustered

async def get_active_trip(db: AsyncSession, now_ist: datetime) -> Optional[Trip]:
    today = now_ist.date()
    hour  = now_ist.hour
    if 6 <= hour < 11:
        direction = "forward"
    elif hour >= 17:
        direction = "reverse"
    else:
        return None

    result = await db.execute(
        select(Trip).where(
            and_(
                Trip.date      == today,
                Trip.direction == direction,
                Trip.status.in_(["scheduled", "on_trip", "late"]),
            )
        ).limit(1)
    )
    return result.scalar_one_or_none()

async def predict_eta(bus_lat, bus_lon, target_stop_lat, target_stop_lon, stops_remaining, hour_of_day, day_of_week, trip_direction, elapsed_minutes, speed_last_3):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            payload  = {
                "lat": bus_lat, "lon": bus_lon,
                "speed_kmh": speed_last_3,
                "direction": "reverse" if trip_direction == 1 else "forward",
                "stops_ahead": stops_remaining,
                "hour": hour_of_day,
                "minute": 0,
                "day_of_week": day_of_week,
                "is_late": 0,
                "late_by_minutes": 0,
            }
            resp = await client.post(f"{ETA_ML_URL}/predict", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                dur_s = data.get("eta_minutes", 0) * 60
                arr_time = to_ist(datetime.now(timezone.utc)) + timedelta(minutes=data.get("eta_minutes", 0))
                return {
                    "eta_minutes": data.get("eta_minutes", 0),
                    "arrival_time": arr_time.strftime("%-I:%M %p"),
                    "source": "ml",
                }
    except Exception as e:
        logger.debug("[TRACKING] ML ETA error: %s", e)
    
    # OSRM Fallback
    dur_s = await get_osrm_duration_s(bus_lat, bus_lon, target_stop_lat, target_stop_lon)
    eta_min = (dur_s / 60.0) * BUS_FACTOR
    arr_time = to_ist(datetime.now(timezone.utc)) + timedelta(minutes=eta_min)
    return {
        "eta_minutes": round(eta_min, 1),
        "arrival_time": arr_time.strftime("%-I:%M %p"),
        "source": "osrm",
    }

# Global Caches
_STOPS_CACHE = None
_ROUTES_CACHE = None
ETA_CACHE: dict[int, tuple[int, dict]] = {}

async def _get_all_stops(db: AsyncSession) -> list[dict]:
    global _STOPS_CACHE
    if _STOPS_CACHE is not None:
        return _STOPS_CACHE
    redis = await get_redis()
    stops = await get_all_stops(db, redis, BusStop)
    _STOPS_CACHE = stops
    return stops

def format_trip_name(direction: str | None) -> str:
    if not direction:
        return "Not in Service"
    d = direction.lower().strip()
    if d in ("forward", "morning"):
        return "Morning"
    elif d in ("reverse", "evening"):
        return "Evening"
    elif d == "unscheduled":
        return "Unscheduled"
    return direction

def _auto_late_minutes(direction: str, time_mins: int) -> int:
    return 0

# --- PORTED ENDPOINTS ---

@app.get("/api/v1/latest")
async def get_latest(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GpsLog.lat, GpsLog.lon, GpsLog.speed, GpsLog.server_time, GpsLog.ist_time)
        .where(GpsLog.lat.isnot(None))
        .order_by(desc(GpsLog.id)).limit(1)
    )
    latest = result.first()
    if not latest:
        raise HTTPException(status_code=404, detail="No GPS data yet")

    now_utc = datetime.now(timezone.utc)
    log_time = latest.server_time
    if log_time.tzinfo is None:
        log_time = log_time.replace(tzinfo=timezone.utc)
    is_live = (now_utc - log_time).total_seconds() <= 60

    return {
        "lat": latest.lat, "lon": latest.lon, "raw_lat": latest.lat, "raw_lon": latest.lon,
        "speed_kmh": latest.speed,
        "server_time": latest.ist_time.isoformat() if latest.ist_time else log_time.isoformat(),
        "is_live": is_live,
    }

@app.get("/api/v1/trip_state")
async def trip_state(db: AsyncSession = Depends(get_db)):
    gps_res = await db.execute(select(GpsLog).where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.id)).limit(1))
    latest_gps = gps_res.scalar_one_or_none()
    
    now_utc = datetime.now(timezone.utc)
    is_gps_alive = False
    gps_dead_minutes = 999
    
    if latest_gps and latest_gps.server_time:
        log_time = latest_gps.server_time
        if log_time.tzinfo is None:
            log_time = log_time.replace(tzinfo=timezone.utc)
        gps_dead_minutes = (now_utc - log_time).total_seconds() / 60.0
        if gps_dead_minutes <= 5.0:
            is_gps_alive = True

    now_ist = datetime.now(timezone.utc) + IST_OFFSET
    today = now_ist.date()
    time_mins = now_ist.hour * 60 + now_ist.minute

    result = await db.execute(select(Trip).where(Trip.date == today).order_by(desc(Trip.id)).limit(5))
    trips = result.scalars().all()

    if trips:
        is_deviated = False
        if is_gps_alive and latest_gps and latest_gps.lat:
            all_stops = await _get_all_stops(db)
            nearest = find_nearest_stop_math(latest_gps.lat, latest_gps.lon, all_stops, threshold_km=10.0)
            if nearest is None:
                is_deviated = True

        active = next((t for t in trips if t.status in ("on_trip", "active", "late")), None)
        if active:
            if active.status in ("on_trip", "active", "late") and gps_dead_minutes <= 30.0:
                is_gps_alive = True
                
            target_status = active.status if active.status != "on_trip" else "active"
            if not is_gps_alive:
                target_status = "connecting"
            
            display_trip = "Unscheduled" if is_deviated else format_trip_name(active.direction)
            if is_deviated and target_status != "connecting":
                target_status = "active"
                
            auto_late = _auto_late_minutes(active.direction, time_mins)
            late_val  = active.late_by_minutes if active.late_by_minutes is not None else auto_late

            return {
                "trip": display_trip, "status": target_status, "trip_id": active.id,
                "late_by_minutes": late_val, "cancellation_reason": None,
                "next_trip_time": None, "is_deviated": is_deviated,
            }
            
        cancelled = next((t for t in trips if t.status == "cancelled"), None)
        if cancelled:
            return {
                "trip": "Unscheduled" if is_gps_alive else "Not in Service",
                "status": "cancelled", "cancellation_reason": cancelled.cancellation_reason,
                "next_trip_time": None,
            }

        current_window = None
        if MORNING_START_MINS <= time_mins <= MORNING_END_MINS:
            current_window = "morning"
        elif EVENING_START_MINS <= time_mins <= EVENING_END_MINS:
            current_window = "evening"

        if current_window:
            completed_in_window = next((t for t in trips if t.status == "completed" and t.direction in (current_window, current_window.capitalize(), "forward" if current_window == "morning" else "reverse")), None)
            if completed_in_window:
                next_time = "05:40 PM" if current_window == "morning" else "07:30 AM"
                return {
                    "trip": "Unscheduled" if is_gps_alive else "Not in Service",
                    "status": "completed", "trip_id": completed_in_window.id,
                    "late_by_minutes": None, "cancellation_reason": None, "next_trip_time": next_time
                }

        scheduled_trips = [t for t in trips if t.status == "scheduled"]
        if scheduled_trips:
            active_target = None
            for t in scheduled_trips:
                is_m = t.direction in ("forward", "morning", "Morning")
                if is_m and (MORNING_START_MINS <= time_mins <= MORNING_END_MINS):
                    active_target = t
                    break
                elif not is_m and (EVENING_START_MINS <= time_mins <= EVENING_END_MINS):
                    active_target = t
                    break
            
            if active_target:
                target_trip = active_target
                is_morning = target_trip.direction in ("forward", "morning", "Morning")
                is_active_window = True
            else:
                scheduled_trips.sort(key=lambda x: 0 if x.direction in ("forward", "morning", "Morning") else 1)
                if time_mins > MORNING_END_MINS:
                    evening_trips = [t for t in scheduled_trips if t.direction not in ("forward", "morning", "Morning")]
                    target_trip = evening_trips[0] if evening_trips else scheduled_trips[0]
                else:
                    target_trip = scheduled_trips[0]
                is_morning = target_trip.direction in ("forward", "morning", "Morning")
                is_active_window = False
                
            if is_active_window:
                display_trip = "Unscheduled" if is_deviated else format_trip_name(target_trip.direction)
                status = "connecting"
                if is_gps_alive:
                    status = "active"
                return {
                    "trip": display_trip, "status": status, "trip_id": target_trip.id,
                    "late_by_minutes": _auto_late_minutes(target_trip.direction, time_mins),
                    "cancellation_reason": None, "next_trip_time": None, "is_deviated": is_deviated,
                }
            else:
                if is_gps_alive:
                    return {
                        "trip": "Unscheduled", "status": "active", "trip_id": target_trip.id,
                        "late_by_minutes": None, "cancellation_reason": None, "next_trip_time": None,
                    }

                is_waiting_window = False
                if is_morning and (MORNING_WAIT_START <= time_mins < MORNING_START_MINS):
                    is_waiting_window = True
                elif not is_morning and (EVENING_WAIT_START <= time_mins < EVENING_START_MINS):
                    is_waiting_window = True
                
                return {
                    "trip": "Not in Service", "status": "waiting" if is_waiting_window else "offline",
                    "trip_id": target_trip.id, "late_by_minutes": None, "cancellation_reason": None,
                    "next_trip_time": "07:30 AM" if is_morning else "05:40 PM",
                }

    if today.weekday() >= 5:
        return {"trip": "Unscheduled" if is_gps_alive else "Not in Service", "status": "active" if is_gps_alive else "weekend", "next_trip_time": "Mon 07:30 AM"}

    if today.weekday() == 4 and time_mins > EVENING_END_MINS:
        return {"trip": "Unscheduled" if is_gps_alive else "Not in Service", "status": "active" if is_gps_alive else "completed", "next_trip_time": "Mon 07:30 AM"}

    if MORNING_START_MINS <= time_mins <= MORNING_END_MINS or EVENING_START_MINS <= time_mins <= EVENING_END_MINS:
        dir_name = "Morning" if time_mins <= MORNING_END_MINS else "Evening"
        return {"trip": dir_name, "status": "active" if is_gps_alive else "connecting", "next_trip_time": None}
    
    if is_gps_alive:
        return {"trip": "Unscheduled", "status": "active", "next_trip_time": None}

    if MORNING_WAIT_START <= time_mins < MORNING_START_MINS:
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "07:30 AM"}
    elif EVENING_WAIT_START <= time_mins < EVENING_START_MINS:
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "05:40 PM"}
    else:
        next_trip = "07:30 AM" if time_mins < MORNING_WAIT_START or time_mins > EVENING_END_MINS else "05:40 PM"
        return {"trip": "Not in Service", "status": "offline", "next_trip_time": next_trip}


@app.get("/api/v1/route_history")
async def route_history(trip_id: int = None, db: AsyncSession = Depends(get_db)):
    stops_dicts = await _get_all_stops(db)

    if trip_id:
        trip_res = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip = trip_res.scalar_one_or_none()
        if trip and trip.started_at:
            end_time = trip.ended_at or datetime.now(timezone.utc)
            result = await db.execute(
                select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.server_time >= trip.started_at, GpsLog.server_time <= end_time).order_by(GpsLog.id)
            )
            logs = result.scalars().all()
        else:
            logs = []
    else:
        now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
        is_evening = now_ist.hour > 17 or (now_ist.hour == 17 and now_ist.minute >= 30)
        today_ist = now_ist.date()
        start_of_today = datetime.combine(today_ist, datetime.min.time())

        result = await db.execute(
            select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start_of_today).order_by(GpsLog.id)
        )
        logs = result.scalars().all()

        filtered_logs = []
        for log in logs:
            if log.ist_time is None: continue
            entry_hour = log.ist_time.hour
            log_is_evening = entry_hour > 17 or (entry_hour == 17 and log.ist_time.minute >= 30)
            if is_evening == log_is_evening:
                filtered_logs.append(log)
        logs = filtered_logs

    visited_stops = {}
    arrival_times = {}

    if trip_id and trip and trip.visited_stops:
        for stop_id in trip.visited_stops:
            stop_match = next((s for s in stops_dicts if s["id"] == stop_id), None)
            if stop_match:
                visited_stops[stop_match["name"]] = True

    for log in logs:
        if log.ist_time is None: continue
        nearest = find_nearest_stop_math(log.lat, log.lon, stops_dicts, threshold_km=0.3)
        if nearest:
            name = nearest["name"]
            if name not in arrival_times:
                visited_stops[name] = True
                arrival_times[name] = log.ist_time.strftime("%I:%M %p")

    return {"visitedStops": visited_stops, "arrivalTimes": arrival_times}

@app.get("/api/v1/stops")
async def get_stops(db: AsyncSession = Depends(get_db)):
    return await _get_all_stops(db)

@app.get("/api/v1/routes")
async def get_routes(db: AsyncSession = Depends(get_db)):
    global _ROUTES_CACHE
    if _ROUTES_CACHE is not None:
        return _ROUTES_CACHE

    result = await db.execute(select(Route))
    routes = result.scalars().all()
    all_stops = await _get_all_stops(db)

    output = []
    for route in routes:
        route_stops = [s for s in all_stops if s.get("route_id") == route.id]
        output.append({"id": route.id, "name": route.name, "stops": route_stops})
        
    _ROUTES_CACHE = output
    return _ROUTES_CACHE

@app.get("/api/v1/route_geometry")
async def get_route_geometry_full(db: AsyncSession = Depends(get_db)):
    stops = await _get_all_stops(db)
    if not stops:
        return {"coordinates": []}
    waypoints = [(s["lat"], s["lon"]) for s in stops]
    coords = await get_osrm_route_geometry(waypoints)
    return {"coordinates": coords}

@app.get("/api/v1/route_segment")
async def get_route_segment(lat1: float = Query(...), lon1: float = Query(...), lat2: float = Query(...), lon2: float = Query(...)):
    coords = await get_osrm_segment_geometry(lat1, lon1, lat2, lon2)
    return {"coordinates": coords}

@app.post("/api/v1/snap_route")
async def snap_route(payload: dict):
    raw_pts = payload.get("points", [])
    if not raw_pts:
        return {"coordinates": []}

    waypoints = []
    for p in raw_pts:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            waypoints.append((float(p[1]), float(p[0])) if abs(float(p[0])) > 40 else (float(p[0]), float(p[1])))
        elif isinstance(p, dict) and "lat" in p and "lon" in p:
            waypoints.append((float(p["lat"]), float(p["lon"])))

    coords = await get_osrm_route_geometry(waypoints)
    return {"coordinates": coords}


@app.get("/api/v1/eta")
async def get_eta(stop_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GpsLog.id, GpsLog.lat, GpsLog.lon, GpsLog.speed).where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.id)).limit(5))
    recent_logs = result.all()
    if not recent_logs:
        raise HTTPException(status_code=404, detail="No bus data available")

    latest = recent_logs[0]

    if stop_id in ETA_CACHE:
        cached_log_id, cached_response = ETA_CACHE[stop_id]
        if cached_log_id == latest.id:
            return cached_response

    stop_result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop = stop_result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")

    speeds = [log.speed for log in recent_logs if log.speed is not None]
    speed_avg = sum(speeds) / len(speeds) if speeds else 20.0

    now = datetime.now(timezone.utc)
    active_trip = await get_active_trip(db, to_ist(now))
    direction = active_trip.direction if active_trip else ("reverse" if to_ist(now).hour >= 17 else "forward")
    visited = active_trip.visited_stops if active_trip else []

    all_stops = await _get_all_stops(db)
    ahead = await get_stops_ahead(latest.lat, latest.lon, all_stops, direction, visited)
    
    target_in_ahead = next((s for s in ahead if s["id"] == stop_id), None)
    if not target_in_ahead and stop_id in visited:
        response = {"stop_id": stop_id, "stop_name": stop.name, "status": "passed"}
        ETA_CACHE[stop_id] = (latest.id, response)
        return response
        
    if target_in_ahead and target_in_ahead.get("deviated"):
        response = {"stop_id": stop_id, "stop_name": stop.name, "status": "deviated"}
        ETA_CACHE[stop_id] = (latest.id, response)
        return response

    stops_remaining = len([s for s in ahead if s["order_index"] <= stop.order_index])
    snapped_bus_lat, snapped_bus_lon = await snap_live_gps(latest.lat, latest.lon)

    elapsed_minutes = 0.0
    if active_trip and active_trip.started_at:
        trip_started = active_trip.started_at
        if trip_started.tzinfo is None:
            trip_started = trip_started.replace(tzinfo=timezone.utc)
        elapsed_minutes = max(0.0, (now - trip_started).total_seconds() / 60.0)

    prediction = await predict_eta(
        bus_lat=snapped_bus_lat, bus_lon=snapped_bus_lon, target_stop_lat=stop.lat, target_stop_lon=stop.lon,
        stops_remaining=stops_remaining, hour_of_day=now.hour, day_of_week=now.weekday(),
        trip_direction=1 if now.hour >= 17 else 0, elapsed_minutes=elapsed_minutes, speed_last_3=speed_avg
    )

    response = {"stop_id": stop_id, "stop_name": stop.name, "bus_lat": snapped_bus_lat, "bus_lon": snapped_bus_lon, **prediction}
    ETA_CACHE[stop_id] = (latest.id, response)
    return response

@app.get("/api/v1/eta/all")
async def get_all_etas(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GpsLog.id, GpsLog.lat, GpsLog.lon, GpsLog.speed).where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.id)).limit(5))
    recent_logs = result.all()
    if not recent_logs: return {}

    latest = recent_logs[0]
    speeds = [log.speed for log in recent_logs if log.speed is not None]
    speed_avg = sum(speeds) / len(speeds) if speeds else 20.0

    now = datetime.now(timezone.utc)
    active_trip = await get_active_trip(db, to_ist(now))
    direction = active_trip.direction if active_trip else ("reverse" if to_ist(now).hour >= 17 else "forward")
    visited = active_trip.visited_stops if active_trip else []

    all_stops = await _get_all_stops(db)
    ahead = await get_stops_ahead(latest.lat, latest.lon, all_stops, direction, visited)
    snapped_bus_lat, snapped_bus_lon = await snap_live_gps(latest.lat, latest.lon)

    elapsed_minutes = 0.0
    if active_trip and active_trip.started_at:
        trip_started = active_trip.started_at
        if trip_started.tzinfo is None:
            trip_started = trip_started.replace(tzinfo=timezone.utc)
        elapsed_minutes = max(0.0, (now - trip_started).total_seconds() / 60.0)

    etas = {}
    tasks = []
    
    for stop in ahead:
        stops_remaining = len([s for s in ahead if s["order_index"] <= stop["order_index"]])
        tasks.append(predict_eta(
            bus_lat=snapped_bus_lat, bus_lon=snapped_bus_lon, target_stop_lat=stop["lat"], target_stop_lon=stop["lon"],
            stops_remaining=stops_remaining, hour_of_day=now.hour, day_of_week=now.weekday(),
            trip_direction=1 if now.hour >= 17 else 0, elapsed_minutes=elapsed_minutes, speed_last_3=speed_avg
        ))
        
    predictions = await asyncio.gather(*tasks, return_exceptions=True)
    
    for idx, stop in enumerate(ahead):
        pred = predictions[idx]
        if not isinstance(pred, Exception):
            etas[stop["id"]] = pred["eta_minutes"]

    return etas

@app.get("/api/v1/history/{date_str}")
async def history_by_date(date_str: str, db: AsyncSession = Depends(get_db)):
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    start = datetime.combine(target_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
    end   = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))

    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start, GpsLog.ist_time < end).order_by(GpsLog.id)
    )
    logs = result.scalars().all()

    route_points = []
    last_lat, last_lon = None, None
    for log in logs:
        if log.ist_time is None: continue
        hour = log.ist_time.hour
        if hour < 6 or hour >= 21: continue
        if last_lat is not None:
            dist = haversine_km(last_lat, last_lon, log.lat, log.lon)
            if dist < 0.01: continue
            if dist > 10.0: continue
        route_points.append({"lat": log.lat, "lon": log.lon, "time": log.ist_time.isoformat()})
        last_lat, last_lon = log.lat, log.lon

    return route_points

@app.get("/api/v1/current_trace")
async def get_current_trace(db: AsyncSession = Depends(get_db)):
    now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    is_evening = now_ist.hour > 17 or (now_ist.hour == 17 and now_ist.minute >= 30)
    start_of_today = datetime.combine(now_ist.date(), datetime.min.time())

    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start_of_today).order_by(GpsLog.id)
    )
    logs = result.scalars().all()

    filtered_logs = []
    for log in logs:
        if log.ist_time is None: continue
        entry_hour = log.ist_time.hour
        log_is_evening = entry_hour > 17 or (entry_hour == 17 and log.ist_time.minute >= 30)
        if is_evening == log_is_evening:
            filtered_logs.append(log)

    if not filtered_logs:
        return {"coordinates": []}

    raw_tuples = [(log.lat, log.lon) for log in filtered_logs]
    filtered_waypoints = cluster_gps_points(raw_tuples, radius_km=0.030)

    if len(filtered_waypoints) < 2:
        return {"coordinates": [[w[1], w[0]] for w in filtered_waypoints], "trip_status": "active", "started_at": start_of_today.isoformat(), "ended_at": None}

    validated = [filtered_waypoints[0]]
    for i in range(1, len(filtered_waypoints)):
        dist_m = haversine_km(validated[-1][0], validated[-1][1], filtered_waypoints[i][0], filtered_waypoints[i][1]) * 1000
        if dist_m <= 5000:
            validated.append(filtered_waypoints[i])

    if len(validated) < 2:
        return {"coordinates": [[w[1], w[0]] for w in validated], "trip_status": "active", "started_at": start_of_today.isoformat(), "ended_at": None}

    coords = await get_osrm_route_geometry(validated)
    return {"coordinates": coords, "trip_status": "active", "started_at": start_of_today.isoformat(), "ended_at": None}

_TRIP_TRACE_CACHE = {}

@app.get("/api/v1/trip_trace/{trip_id}")
async def get_trip_trace(trip_id: int, db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc).timestamp()
    if trip_id in _TRIP_TRACE_CACHE:
        cache_time, cache_data = _TRIP_TRACE_CACHE[trip_id]
        if cache_time == float('inf') or now - cache_time < 15.0:
            return cache_data

    trip_res = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = trip_res.scalar_one_or_none()
    if not trip or not trip.started_at:
        return {"coordinates": []}
        
    is_permanent = trip.status in ('completed', 'cancelled')
    cache_time = float('inf') if is_permanent else now
    
    end_time = trip.ended_at or datetime.now(timezone.utc)
    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.server_time >= trip.started_at, GpsLog.server_time <= end_time).order_by(GpsLog.id)
    )
    logs = result.scalars().all()
    
    if not logs:
        return {"coordinates": []}
        
    raw_tuples = [(log.lat, log.lon) for log in logs]
    filtered_waypoints = cluster_gps_points(raw_tuples, radius_km=0.030)

    if len(filtered_waypoints) < 2:
        return {"coordinates": [[w[1], w[0]] for w in filtered_waypoints], "trip_status": trip.status, "started_at": trip.started_at.isoformat(), "ended_at": end_time.isoformat() if trip.ended_at else None}

    validated = [filtered_waypoints[0]]
    for i in range(1, len(filtered_waypoints)):
        dist_m = haversine_km(validated[-1][0], validated[-1][1], filtered_waypoints[i][0], filtered_waypoints[i][1]) * 1000
        if dist_m <= 5000:
            validated.append(filtered_waypoints[i])

    coords = await get_osrm_route_geometry(validated) if len(validated) >= 2 else []
    
    resp = {
        "coordinates": coords,
        "trip_status": trip.status,
        "started_at": trip.started_at.isoformat(),
        "ended_at": trip.ended_at.isoformat() if trip.ended_at else None
    }
    
    _TRIP_TRACE_CACHE[trip_id] = (cache_time, resp)
    return resp
