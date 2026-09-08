# services/tracking-service/main.py
"""
Tracking Service — The core backend API for the passenger PWA.

Provides real-time GPS state, ETA predictions, route geometry, and stop lists.

How the live tracking works:
  1. The PWA asks for the current bus location via GET /api/v1/latest
  2. This service reads the latest GPS state from Redis (grid-based cache)
  3. If Nominatim (reverse geocoding) hasn't finished fetching the area name,
     this service waits up to 1.5s for it, then falls back to OSRM.
  4. The returned location is drawn on the map.
  5. The PWA then connects via WebSocket (realtime-gateway) to get subsequent
     live updates without polling.

Endpoints:
  GET /api/v1/stops           — List of all bus stops
  GET /api/v1/routes          — List of available routes
  GET /api/v1/latest          — Most recent GPS location + direction + ETA
  GET /api/v1/trip_state      — Current active trip ID and status
  GET /api/v1/eta             — Estimated arrival time at a specific stop
  GET /api/v1/route_geometry  — GeoJSON lines to draw the bus route on the map
  GET /api/v1/route_segment   — Snap a user's location to the nearest bus route
"""
import asyncio
import httpx
import logging
import os
import sys
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select, desc, and_
from libs.duk_common.geocoding import get_location_name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from libs.duk_common.redis_client import get_redis
from libs.duk_common.cache.stops_cache import get_all_stops, INVALIDATE_CHANNEL
from libs.duk_common.osrm_client import (
    get_osrm_duration_s,
    get_osrm_route_geometry_multi,
    get_osrm_match_geometry,
    snap_points_individually,
    get_osrm_distance_matrix_m,
    haversine_m,
    BUS_FACTOR,
    snap_to_road,
    close_client as close_osrm_client,
)
from libs.duk_common.middleware import configure_app
from libs.duk_common.settings import get_settings

from database_local import get_db
from models_local import Trip, BusStop, GpsLog, Route

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(title="tracking-service", docs_url=None, redoc_url=None)
configure_app(app, allowed_origins=settings.allowed_origins_list)

IST_OFFSET = timedelta(hours=5, minutes=30)
ETA_ML_URL = os.environ.get("ETA_ML_URL", "http://eta-ml-service:8003")

MORNING_START_MINS = 360
MORNING_END_MINS = 660
EVENING_START_MINS = 1020
EVENING_END_MINS = 1230
MORNING_WAIT_START = 330
EVENING_WAIT_START = 990


class BoundedCache(OrderedDict):
    def __init__(self, maxsize: int = 256):
        super().__init__()
        self.maxsize = maxsize

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            oldest = next(iter(self))
            del self[oldest]


ETA_CACHE: BoundedCache = BoundedCache(maxsize=128)
_TRIP_TRACE_CACHE: BoundedCache = BoundedCache(maxsize=64)
_ROUTES_CACHE = None

#  Pooled httpx client for eta-ml-service calls (was: new client per request) 
_ETA_CLIENT: Optional[httpx.AsyncClient] = None


def _get_eta_client() -> httpx.AsyncClient:
    global _ETA_CLIENT
    if _ETA_CLIENT is None:
        _ETA_CLIENT = httpx.AsyncClient(
            base_url=ETA_ML_URL,
            timeout=httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )
    return _ETA_CLIENT


#  Startup / shutdown 
@app.on_event("startup")
async def _on_startup():
    redis = await get_redis()
    asyncio.create_task(_routes_invalidation_loop(redis))


@app.on_event("shutdown")
async def _on_shutdown():
    if _ETA_CLIENT:
        await _ETA_CLIENT.aclose()
    await close_osrm_client()


