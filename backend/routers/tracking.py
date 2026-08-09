"""
routers/tracking.py — Public tracking endpoints (latest GPS, trip state, route history, ETA).
"""
import logging
from datetime import datetime, timezone, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from database import get_db
from models.gps import GpsLog
from models.route import BusStop, Route
from models.trip import Trip
from services.geofence import haversine_km, find_nearest_stop_math, get_stops_ahead
from services.osrm_client import get_osrm_route_geometry, get_osrm_segment_geometry
from services.eta_engine import predict_eta
from services.trip_lifecycle import auto_complete_expired_trips
from constants import (
    IST_OFFSET, MORNING_START_MINS, MORNING_END_MINS, 
    EVENING_START_MINS, EVENING_END_MINS, 
    MORNING_WAIT_START, EVENING_WAIT_START
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["tracking"])


# --- Global Memory Caches ---
_STOPS_CACHE = None
_ROUTES_CACHE = None
ETA_CACHE: dict[int, tuple[int, dict]] = {}  # stop_id -> (gps_log_id, eta_payload)

def clear_stops_cache():
    """Wipes the stops cache and routes cache, forcing a DB reload on the next request."""
    global _STOPS_CACHE, _ROUTES_CACHE
    _STOPS_CACHE = None
    _ROUTES_CACHE = None
    logger.info("[CACHE] Stops and routes cache cleared.")


async def _get_all_stops(db: AsyncSession) -> list[dict]:
    """Fetch all stops from DB ordered by route + order_index (with memory caching)."""
    global _STOPS_CACHE
    if _STOPS_CACHE is not None:
        return _STOPS_CACHE

    result = await db.execute(
        select(BusStop).order_by(BusStop.route_id, BusStop.order_index)
    )
    stops = result.scalars().all()
    _STOPS_CACHE = [
        {"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon, "order_index": s.order_index}
        for s in stops
    ]
    return _STOPS_CACHE


@router.get("/latest")
async def get_latest(db: AsyncSession = Depends(get_db)):
    """Latest GPS ping from the bus."""
    await auto_complete_expired_trips(db)
    result = await db.execute(
        select(GpsLog)
        .where(GpsLog.lat.isnot(None))
        .order_by(desc(GpsLog.id))
        .limit(1)
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="No GPS data yet")

    now_utc = datetime.now(timezone.utc)
    log_time = log.server_time
    if log_time.tzinfo is None:
        log_time = log_time.replace(tzinfo=timezone.utc)
    is_live = (now_utc - log_time).total_seconds() <= 60

    return {
        "lat":         log.lat,
        "lon":         log.lon,
        "speed_kmh":   log.speed,
        "server_time": log.ist_time.isoformat() if log.ist_time else log.server_time.isoformat() if log.server_time else None,
        "is_live":     is_live,
    }


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


@router.get("/trip_state")
async def trip_state(db: AsyncSession = Depends(get_db)):
    """
    Returns current trip status based on active Trip records.
    Falls back to time-window logic if no Trip records exist.
    If trip is active but GPS connection is lost (> 60s), returns status 'connecting'.
    """
    await auto_complete_expired_trips(db)
    # Check GPS connectivity (ping within last 60 seconds)
    gps_res = await db.execute(
        select(GpsLog)
        .where(GpsLog.lat.isnot(None))
        .order_by(desc(GpsLog.id))
        .limit(1)
    )
    latest_gps = gps_res.scalar_one_or_none()
    is_gps_alive = False
    if latest_gps and latest_gps.server_time:
        now_utc = datetime.now(timezone.utc)
        log_time = latest_gps.server_time
        if log_time.tzinfo is None:
            log_time = log_time.replace(tzinfo=timezone.utc)
        if (now_utc - log_time).total_seconds() <= 60:
            is_gps_alive = True

    today = date.today()
    result = await db.execute(
        select(Trip)
        .where(Trip.date == today)
        .order_by(desc(Trip.id))
        .limit(5)
    )
    trips = result.scalars().all()

    # Compute IST time once — used throughout the function
    now_ist   = datetime.now(timezone.utc) + IST_OFFSET
    time_mins = now_ist.hour * 60 + now_ist.minute

    if trips:
        active = next((t for t in trips if t.status in ("on_trip", "active", "late")), None)
        if active:
            target_status = active.status if active.status != "on_trip" else "active"
            if not is_gps_alive:
                target_status = "connecting"
            return {
                "trip":           format_trip_name(active.direction),
                "status":         target_status,
                "trip_id":        active.id,
                "late_by_minutes": active.late_by_minutes,
                "cancellation_reason": None,
                "next_trip_time": None,
            }
        cancelled = next((t for t in trips if t.status == "cancelled"), None)
        if cancelled:
            return {
                "trip":                "Unscheduled" if is_gps_alive else "Not in Service",
                "status":              "cancelled",
                "cancellation_reason": cancelled.cancellation_reason,
                "next_trip_time":      None,
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
                    "status": "completed",
                    "trip_id": completed_in_window.id,
                    "late_by_minutes": None,
                    "cancellation_reason": None,
                    "next_trip_time": next_time
                }

        # If there is a scheduled trip today, prioritize it over the fallback logic.
        # This properly supports weekend "Special Service" trips.
        scheduled_trips = [t for t in trips if t.status == "scheduled"]
        if scheduled_trips:
            # Sort to process morning ('forward') before evening ('reverse')
            scheduled_trips.sort(key=lambda x: 0 if x.direction in ("forward", "morning", "Morning") else 1)
            target_trip = scheduled_trips[0]
            
            is_morning = target_trip.direction in ("forward", "morning", "Morning")
            
            is_active_window = False
            if is_morning and (MORNING_START_MINS <= time_mins <= MORNING_END_MINS):
                is_active_window = True
            elif not is_morning and (EVENING_START_MINS <= time_mins <= EVENING_END_MINS):
                is_active_window = True
                
            if is_active_window:
                return {
                    "trip": format_trip_name(target_trip.direction),
                    "status": "active" if is_gps_alive else "connecting",
                    "trip_id": target_trip.id,
                    "late_by_minutes": None,
                    "cancellation_reason": None,
                    "next_trip_time": None,
                }
            else:
                if is_gps_alive:
                    return {
                        "trip": "Unscheduled",
                        "status": "active",
                        "trip_id": target_trip.id,
                        "late_by_minutes": None,
                        "cancellation_reason": None,
                        "next_trip_time": None,
                    }

                is_waiting_window = False
                if is_morning and (MORNING_WAIT_START <= time_mins < MORNING_START_MINS):
                    is_waiting_window = True
                elif not is_morning and (EVENING_WAIT_START <= time_mins < EVENING_START_MINS):
                    is_waiting_window = True
                
                return {
                    "trip": "Not in Service",
                    "status": "waiting" if is_waiting_window else "offline",
                    "trip_id": target_trip.id,
                    "late_by_minutes": None,
                    "cancellation_reason": None,
                    "next_trip_time": "07:30 AM" if is_morning else "05:40 PM",
                }

    # Fallback: time-window logic (used for normal weekdays when no trips are in DB yet)

    # Weekend (Saturday / Sunday) -> No regular service, next trip Monday morning
    if today.weekday() >= 5:
        return {
            "trip": "Unscheduled" if is_gps_alive else "Not in Service",
            "status": "active" if is_gps_alive else "weekend",
            "next_trip_time": "Mon 07:30 AM",
        }

    # Friday after evening commute -> Friday trip completed, next is Monday morning
    if today.weekday() == 4 and time_mins > EVENING_END_MINS:
        return {
            "trip": "Unscheduled" if is_gps_alive else "Not in Service",
            "status": "active" if is_gps_alive else "completed",
            "next_trip_time": "Mon 07:30 AM",
        }

    if MORNING_START_MINS <= time_mins <= MORNING_END_MINS or EVENING_START_MINS <= time_mins <= EVENING_END_MINS:
        dir_name = "Morning" if time_mins <= MORNING_END_MINS else "Evening"
        return {"trip": dir_name, "status": "active" if is_gps_alive else "connecting", "next_trip_time": None}
    
    # If the bus is actively driving outside of scheduled hours, it's an unscheduled active trip
    if is_gps_alive:
        return {"trip": "Unscheduled", "status": "active", "next_trip_time": None}

    if MORNING_WAIT_START <= time_mins < MORNING_START_MINS:
        # 6:00 AM to 7:00 AM (Prep for Morning Trip)
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "07:30 AM"}
    elif EVENING_WAIT_START <= time_mins < EVENING_START_MINS:
        # 4:30 PM to 5:30 PM (Prep for Evening Trip)
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "05:40 PM"}
    else:
        # Truly offline
        next_trip = "07:30 AM" if time_mins < MORNING_WAIT_START or time_mins > EVENING_END_MINS else "05:40 PM"
        return {"trip": "Not in Service", "status": "offline", "next_trip_time": next_trip}