async def _routes_invalidation_loop(redis):
    """Background task: clear the process-local routes cache on admin edits."""
    global _ROUTES_CACHE
    pubsub = redis.pubsub()
    await pubsub.subscribe(INVALIDATE_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                _ROUTES_CACHE = None
                logger.info("[CACHE] tracking-service routes cache invalidated")
    except asyncio.CancelledError:
        pass


#  OSRM helpers (now delegate to the shared pooled client) 
async def _get_route_geometry_multi(waypoints: list[tuple[float, float]]) -> list:
    return await get_osrm_route_geometry_multi(waypoints)


async def snap_live_gps(lat: float, lon: float) -> tuple[float, float]:
    return await snap_to_road(lat, lon)


async def get_osrm_segment_geometry(lat1, lon1, lat2, lon2):
    return await get_osrm_route_geometry_multi([(lat1, lon1), (lat2, lon2)])


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


async def get_stops_ahead(lat: float, lon: float, stops: list, direction: str, visited: list, route_id: int) -> list:
    route_stops = [s for s in stops if s["route_id"] == route_id]
    is_forward = direction in ("forward", "morning", "Morning")
    route_stops.sort(key=lambda x: x["order_index"] if is_forward else -x["order_index"])
    visited_set = set(visited or [])
    return [s for s in route_stops if s["id"] not in visited_set]


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
    hour = now_ist.hour
    if 6 <= hour < 11:
        direction = "forward"
    elif hour >= 17:
        direction = "reverse"
    else:
        return None
    result = await db.execute(
        select(Trip).where(
            and_(
                Trip.date == today,
                Trip.direction == direction,
                Trip.status.in_(["scheduled", "on_trip", "late"]),
            )
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def predict_eta(bus_lat, bus_lon, target_stop_lat, target_stop_lon, stops_remaining,
                       hour_of_day, day_of_week, trip_direction, elapsed_minutes, speed_last_3):
    try:
        payload = {
            "lat": bus_lat, "lon": bus_lon,
            "target_lat": target_stop_lat, "target_lon": target_stop_lon,
            "speed_kmh": speed_last_3,
            "direction": "reverse" if trip_direction == 1 else "forward",
            "stops_ahead": stops_remaining,
            "hour": hour_of_day,
            "minute": 0,
            "day_of_week": day_of_week,
            "is_late": 0,
            "late_by_minutes": 0,
        }
        resp = await _get_eta_client().post("/api/v1/ml/predict", json=payload)
        if resp.status_code == 200:
            data = resp.json()
            arr_time = to_ist(datetime.now(timezone.utc)) + timedelta(minutes=data.get("eta_minutes", 0))
            return {
                "eta_minutes": data.get("eta_minutes", 0),
                "arrival_time": arr_time.strftime("%-I:%M %p"),
                "source": "ml",
            }
    except Exception as e:
        logger.debug("[TRACKING] ML ETA error: %s", e)

    dur_s = await get_osrm_duration_s(bus_lat, bus_lon, target_stop_lat, target_stop_lon)
    eta_min = (dur_s / 60.0) * BUS_FACTOR
    arr_time = to_ist(datetime.now(timezone.utc)) + timedelta(minutes=eta_min)
    return {
        "eta_minutes": round(eta_min, 1),
        "arrival_time": arr_time.strftime("%-I:%M %p"),
        "source": "osrm",
    }


async def _get_all_stops(db: AsyncSession) -> list[dict]:
    redis = await get_redis()
    return await get_all_stops(db, redis, BusStop)


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
    depart_mins = 450 if direction in ("forward", "morning", "Morning") else 1060
    delay = time_mins - depart_mins
    return delay if delay > 0 else 0


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

    redis = await get_redis()
    location_name = await get_location_name(latest.lat, latest.lon, redis, wait_timeout=1.5)

    return {
        "lat": latest.lat, "lon": latest.lon, "raw_lat": latest.lat, "raw_lon": latest.lon,
        "speed_kmh": latest.speed,
        "ist_time": latest.ist_time.isoformat() if latest.ist_time else log_time.isoformat(),
        "is_live": is_live,
        "location_name": location_name,
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
            late_val = active.late_by_minutes if active.late_by_minutes is not None else auto_late
            return {
                "trip": display_trip, "status": target_status, "trip_id": active.id,
                "late_by_minutes": late_val, "cancellation_reason": None,
                "next_trip_time": None, "is_deviated": is_deviated,
                "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}

        cancelled = next((t for t in trips if t.status == "cancelled"), None)
        if cancelled:
            return {
                "trip": "Unscheduled" if is_gps_alive else "Not in Service",
                "status": "cancelled", "cancellation_reason": cancelled.cancellation_reason,
                "next_trip_time": None,
                "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}

        current_window = None
        if MORNING_START_MINS <= time_mins <= MORNING_END_MINS:
            current_window = "morning"
        elif EVENING_START_MINS <= time_mins <= EVENING_END_MINS:
            current_window = "evening"

        if current_window:
            completed_in_window = next(
                (t for t in trips if t.status == "completed" and t.direction in
                 (current_window, current_window.capitalize(), "forward" if current_window == "morning" else "reverse")),
                None,
            )
            if completed_in_window:
                next_time = "05:40 PM" if current_window == "morning" else "07:30 AM"
                return {
                    "trip": "Unscheduled" if is_gps_alive else "Not in Service",
                    "status": "completed", "trip_id": completed_in_window.id,
                    "late_by_minutes": None, "cancellation_reason": None, "next_trip_time": next_time,
                "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}

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
                "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}
            else:
                if is_gps_alive:
                    return {
                        "trip": "Unscheduled", "status": "active", "trip_id": target_trip.id,
                        "late_by_minutes": None, "cancellation_reason": None, "next_trip_time": None,
                "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}
                is_waiting_window = False
                if is_morning and (MORNING_WAIT_START <= time_mins < MORNING_START_MINS):
                    is_waiting_window = True
                elif not is_morning and (EVENING_WAIT_START <= time_mins < EVENING_START_MINS):
                    is_waiting_window = True

                return {
                    "trip": "Not in Service", "status": "waiting" if is_waiting_window else "offline",
                    "trip_id": target_trip.id, "late_by_minutes": None, "cancellation_reason": None,
                    "next_trip_time": "07:30 AM" if is_morning else "05:40 PM",
                "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}

    if today.weekday() >= 5:
        return {"trip": "Unscheduled" if is_gps_alive else "Not in Service",
                "status": "active" if is_gps_alive else "weekend", "next_trip_time": "Mon 07:30 AM", "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}

    if today.weekday() == 4 and time_mins > EVENING_END_MINS:
        return {"trip": "Unscheduled" if is_gps_alive else "Not in Service",
                "status": "active" if is_gps_alive else "completed", "next_trip_time": "Mon 07:30 AM", "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}

    if MORNING_START_MINS <= time_mins <= MORNING_END_MINS or EVENING_START_MINS <= time_mins <= EVENING_END_MINS:
        dir_name = "Morning" if time_mins <= MORNING_END_MINS else "Evening"
        return {"trip": dir_name, "status": "active" if is_gps_alive else "connecting", "next_trip_time": None, "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}

    if is_gps_alive:
        return {"trip": "Unscheduled", "status": "active", "next_trip_time": None, "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}
    if MORNING_WAIT_START <= time_mins < MORNING_START_MINS:
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "07:30 AM", "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}
    elif EVENING_WAIT_START <= time_mins < EVENING_START_MINS:
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "05:40 PM", "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}
    else:
        next_trip = "07:30 AM" if time_mins < MORNING_WAIT_START or time_mins > EVENING_END_MINS else "05:40 PM"
        return {"trip": "Not in Service", "status": "offline", "next_trip_time": next_trip, "last_updated_time": latest_gps.server_time.isoformat() if latest_gps and latest_gps.server_time else None}


@app.get("/api/v1/route_history")
async def route_history(trip_id: int = None, db: AsyncSession = Depends(get_db)):
    stops_dicts = await _get_all_stops(db)
    trip = None

    if trip_id:
        trip_res = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip = trip_res.scalar_one_or_none()
        if trip and trip.started_at:
            end_time = trip.ended_at or datetime.now(timezone.utc)
            result = await db.execute(
                select(GpsLog).where(
                    GpsLog.lat.isnot(None),
                    GpsLog.server_time >= trip.started_at,
                    GpsLog.server_time <= end_time,
                ).order_by(GpsLog.id)
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
            if log.ist_time is None:
                continue
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
        if log.ist_time is None:
            continue
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
    coords = await get_osrm_route_geometry_multi(waypoints)   # FIXED (was broken 4-arg call)
    return {"coordinates": coords}


@app.get("/api/v1/route_segment")
async def get_route_segment(lat1: float = Query(...), lon1: float = Query(...),
                             lat2: float = Query(...), lon2: float = Query(...)):
    coords = await get_osrm_segment_geometry(lat1, lon1, lat2, lon2)
    return {"coordinates": coords}


@app.post("/api/v1/snap_route")
async def snap_route(payload: dict):
    raw_pts = payload.get("points", [])
    if not raw_pts:
        return {"coordinates": []}

    # Cap input to prevent computational DoS via OSRM
    MAX_SNAP_POINTS = 100
    if len(raw_pts) > MAX_SNAP_POINTS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many points: {len(raw_pts)}. Maximum allowed is {MAX_SNAP_POINTS}.",
        )

    waypoints = []
    for p in raw_pts:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            waypoints.append((float(p[1]), float(p[0])) if abs(float(p[0])) > 40 else (float(p[0]), float(p[1])))
        elif isinstance(p, dict) and "lat" in p and "lon" in p:
            waypoints.append((float(p["lat"]), float(p["lon"])))

    coords = await get_osrm_match_geometry(waypoints)
    return {"coordinates": coords}


@app.get("/api/v1/eta")
async def get_eta(stop_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GpsLog.id, GpsLog.lat, GpsLog.lon, GpsLog.speed)
        .where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.id)).limit(5)
    )
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
    ahead = await get_stops_ahead(latest.lat, latest.lon, all_stops, direction, visited,
                                   active_trip.route_id if active_trip else 1)

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
        trip_direction=1 if now.hour >= 17 else 0, elapsed_minutes=elapsed_minutes, speed_last_3=speed_avg,
    )
    response = {"stop_id": stop_id, "stop_name": stop.name, "bus_lat": snapped_bus_lat, "bus_lon": snapped_bus_lon, **prediction}
    ETA_CACHE[stop_id] = (latest.id, response)
    return response


# In-memory + Redis cache for /eta/all.
# Key: "eta_all:{bucket_lat}:{bucket_lon}:{trip_id}" where bucket rounds to 3dp (~111 m).
# TTL: 30 seconds — short enough to stay fresh, long enough to absorb burst polling.
_ETA_ALL_LOCAL: dict = {}  # process-level L1 cache (key -> (result, expires_at))
_ETA_ALL_TTL = 30


@app.get("/api/v1/eta/all")
async def get_all_etas(db: AsyncSession = Depends(get_db)):
    import time as _time
    result = await db.execute(
        select(GpsLog.id, GpsLog.lat, GpsLog.lon, GpsLog.speed)
        .where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.id)).limit(5)
    )
    recent_logs = result.all()
    if not recent_logs:
        return {}

    latest = recent_logs[0]
    speeds = [log.speed for log in recent_logs if log.speed is not None]
    speed_avg = sum(speeds) / len(speeds) if speeds else 20.0

    now = datetime.now(timezone.utc)
    active_trip = await get_active_trip(db, to_ist(now))
    direction = active_trip.direction if active_trip else ("reverse" if to_ist(now).hour >= 17 else "forward")
    visited = active_trip.visited_stops if active_trip else []
    trip_id = active_trip.id if active_trip else 0

    # Build cache key from position bucket (~111 m resolution) + trip
    blat = round(latest.lat, 3)
    blon = round(latest.lon, 3)
    cache_key = f"eta_all:{blat}:{blon}:{trip_id}"

    # L1: process-level cache
    now_ts = _time.monotonic()
    if cache_key in _ETA_ALL_LOCAL:
        cached_result, expires_at = _ETA_ALL_LOCAL[cache_key]
        if now_ts < expires_at:
            return cached_result

    # L2: Redis cache
    try:
        redis = await get_redis()
        redis_cached = await redis.get(f"svc:{cache_key}")
        if redis_cached:
            import json as _json
            result_dict = _json.loads(redis_cached)
            _ETA_ALL_LOCAL[cache_key] = (result_dict, now_ts + _ETA_ALL_TTL)
            return result_dict
    except Exception:
        pass

    all_stops = await _get_all_stops(db)
    ahead = await get_stops_ahead(latest.lat, latest.lon, all_stops, direction, visited,
                                   active_trip.route_id if active_trip else 1)
    snapped_bus_lat, snapped_bus_lon = await snap_live_gps(latest.lat, latest.lon)

    elapsed_minutes = 0.0
    if active_trip and active_trip.started_at:
        trip_started = active_trip.started_at
        if trip_started.tzinfo is None:
            trip_started = trip_started.replace(tzinfo=timezone.utc)
        elapsed_minutes = max(0.0, (now - trip_started).total_seconds() / 60.0)

    tasks = []
    for stop in ahead:
        stops_remaining = len([s for s in ahead if s["order_index"] <= stop["order_index"]])
        tasks.append(predict_eta(
            bus_lat=snapped_bus_lat, bus_lon=snapped_bus_lon, target_stop_lat=stop["lat"], target_stop_lon=stop["lon"],
            stops_remaining=stops_remaining, hour_of_day=now.hour, day_of_week=now.weekday(),
            trip_direction=1 if now.hour >= 17 else 0, elapsed_minutes=elapsed_minutes, speed_last_3=speed_avg,
        ))

    predictions = await asyncio.gather(*tasks, return_exceptions=True)

    etas = {}
    for idx, stop in enumerate(ahead):
        pred = predictions[idx]
        if not isinstance(pred, Exception):
            etas[stop["id"]] = pred["eta_minutes"]

    # Write back to both cache layers
    _ETA_ALL_LOCAL[cache_key] = (etas, now_ts + _ETA_ALL_TTL)
    try:
        import json as _json
        await redis.set(f"svc:{cache_key}", _json.dumps(etas), ex=_ETA_ALL_TTL)
    except Exception:
        pass

    return etas


@app.get("/api/v1/history/{date_str}")
async def history_by_date(date_str: str, db: AsyncSession = Depends(get_db)):
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    start = datetime.combine(target_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
    end = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))

    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start, GpsLog.ist_time < end).order_by(GpsLog.id)
    )
    logs = result.scalars().all()

    route_points = []
    last_lat, last_lon = None, None
    for log in logs:
        if log.ist_time is None:
            continue
        hour = log.ist_time.hour
        if hour < 6 or hour >= 21:
            continue
        if last_lat is not None:
            dist = haversine_km(last_lat, last_lon, log.lat, log.lon)
            if dist < 0.01:
                continue
            if dist > 10.0:
                continue
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
        if log.ist_time is None:
            continue
        entry_hour = log.ist_time.hour
        log_is_evening = entry_hour > 17 or (entry_hour == 17 and log.ist_time.minute >= 30)
        if is_evening == log_is_evening:
            filtered_logs.append(log)

    if not filtered_logs:
        return {"coordinates": []}

    raw_tuples = [(log.lat, log.lon) for log in filtered_logs]
    filtered_waypoints = cluster_gps_points(raw_tuples, radius_km=0.030)
    if len(filtered_waypoints) < 2:
        return {"coordinates": [[w[1], w[0]] for w in filtered_waypoints], "trip_status": "active",
                "started_at": start_of_today.isoformat(), "ended_at": None}

    validated = [filtered_waypoints[0]]
    for i in range(1, len(filtered_waypoints)):
        dist_m = haversine_km(validated[-1][0], validated[-1][1], filtered_waypoints[i][0], filtered_waypoints[i][1]) * 1000
        if dist_m <= 5000:
            validated.append(filtered_waypoints[i])

    if len(validated) < 2:
        return {"coordinates": [[w[1], w[0]] for w in validated], "trip_status": "active",
                "started_at": start_of_today.isoformat(), "ended_at": None}

    coords = await get_osrm_route_geometry_multi(validated)   # FIXED
    return {"coordinates": coords, "trip_status": "active", "started_at": start_of_today.isoformat(), "ended_at": None}


@app.get("/api/v1/trip_trace/{trip_id}")
async def get_trip_trace(trip_id: int, db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc).timestamp()
    if trip_id in _TRIP_TRACE_CACHE:
        cache_time, cache_data = _TRIP_TRACE_CACHE[trip_id]
        if cache_time == float("inf") or now - cache_time < 15.0:
            return cache_data

    trip_res = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = trip_res.scalar_one_or_none()
    if not trip or not trip.started_at:
        return {"coordinates": []}

    is_permanent = trip.status in ("completed", "cancelled")
    cache_time = float("inf") if is_permanent else now

    end_time = trip.ended_at or datetime.now(timezone.utc)
    result = await db.execute(
        select(GpsLog).where(
            GpsLog.lat.isnot(None), GpsLog.server_time >= trip.started_at, GpsLog.server_time <= end_time
        ).order_by(GpsLog.id)
    )
    logs = result.scalars().all()

    if not logs:
        return {"coordinates": []}

    raw_tuples = [(log.lat, log.lon) for log in logs]
    filtered_waypoints = cluster_gps_points(raw_tuples, radius_km=0.030)
    if len(filtered_waypoints) < 2:
        return {"coordinates": [[w[1], w[0]] for w in filtered_waypoints], "trip_status": trip.status,
                "started_at": trip.started_at.isoformat(), "ended_at": end_time.isoformat() if trip.ended_at else None}

    validated = [filtered_waypoints[0]]
    for i in range(1, len(filtered_waypoints)):
        dist_m = haversine_km(validated[-1][0], validated[-1][1], filtered_waypoints[i][0], filtered_waypoints[i][1]) * 1000
        if dist_m <= 5000:
            validated.append(filtered_waypoints[i])

    coords = await get_osrm_route_geometry_multi(validated) if len(validated) >= 2 else []   # FIXED

    resp = {
        "coordinates": coords,
        "trip_status": trip.status,
        "started_at": trip.started_at.isoformat(),
        "ended_at": trip.ended_at.isoformat() if trip.ended_at else None,
    }
    _TRIP_TRACE_CACHE[trip_id] = (cache_time, resp)
    return resp


@app.get("/health")
async def health():
    return {"status": "ok"}