@router.get("/route_history")
async def route_history(db: AsyncSession = Depends(get_db)):
    """Visited stops and arrival times for today's current session."""
    stops_dicts = await _get_all_stops(db)

    # Use ist_time (Supabase-computed) to determine current IST date and session type
    now_ist = datetime.now(timezone.utc) + IST_OFFSET
    is_evening = now_ist.hour > 17 or (now_ist.hour == 17 and now_ist.minute >= 30)
    today_ist = now_ist.date()
    start_of_today = datetime.combine(today_ist, datetime.min.time())

    result = await db.execute(
        select(GpsLog)
        .where(
            GpsLog.lat.isnot(None),
            GpsLog.ist_time >= start_of_today,
        )
        .order_by(GpsLog.id)
    )
    logs = result.scalars().all()

    visited_stops: dict[str, bool] = {}
    arrival_times: dict[str, str]  = {}

    for log in logs:
        # ist_time is directly stored as IST by Supabase — no conversion needed
        log_ist = log.ist_time
        if log_ist is None:
            continue

        entry_hour = log_ist.hour
        log_is_evening = entry_hour > 17 or (entry_hour == 17 and log_ist.minute >= 30)
        if is_evening != log_is_evening:
            continue

        nearest = find_nearest_stop_math(log.lat, log.lon, stops_dicts, threshold_km=0.3)
        if nearest:
            name = nearest["name"]
            if name not in visited_stops:
                visited_stops[name] = True
                arrival_times[name] = log_ist.strftime("%I:%M %p")

    return {"visitedStops": visited_stops, "arrivalTimes": arrival_times}


@router.get("/stops")
async def get_stops(db: AsyncSession = Depends(get_db)):
    """Return all bus stops (for mobile route rendering and admin panel)."""
    return await _get_all_stops(db)


@router.get("/routes")
async def get_routes(db: AsyncSession = Depends(get_db)):
    """Return all routes with their stops."""
    global _ROUTES_CACHE
    if _ROUTES_CACHE is not None:
        return _ROUTES_CACHE

    result = await db.execute(select(Route))
    routes = result.scalars().all()
    output = []
    for route in routes:
        stops = await _get_all_stops(db)  # filter by route_id in production
        output.append({"id": route.id, "name": route.name, "stops": stops})
        
    _ROUTES_CACHE = output
    return _ROUTES_CACHE


@router.get("/route_geometry")
async def get_route_geometry(db: AsyncSession = Depends(get_db)):
    """Return turn-by-turn road polyline coordinates calculated by OSRM for the standard route."""
    stops = await _get_all_stops(db)
    if not stops:
        return {"coordinates": []}
    waypoints = [(s["lat"], s["lon"]) for s in stops]
    coords = await get_osrm_route_geometry(waypoints)
    return {"coordinates": coords}


@router.get("/route_segment")
async def get_route_segment(
    lat1: float = Query(..., description="Start latitude"),
    lon1: float = Query(..., description="Start longitude"),
    lat2: float = Query(..., description="End latitude"),
    lon2: float = Query(..., description="End longitude"),
):
    """Return road-following coordinates between two points from OSRM."""
    coords = await get_osrm_segment_geometry(lat1, lon1, lat2, lon2)
    return {"coordinates": coords}


@router.post("/snap_route")
async def snap_route(payload: dict):
    """Snap an arbitrary list of [[lon, lat], ...] or [{lat, lon}, ...] points to roads using OSRM."""
    raw_pts = payload.get("points", [])
    if not raw_pts:
        return {"coordinates": []}

    waypoints = []
    for p in raw_pts:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            waypoints.append((float(p[1]), float(p[0])) if abs(float(p[0])) > 40 else (float(p[0]), float(p[1])))
        elif isinstance(p, dict) and "lat" in p and "lon" in p:
            waypoints.append((float(p["lat"]), float(p["lon"])))

    from services.osrm_client import get_osrm_match_geometry
    coords = await get_osrm_match_geometry(waypoints)
    return {"coordinates": coords}




@router.get("/eta")
async def get_eta(
    stop_id: int = Query(..., description="Target bus stop ID"),
    db:      AsyncSession = Depends(get_db),
):
    from services.trip_lifecycle import get_active_trip, to_ist
    """LightGBM ETA prediction to the target stop from current bus position."""
    # Get latest bus position
    result = await db.execute(
        select(GpsLog)
        .where(GpsLog.lat.isnot(None))
        .order_by(desc(GpsLog.id))
        .limit(5)
    )
    recent_logs = result.scalars().all()
    if not recent_logs:
        raise HTTPException(status_code=404, detail="No bus data available")

    latest = recent_logs[0]

    # ── Check GPS-Synced ETA Cache ──
    if stop_id in ETA_CACHE:
        cached_log_id, cached_response = ETA_CACHE[stop_id]
        if cached_log_id == latest.id:
            return cached_response

    # Get target stop
    stop_result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop        = stop_result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")

    # Speed average over last 3 pings
    speeds = [log.speed for log in recent_logs if log.speed is not None]
    speed_avg = sum(speeds) / len(speeds) if speeds else 20.0

    now = datetime.now(timezone.utc)
    active_trip = await get_active_trip(db, to_ist(now))
    direction = active_trip.direction if active_trip else ("reverse" if to_ist(now).hour >= 17 else "forward")
    visited = active_trip.visited_stops if active_trip else []

    # Stops remaining (approximate)
    all_stops = await _get_all_stops(db)
    ahead     = await get_stops_ahead(latest.lat, latest.lon, all_stops, direction, visited)
    
    # Check if this stop was hard-skipped or soft-skipped (deviated)
    target_in_ahead = next((s for s in ahead if s["id"] == stop_id), None)
    
    if not target_in_ahead and stop_id in visited:
        # It was literally visited
        response = {
            "stop_id":   stop_id,
            "stop_name": stop.name,
            "status":    "passed",
        }
        ETA_CACHE[stop_id] = (latest.id, response)
        return response
        
    if target_in_ahead and target_in_ahead.get("deviated"):
        response = {
            "stop_id":   stop_id,
            "stop_name": stop.name,
            "status":    "deviated",
        }
        ETA_CACHE[stop_id] = (latest.id, response)
        return response

    stops_remaining = len([s for s in ahead if s["id"] == stop_id or s["order_index"] <= stop.order_index])

    now = datetime.now()
    prediction = await predict_eta(
        bus_lat=latest.lat,
        bus_lon=latest.lon,
        target_stop_lat=stop.lat,
        target_stop_lon=stop.lon,
        stops_remaining=stops_remaining,
        hour_of_day=now.hour,
        day_of_week=now.weekday(),
        trip_direction=1 if now.hour >= 17 else 0,
        elapsed_minutes=0,   # would need trip start time
        speed_last_3=speed_avg,
    )

    response = {
        "stop_id":     stop_id,
        "stop_name":   stop.name,
        "bus_lat":     latest.lat,
        "bus_lon":     latest.lon,
        **prediction,
    }
    
    # Save to cache
    ETA_CACHE[stop_id] = (latest.id, response)
    
    return response


@router.get("/history/{date_str}")
async def history_by_date(date_str: str, db: AsyncSession = Depends(get_db)):
    """GPS route history for a specific date (YYYY-MM-DD)."""
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    # Filter by ist_time — Supabase-computed IST column, so dates are always correct
    start = datetime.combine(target_date, datetime.min.time())
    end   = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    result = await db.execute(
        select(GpsLog)
        .where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start, GpsLog.ist_time < end)
        .order_by(GpsLog.id)
    )
    logs = result.scalars().all()

    route_points = []
    last_lat, last_lon = None, None
    for log in logs:
        # ist_time is directly stored as IST by Supabase — no conversion needed
        log_ist = log.ist_time
        if log_ist is None:
            continue
        hour = log_ist.hour

        # Filter out trips outside 6 AM to 9 PM IST
        if hour < 6 or hour >= 21:
            continue

        if last_lat is not None:
            dist = haversine_km(last_lat, last_lon, log.lat, log.lon)
            if dist < 0.05:  # < 50 m movement — skip (stationary drift)
                continue
            if dist > 2.0:   # > 2 km movement in one ping — teleport / new trip, reset
                route_points = []

        route_points.append({
            "lat":  log.lat,
            "lon":  log.lon,
            "time": log_ist.isoformat(),
        })
        last_lat, last_lon = log.lat, log.lon

    return route_points


@router.get("/trip_trace/{trip_id}")
async def get_trip_trace(trip_id: int, db: AsyncSession = Depends(get_db)):
    """Return historical OSRM-snapped trail for the given trip."""
    trip_res = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = trip_res.scalar_one_or_none()
    if not trip or not trip.started_at:
        return {"coordinates": []}
    end_time = trip.ended_at or datetime.now(timezone.utc)
    result = await db.execute(
        select(GpsLog)
        .where(GpsLog.lat.isnot(None), GpsLog.server_time >= trip.started_at, GpsLog.server_time <= end_time)
        .order_by(GpsLog.id)
    )
    logs = result.scalars().all()
    
    if not logs:
        return {"coordinates": []}
        
    filtered_waypoints = []
    last_lat, last_lon = None, None
    for log in logs:
        lat, lon = log.lat, log.lon
        if last_lat is None:
            filtered_waypoints.append((lat, lon))
            last_lat, last_lon = lat, lon
        else:
            # Downsample: only include points > 10m apart to avoid OSRM bloat
            if haversine_km(last_lat, last_lon, lat, lon) > 0.01:
                filtered_waypoints.append((lat, lon))
                last_lat, last_lon = lat, lon

    if len(filtered_waypoints) < 2:
        return {"coordinates": [[w[1], w[0]] for w in filtered_waypoints]}
        
    from services.osrm_client import get_osrm_route_geometry
    coords = await get_osrm_route_geometry(filtered_waypoints)
    return {"coordinates": coords}
