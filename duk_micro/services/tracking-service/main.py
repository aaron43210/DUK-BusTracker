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
import json
import httpx
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_
from libs.duk_common.geocoding import get_location_name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from libs.duk_common.redis_client import get_redis, redis_lock
from libs.duk_common.cache.stops_cache import get_all_stops
from libs.duk_common.osrm_client import (
    get_osrm_duration_s,
    get_osrm_route_geometry_multi,
    get_osrm_match_geometry,
    BUS_FACTOR,
    snap_to_road,
    close_client as close_osrm_client,
)
from libs.duk_common.geo import haversine_km, cluster_gps_points
from libs.duk_common.middleware import configure_app
from libs.duk_common.settings import get_settings
from fastapi.responses import JSONResponse

from database_local import get_db, AsyncSessionLocal
from libs.duk_common.models import Trip, BusStop, GpsLog, GpsRealtime, Route, TripTrace, RouteGeometryCache
from sqlalchemy import text
from collections import defaultdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


def _effective_order(stop: dict, direction: str) -> int:
    """Direction-aware order index. Uses morning_order_index for forward trips
    and evening_order_index for reverse trips. Falls back to order_index.

    Uses an explicit None check, not `a or b`: a legitimate order index of 0
    (the origin stop) is falsy and `or` would silently drop it, scrambling the
    stop order (audit D3)."""
    is_forward = (direction or "forward").lower().startswith("f")
    primary = stop.get("morning_order_index") if is_forward else stop.get("evening_order_index")
    return primary if primary is not None else stop.get("order_index", 0)


async def _compute_and_store_route_geometry(direction: str, db: AsyncSession, redis) -> dict:
    """Compute route geometry via OSRM using all stops, store in DB + Redis.
    Called once at startup and whenever stops change.

    A direction can be *pinned* (a hand-reviewed fixed route stored by
    scripts/build_fixed_route.py). When pinned, we never overwrite it with an
    OSRM auto-route — that auto-route through sparse stops is exactly the
    "false path" the pinning was introduced to replace (audit Phase 2).
    """
    if await redis.get(f"cache:route_geometry_pinned:{direction}"):
        existing = await redis.get(f"cache:route_geometry:{direction}")
        if existing:
            logger.info("[GEOMETRY] direction=%s is pinned — skipping auto-recompute", direction)
            return json.loads(existing)
        logger.warning("[GEOMETRY] direction=%s flagged pinned but no cached geometry; computing once", direction)

    stops = await _get_all_stops(db)
    is_forward = direction != "reverse"
    route_groups = defaultdict(list)
    for s in stops:
        route_groups[s["route_id"]].append(s)

    multi_coords = []
    for r_id, s_list in route_groups.items():
        s_list.sort(key=lambda x: _effective_order(x, direction))
        if len(s_list) >= 2:
            waypoints = [(s["lat"], s["lon"]) for s in s_list]
            coords = await get_osrm_route_geometry_multi(waypoints)
            if coords:
                multi_coords.append(coords)

    payload = {"coordinates": multi_coords, "type": "MultiLineString"}

    # Upsert into DB (persistent across Redis restarts)
    await db.execute(
        text("""
            INSERT INTO route_geometry_cache (route_id, direction, coordinates, computed_at)
            VALUES (1, :dir, CAST(:coords AS jsonb), NOW())
            ON CONFLICT (route_id, direction)
            DO UPDATE SET coordinates = EXCLUDED.coordinates, computed_at = NOW()
        """),
        {"dir": direction, "coords": json.dumps(multi_coords)}
    )
    await db.commit()

    # Write to Redis (24h TTL fast path)
    await redis.set(f"cache:route_geometry:{direction}", json.dumps(payload), ex=86400)
    # Bump the version so gps-ingestion's in-process planned-route cache reloads
    # instead of snapping to a stale route after a stop/route edit.
    await redis.incr(f"cache:route_geometry_version:{direction}")
    logger.info("[GEOMETRY] Computed and cached route geometry for direction=%s (%d segments)", direction, len(multi_coords))
    return payload


async def _precompute_geometries_on_startup():
    """Pre-compute both forward + reverse route geometries if not already in Redis."""
    await asyncio.sleep(5)   # let DB pool warm up
    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        for direction in ["forward", "reverse"]:
            if not await redis.get(f"cache:route_geometry:{direction}"):
                logger.info("[STARTUP] Pre-computing route geometry for direction=%s", direction)
                try:
                    await _compute_and_store_route_geometry(direction, db, redis)
                except Exception:
                    logger.exception("[STARTUP] Failed to pre-compute route geometry for direction=%s", direction)


@asynccontextmanager
async def lifespan(app):
    asyncio.create_task(_precompute_geometries_on_startup())
    asyncio.create_task(_publish_public_meta_loop())
    yield
    if _ETA_CLIENT:
        await _ETA_CLIENT.aclose()
    await close_osrm_client()

app = FastAPI(title="tracking-service", docs_url=None, redoc_url=None, lifespan=lifespan)
configure_app(app, allowed_origins=settings.allowed_origins_list, environment=settings.ENVIRONMENT)

# ── Auth: the passenger PWA read endpoints now require a logged-in student JWT ──
# The public website no longer reads live data from this API — it reads from
# Firebase Realtime instead — so every route below is gated behind a valid token.
from libs.duk_common.auth import make_get_current_user_id, decode_token
from libs.duk_common.rate_limit_deps import make_rate_limit_dep

_auth = make_get_current_user_id(
    secret_key=settings.SECRET_KEY,
    redis_getter=get_redis,
    algorithm=settings.ALGORITHM,
)
require_user = _auth.get_user_id
_require_payload = _auth.get_payload


async def require_admin(payload: dict = Depends(_require_payload)) -> str:
    """Admin-only guard: the JWT must carry role == 'admin' (issued by the admin
    login). Prevents ordinary logged-in users from reaching privileged endpoints."""
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return payload.get("sub", "")


def _read_rate_identifier(request):
    """Key the read throttle by the JWT subject (best-effort), so a campus NAT
    doesn't throttle a whole building. Falls back to client IP if the token can't
    be read. Auth itself is enforced separately by require_user."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = decode_token(auth[7:], secret_key=settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        if payload and payload.get("sub"):
            return f"u:{payload['sub']}"
    return request.client.host if request.client else "unknown"


# Generous enough for normal polling; caps a single scraping token.
_read_limit = make_rate_limit_dep(
    "track:read", max_requests=240, window_seconds=60,
    identifier_fn=_read_rate_identifier,
)

# Public website feed: publish a small non-PII trip/power snapshot to RTDB so the
# public site (which no longer hits the JWT-gated API) can render trip status +
# engine state. Live position is published separately by gps-ingestion to `live_bus`.
from libs.duk_common.firebase_app import publish_public_bus_state


async def _compute_trip_progress(db: AsyncSession, direction: str, all_stops: list) -> dict:
    """Visited stop names + first-arrival time (IST) for today's active trip, mirrored
    to Firebase so the public site's timeline shows real visited/arrival data instead
    of the empty placeholder it had (audit W2)."""
    now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    is_evening = normalize_direction(direction) != "forward"
    start_of_today = datetime.combine(now_ist.date(), datetime.min.time())
    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start_of_today).order_by(GpsLog.server_time)
    )
    visited, arrivals = [], {}
    for log in result.scalars().all():
        if log.ist_time is None:
            continue
        h = log.ist_time.hour
        log_is_evening = h > 17 or (h == 17 and log.ist_time.minute >= 30)
        if is_evening != log_is_evening:
            continue
        nearest = find_nearest_stop_math(log.lat, log.lon, all_stops, threshold_km=0.3)
        if nearest and nearest["name"] not in arrivals:
            visited.append(nearest["name"])
            arrivals[nearest["name"]] = log.ist_time.strftime("%I:%M %p")
    return {"visited": visited, "arrivals": arrivals, "updated": now_ist.isoformat()}


async def _publish_public_meta_loop():
    """Every 5s write {trip_status, trip, direction, is_engine_on} to RTDB `bus_meta`.
    Best-effort; a no-op until Firebase creds + FIREBASE_DATABASE_URL are configured."""
    await asyncio.sleep(6)  # let the DB pool warm up
    while True:
        try:
            today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
            async with AsyncSessionLocal() as db:
                trip_res = await db.execute(
                    select(Trip).where(Trip.date == today).order_by(desc(Trip.id)).limit(1)
                )
                trip = trip_res.scalar_one_or_none()
                power_res = await db.execute(
                    select(GpsLog.event)
                    .where(GpsLog.event.in_(["POWER_LOST", "POWER_RESTORE"]))
                    .order_by(desc(GpsLog.server_time)).limit(1)
                )
                power = power_res.scalar_one_or_none()
            await publish_public_bus_state({
                "trip_status": trip.status if trip else "offline",
                # Public site reads `trip` as the direction label (morning/evening);
                # the Trip model has no `trip` column — use `direction`.
                "trip": trip.direction if trip else None,
                "direction": trip.direction if trip else None,
                "is_engine_on": power != "POWER_LOST",
                "updated": datetime.now(timezone.utc).isoformat(),
            }, path="bus_meta")

            # Mirror visited stops + arrival times for the public timeline (W2).
            if trip and trip.direction in ("forward", "reverse") and \
               trip.status in ("active", "on_trip", "late", "completed"):
                async with AsyncSessionLocal() as db2:
                    all_stops = await _get_all_stops(db2)
                    progress = await _compute_trip_progress(db2, trip.direction, all_stops)
                await publish_public_bus_state(progress, path="trip_progress")
        except Exception:
            logger.exception("[PUBLIC_META] publish loop error")
        await asyncio.sleep(5)

ETA_ML_URL = os.environ.get("ETA_ML_URL", "http://eta-ml-service:8003")

from libs.duk_common.constants import normalize_direction


#  Pooled httpx client for eta-ml-service calls (was: new client per request) 
_ETA_CLIENT: Optional[httpx.AsyncClient] = None



def get_dynamic_schedule(all_stops: list) -> dict:
    morning_time_mins = 450
    evening_time_mins = 1054
    for s in all_stops:
        if s.get("is_morning_origin") and s.get("morning_time"):
            try:
                t = datetime.strptime(s["morning_time"], "%I:%M %p")
                morning_time_mins = t.hour * 60 + t.minute
            except Exception:
                try:
                    t = datetime.strptime(s["morning_time"], "%H:%M")
                    morning_time_mins = t.hour * 60 + t.minute
                except Exception:
                    pass
        if s.get("is_evening_origin") and s.get("evening_time"):
            try:
                t = datetime.strptime(s["evening_time"], "%I:%M %p")
                evening_time_mins = t.hour * 60 + t.minute
            except Exception:
                try:
                    t = datetime.strptime(s["evening_time"], "%H:%M")
                    evening_time_mins = t.hour * 60 + t.minute
                except Exception:
                    pass
    return {
        "morning_depart_mins": morning_time_mins,
        "morning_wait_start": morning_time_mins - 30,
        "morning_start_mins": morning_time_mins - 30,
        "morning_end_mins": morning_time_mins + 120,
        "evening_depart_mins": evening_time_mins,
        "evening_wait_start": evening_time_mins - 30,
        "evening_start_mins": evening_time_mins - 30,
        "evening_end_mins": evening_time_mins + 120,
    }


def _get_eta_client() -> httpx.AsyncClient:
    global _ETA_CLIENT
    if _ETA_CLIENT is None:
        # eta-ml-service gates every /ml/* endpoint on this shared internal
        # token. Send it on every call so predict/metrics are not publicly
        # callable. Must match ML_INTERNAL_TOKEN in the eta-ml-service env.
        _ml_token = os.environ.get("ML_INTERNAL_TOKEN", "")
        _headers = {"X-Internal-Token": _ml_token} if _ml_token else {}
        _ETA_CLIENT = httpx.AsyncClient(
            base_url=ETA_ML_URL,
            timeout=httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
            headers=_headers,
        )
    return _ETA_CLIENT



#  OSRM helpers (now delegate to the shared pooled client) 
async def _get_route_geometry_multi(waypoints: list[tuple[float, float]]) -> list:
    return await get_osrm_route_geometry_multi(waypoints)


async def get_osrm_segment_geometry(lat1, lon1, lat2, lon2):
    return await get_osrm_route_geometry_multi([(lat1, lon1), (lat2, lon2)])


def to_ist(utc_dt: datetime) -> datetime:
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(ZoneInfo("Asia/Kolkata"))


def find_nearest_stop_math(lat, lon, stops, threshold_km=0.5):
    """
    Finds the nearest stop to a given lat/lon within threshold_km radius.
    Default radius is 0.5km, which is appropriate for live tracking.
    """
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
    is_forward = normalize_direction(direction) == "forward"
    route_stops.sort(key=lambda x: _effective_order(x, direction))
    visited_set = set(visited or [])
    return [s for s in route_stops if s["id"] not in visited_set]


async def get_active_trip(db: AsyncSession, now_ist: datetime, all_stops: list = None) -> Optional[Trip]:
    if all_stops is None:
        all_stops = await _get_all_stops(db)
    sched = get_dynamic_schedule(all_stops)
    today = now_ist.date()
    time_mins = now_ist.hour * 60 + now_ist.minute
    if sched["morning_start_mins"] <= time_mins <= sched["morning_end_mins"]:
        direction = "forward"
    elif sched["evening_start_mins"] <= time_mins <= sched["evening_end_mins"]:
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
        ).order_by(desc(Trip.id)).limit(1)
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
            "elapsed_minutes": elapsed_minutes,
        }
        resp = await asyncio.wait_for(
            _get_eta_client().post("/api/v1/ml/predict", json=payload),
            timeout=0.5
        )
        if resp.status_code == 200:
            data = resp.json()
            arr_time = to_ist(datetime.now(timezone.utc)) + timedelta(minutes=data.get("eta_minutes", 0))
            return {
                "eta_minutes": data.get("eta_minutes", 0),
                "arrival_time": arr_time.strftime("%-I:%M %p"),
                "source": "ml",
            }
    except (Exception, asyncio.TimeoutError) as e:
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
    norm = normalize_direction(direction)
    if norm == "forward":
        return "Morning"
    elif norm == "reverse":
        return "Evening"
    elif norm == "unscheduled":
        return "Unscheduled"
    return "Not in Service"


def _auto_late_minutes(direction: str, time_mins: int, sched: dict) -> int:
    """Calculates estimated delay when live GPS is unavailable."""
    depart_mins = sched["morning_depart_mins"] if normalize_direction(direction) == "forward" else sched["evening_depart_mins"]
    delay = time_mins - depart_mins
    return delay if delay > 0 else 0


# --- PORTED ENDPOINTS ---

@app.get("/api/v1/latest")
async def get_latest(db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    # PRIMARY SOURCE: gps_realtime — the hardware GPS device UPSERTs here directly.
    # Order by gps_time (actual hardware GPS clock) which changes on every UPSERT,
    # unlike created_at which is frozen at the original INSERT date (Aug 19).
    result = await db.execute(
        select(
            GpsRealtime.lat, GpsRealtime.lon, GpsRealtime.speed,
            GpsRealtime.server_time, GpsRealtime.ist_time, GpsRealtime.gps_time
        )
        .where(GpsRealtime.lat.isnot(None))
        # Order by ist_time (updated on every UPSERT by GPS bridge) so the
        # freshest row wins even when gps_time is NULL.
        .order_by(desc(GpsRealtime.ist_time), desc(GpsRealtime.gps_time)).limit(1)
    )
    latest = result.first()
    use_gps_realtime = latest is not None

    # FALLBACK: if gps_realtime is empty, try today's gps_logs
    if not latest:
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        today_start_utc = now_ist.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        result2 = await db.execute(
            select(GpsLog.lat, GpsLog.lon, GpsLog.speed, GpsLog.server_time, GpsLog.ist_time)
            .where(GpsLog.lat.isnot(None), GpsLog.server_time >= today_start_utc)
            .order_by(desc(GpsLog.server_time)).limit(1)
        )
        latest = result2.first()

    if not latest:
        raise HTTPException(status_code=404, detail="No GPS data today")

    now_utc = datetime.now(timezone.utc)
    # Liveness: prefer gps_time (hardware clock), then ist_time (bridge timestamp),
    # then created_at (server_time). This ensures freshly-ingested rows are marked live.
    ref_time = getattr(latest, 'gps_time', None) if use_gps_realtime else None
    if ref_time is None and use_gps_realtime and latest.ist_time is not None:
        # ist_time is tz-naive IST — convert to UTC for comparison
        ref_time = latest.ist_time.replace(tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(timezone.utc)
    if ref_time is None:
        ref_time = latest.server_time
    if ref_time and ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=timezone.utc)
    is_live = ref_time is not None and (now_utc - ref_time).total_seconds() <= 120

    redis = await get_redis()
    location_name = await get_location_name(latest.lat, latest.lon, redis, wait_timeout=1.5)

    # For the displayed timestamp, prefer ist_time; fall back to ref_time in IST
    display_time = latest.ist_time
    if display_time is None and ref_time is not None:
        display_time = (ref_time + timedelta(hours=5, minutes=30)).replace(tzinfo=None)

    return {
        "lat": latest.lat, "lon": latest.lon, "raw_lat": latest.lat, "raw_lon": latest.lon,
        "speed_kmh": latest.speed,
        "ist_time": display_time.isoformat() if display_time else None,
        "is_live": is_live,
        "location_name": location_name,
    }


@app.get("/api/v1/latest_power")
async def get_latest_power(db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    result = await db.execute(
        select(GpsLog.event, GpsLog.server_time)
        .where(GpsLog.event.in_(["POWER_LOST", "POWER_RESTORE"]))
        .order_by(desc(GpsLog.server_time)).limit(1)
    )
    latest_power = result.first()
    if not latest_power:
        return {"event": "POWER_RESTORE", "server_time": None}
    
    return {
        "event": latest_power.event,
        "server_time": latest_power.server_time.isoformat() if latest_power.server_time else None
    }



@app.get("/api/v1/trip_state")
async def trip_state(db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    # Check gps_realtime FIRST — hardware device UPSERTs here (gps_time changes every ping).
    # Fall back to gps_logs for liveness if gps_realtime is empty.
    rt_res = await db.execute(
        select(GpsRealtime.lat, GpsRealtime.lon, GpsRealtime.gps_time, GpsRealtime.server_time)
        .where(GpsRealtime.lat.isnot(None))
        .order_by(desc(GpsRealtime.gps_time)).limit(1)
    )
    latest_realtime = rt_res.first()

    gps_res = await db.execute(select(GpsLog).where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.server_time)).limit(1))
    latest_gps = gps_res.scalar_one_or_none()

    now_utc = datetime.now(timezone.utc)
    is_gps_alive = False
    gps_dead_minutes = 999
    last_updated_time = None

    if latest_realtime:
        # Use gps_time from gps_realtime — this is the actual hardware GPS clock, changes every ping
        ref_time = latest_realtime.gps_time or latest_realtime.server_time
        if ref_time:
            if ref_time.tzinfo is None:
                ref_time = ref_time.replace(tzinfo=timezone.utc)
            gps_dead_minutes = (now_utc - ref_time).total_seconds() / 60.0
            if gps_dead_minutes <= 5.0:
                is_gps_alive = True
            last_updated_time = ref_time.isoformat()

    # Also check gps_logs as fallback for liveness (for devices using the API)
    if not is_gps_alive and latest_gps and latest_gps.server_time:
        log_time = latest_gps.server_time
        if log_time.tzinfo is None:
            log_time = log_time.replace(tzinfo=timezone.utc)
        log_dead = (now_utc - log_time).total_seconds() / 60.0
        if log_dead < gps_dead_minutes:
            gps_dead_minutes = log_dead
        if log_dead <= 5.0:
            is_gps_alive = True
            last_updated_time = log_time.isoformat()

    now_ist = to_ist(datetime.now(timezone.utc))
    today = now_ist.date()
    time_mins = now_ist.hour * 60 + now_ist.minute
    
    all_stops = await _get_all_stops(db)
    sched = get_dynamic_schedule(all_stops)

    result = await db.execute(select(Trip).where(Trip.date == today).order_by(desc(Trip.id)).limit(5))
    trips = result.scalars().all()

    if trips:
        is_deviated = False
        # Use latest_realtime coords (live) if available, fall back to gps_logs
        live_lat = latest_realtime.lat if latest_realtime else (latest_gps.lat if latest_gps else None)
        live_lon = latest_realtime.lon if latest_realtime else (latest_gps.lon if latest_gps else None)
        if is_gps_alive and live_lat and live_lon:
            nearest = find_nearest_stop_math(live_lat, live_lon, all_stops, threshold_km=10.0)
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

            auto_late = _auto_late_minutes(active.direction, time_mins, sched)
            late_val = active.late_by_minutes if active.late_by_minutes is not None else 0
            if not is_gps_alive:
                late_val = max(late_val, auto_late)
                
            return {
                "trip": display_trip, "status": target_status, "trip_id": active.id,
                "late_by_minutes": late_val, "cancellation_reason": None,
                "next_trip_time": None, "is_deviated": is_deviated,
                "last_updated_time": last_updated_time}

        cancelled = next((t for t in trips if t.status == "cancelled"), None)
        if cancelled:
            return {
                "trip": "Unscheduled" if is_gps_alive else "Not in Service",
                "status": "cancelled", "cancellation_reason": cancelled.cancellation_reason,
                "next_trip_time": None,
                "last_updated_time": last_updated_time}

        current_window = None
        if sched["morning_start_mins"] <= time_mins <= sched["morning_end_mins"]:
            current_window = "morning"
        elif sched["evening_start_mins"] <= time_mins <= sched["evening_end_mins"]:
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
                "last_updated_time": last_updated_time}

        scheduled_trips = [t for t in trips if t.status == "scheduled"]
        if scheduled_trips:
            active_target = None
            for t in scheduled_trips:
                is_m = normalize_direction(t.direction) == "forward"
                if is_m and (sched["morning_start_mins"] <= time_mins <= sched["morning_end_mins"]):
                    active_target = t
                    break
                elif not is_m and (sched["evening_start_mins"] <= time_mins <= sched["evening_end_mins"]):
                    active_target = t
                    break

            if active_target:
                target_trip = active_target
                is_morning = normalize_direction(target_trip.direction) == "forward"
                is_active_window = True
            else:
                scheduled_trips.sort(key=lambda x: 0 if normalize_direction(x.direction) == "forward" else 1)
                if time_mins > sched["morning_end_mins"]:
                    evening_trips = [t for t in scheduled_trips if normalize_direction(t.direction) != "forward"]
                    target_trip = evening_trips[0] if evening_trips else scheduled_trips[0]
                else:
                    target_trip = scheduled_trips[0]
                is_morning = normalize_direction(target_trip.direction) == "forward"
                is_active_window = False

            if is_active_window:
                display_trip = "Unscheduled" if is_deviated else format_trip_name(target_trip.direction)
                status = "connecting"
                if is_gps_alive:
                    status = "active"
                return {
                    "trip": display_trip, "status": status, "trip_id": target_trip.id,
                    "late_by_minutes": None,
                    "cancellation_reason": None, "next_trip_time": None, "is_deviated": is_deviated,
                "last_updated_time": last_updated_time}
            else:
                if is_gps_alive:
                    return {
                        "trip": "Unscheduled", "status": "active", "trip_id": target_trip.id,
                        "late_by_minutes": None, "cancellation_reason": None, "next_trip_time": None,
                "last_updated_time": last_updated_time}
                is_waiting_window = False
                if is_morning and (sched["morning_wait_start"] <= time_mins < sched["morning_start_mins"]):
                    is_waiting_window = True
                elif not is_morning and (sched["evening_wait_start"] <= time_mins < sched["evening_start_mins"]):
                    is_waiting_window = True

                return {
                    "trip": "Not in Service", "status": "waiting" if is_waiting_window else "offline",
                    "trip_id": target_trip.id, "late_by_minutes": None, "cancellation_reason": None,
                    "next_trip_time": "07:30 AM" if is_morning else "05:40 PM",
                "last_updated_time": last_updated_time}

    if today.weekday() >= 5:
        return {"trip": "Unscheduled" if is_gps_alive else "Not in Service",
                "status": "active" if is_gps_alive else "weekend", "next_trip_time": "Mon 07:30 AM", "last_updated_time": last_updated_time}

    if today.weekday() == 4 and time_mins > sched["evening_end_mins"]:
        return {"trip": "Unscheduled" if is_gps_alive else "Not in Service",
                "status": "active" if is_gps_alive else "completed", "next_trip_time": "Mon 07:30 AM", "last_updated_time": last_updated_time}

    if sched["morning_start_mins"] <= time_mins <= sched["morning_end_mins"] or sched["evening_start_mins"] <= time_mins <= sched["evening_end_mins"]:
        dir_name = "Morning" if time_mins <= sched["morning_end_mins"] else "Evening"
        return {"trip": dir_name, "status": "active" if is_gps_alive else "connecting", "next_trip_time": None, "last_updated_time": last_updated_time}

    if is_gps_alive:
        return {"trip": "Unscheduled", "status": "active", "next_trip_time": None, "last_updated_time": last_updated_time}
    if sched["morning_wait_start"] <= time_mins < sched["morning_start_mins"]:
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "07:30 AM", "last_updated_time": last_updated_time}
    elif sched["evening_wait_start"] <= time_mins < sched["evening_start_mins"]:
        return {"trip": "Not in Service", "status": "waiting", "next_trip_time": "05:40 PM", "last_updated_time": last_updated_time}
    else:
        next_trip = "07:30 AM" if time_mins < sched["morning_wait_start"] or time_mins > sched["evening_end_mins"] else "05:40 PM"
        return {"trip": "Not in Service", "status": "offline", "next_trip_time": next_trip, "last_updated_time": last_updated_time}


@app.get("/api/v1/route_history")
async def route_history(trip_id: int = None, db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    """Serves the full GPS trail for a trip from the trip_trace table.
    Because trip_trace is populated by the bridge from trip start, the full
    trail is immediately available — even if the user opens the app mid-trip.
    Results are cached in Redis for 30 seconds to avoid re-running the stop-match
    loop (N traces × M stops) on every request during an active trip.
    """
    if not trip_id:
        return {"visitedStops": {}, "arrivalTimes": {}, "coords": []}

    redis = await get_redis()
    cache_key = f"cache:route_history:{trip_id}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    result = await db.execute(
        select(TripTrace)
        .where(TripTrace.trip_id == trip_id)
        .order_by(TripTrace.gps_time)
    )
    traces = result.scalars().all()

    stops_dicts = await _get_all_stops(db)
    visited_stops: dict = {}
    arrival_times: dict = {}
    coords: list = []

    for trace in traces:
        lat = trace.snapped_lat if trace.snapped_lat is not None else trace.raw_lat
        lon = trace.snapped_lon if trace.snapped_lon is not None else trace.raw_lon
        
        if getattr(trace, "osrm_path", None):
            coords.extend(trace.osrm_path)
        elif lat is not None and lon is not None:
            coords.append([float(lon), float(lat)])
            
        nearest = find_nearest_stop_math(lat, lon, stops_dicts, threshold_km=0.3)
        if nearest:
            name = nearest["name"]
            if name not in arrival_times:
                visited_stops[name] = True
                ts = trace.gps_time
                if ts and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts:
                    ts_ist = ts.astimezone(ZoneInfo("Asia/Kolkata"))
                    arrival_times[name] = ts_ist.strftime("%-I:%M %p")

    payload = {"visitedStops": visited_stops, "arrivalTimes": arrival_times, "coords": coords}
    # Cache for 30 seconds — trail updates every ~7s so this is a good balance
    await redis.set(cache_key, json.dumps(payload), ex=30)
    return payload

@app.get("/api/v1/stops")
async def get_stops(db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    return await _get_all_stops(db)


@app.get("/api/v1/routes")
async def get_routes(db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    redis = await get_redis()
    cached = await redis.get("cache:routes:v2")
    if cached:
        return json.loads(cached)

    # Cross-process cache-stampede guard (was a process-local asyncio.Lock) so a
    # cold cache doesn't trigger the same rebuild on every worker at once.
    async with redis_lock(redis, "lock:routes:v2", ttl_s=15.0, wait_s=10.0):
        cached = await redis.get("cache:routes:v2")
        if cached:
            return json.loads(cached)

        result = await db.execute(select(Route))
        routes = result.scalars().all()
        all_stops = await _get_all_stops(db)
        output = []
        for route in routes:
            route_stops = [s for s in all_stops if s.get("route_id") == route.id]
            output.append({"id": route.id, "name": route.name, "stops": route_stops})

        await redis.set("cache:routes:v2", json.dumps(output))
        return output


@app.get("/api/v1/route_geometry")
async def get_route_geometry_full(
    trip_id: int = Query(None),
    direction: str = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: str = Depends(require_user),
    _limit: None = Depends(_read_limit),
):
    """Serves the pre-computed grey planned route for the map.
    Reads from Redis (fast, 24h TTL) → falls back to DB (persistent) → computes via OSRM (first run).
    Zero per-request OSRM calls during normal operation.
    """
    if trip_id:
        trip_res = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip = trip_res.scalar_one_or_none()
        if trip:
            direction = trip.direction

    db_dir = "reverse" if normalize_direction(direction) == "reverse" else "forward"

    # ① Redis fast path
    redis = await get_redis()
    cache_key = f"cache:route_geometry:{db_dir}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    # ② DB fallback (survives Redis restart)
    result = await db.execute(
        select(RouteGeometryCache)
        .where(RouteGeometryCache.direction == db_dir)
        .order_by(desc(RouteGeometryCache.computed_at))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row:
        payload = {"coordinates": row.coordinates, "type": "MultiLineString"}
        await redis.set(cache_key, json.dumps(payload), ex=86400)
        return payload

    # ③ Compute fresh (startup race or first-ever run)
    return await _compute_and_store_route_geometry(db_dir, db, redis)


@app.post("/api/v1/admin/refresh_route_geometry")
async def refresh_route_geometry(db: AsyncSession = Depends(get_db), _admin: str = Depends(require_admin), _limit: None = Depends(_read_limit)):
    """Admin endpoint: re-compute and cache route geometry for both directions.
    Call this after adding, removing, or moving a bus stop.

    Pinned directions (hand-reviewed fixed routes) are left untouched — unpin
    first (delete cache:route_geometry_pinned:{direction}) to force a recompute.
    """
    redis = await get_redis()
    skipped = []
    for direction in ["forward", "reverse"]:
        if await redis.get(f"cache:route_geometry_pinned:{direction}"):
            skipped.append(direction)
            continue
        await redis.delete(f"cache:route_geometry:{direction}")
        await _compute_and_store_route_geometry(direction, db, redis)
    msg = "Route geometry recomputed for forward and reverse directions"
    if skipped:
        msg = f"Recomputed unpinned directions; left pinned: {', '.join(skipped)}"
    return {"ok": True, "message": msg, "pinned_skipped": skipped}


@app.get("/api/v1/route_segment")
async def get_route_segment(lat1: float = Query(...), lon1: float = Query(...),
                             lat2: float = Query(...), lon2: float = Query(...),
                             _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    coords = await get_osrm_segment_geometry(lat1, lon1, lat2, lon2)
    return {"coordinates": coords}


@app.post("/api/v1/snap_route")
async def snap_route(payload: dict, _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
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
    if not coords:
        coords = [[w[1], w[0]] for w in waypoints]
    return {"coordinates": coords}


async def get_dynamic_visited_stops(db: AsyncSession, redis, direction: str, all_stops: list) -> list:
    cache_key = f"cache:dynamic_visited:{direction}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)
        
    now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    is_evening = normalize_direction(direction) != "forward"
    start_of_today = datetime.combine(now_ist.date(), datetime.min.time())
    
    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start_of_today).order_by(GpsLog.server_time)
    )
    logs = result.scalars().all()
    
    visited_ids = []
    arrival_times = {}
    for log in logs:
        if log.ist_time is None: continue
        entry_hour = log.ist_time.hour
        log_is_evening = entry_hour > 17 or (entry_hour == 17 and log.ist_time.minute >= 30)
        if is_evening == log_is_evening:
            nearest = find_nearest_stop_math(log.lat, log.lon, all_stops, threshold_km=0.3)
            if nearest:
                nid = nearest["id"]
                if nid not in arrival_times:
                    visited_ids.append(nid)
                    arrival_times[nid] = True
                    
    await redis.setex(cache_key, 60, json.dumps(visited_ids))
    return visited_ids


@app.get("/api/v1/eta")
async def get_eta(stop_id: int = Query(...), db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    result = await db.execute(
        select(GpsLog.id, GpsLog.lat, GpsLog.lon, GpsLog.speed)
        .where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.server_time)).limit(5)
    )
    recent_logs = result.all()
    if not recent_logs:
        raise HTTPException(status_code=404, detail="No bus data available")

    latest = recent_logs[0]
    redis = await get_redis()
    eta_cache_key = f"cache:eta:{stop_id}:{latest.id}"
    cached_eta = await redis.get(eta_cache_key)
    if cached_eta:
        return json.loads(cached_eta)

    stop_result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop = stop_result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")

    speeds = [log.speed for log in recent_logs if log.speed is not None]
    speed_avg = sum(speeds) / len(speeds) if speeds else 20.0

    now = datetime.now(timezone.utc)
    now_ist = to_ist(now)
    all_stops = await _get_all_stops(db)
    active_trip = await get_active_trip(db, now_ist, all_stops)
    direction = active_trip.direction if active_trip else ("reverse" if (now_ist.hour * 60 + now_ist.minute >= 1050) else "forward")
    visited = active_trip.visited_stops if active_trip and active_trip.visited_stops else []
    
    if not visited:
        visited = await get_dynamic_visited_stops(db, redis, direction, all_stops)

    ahead = await get_stops_ahead(latest.lat, latest.lon, all_stops, direction, visited,
                                   active_trip.route_id if active_trip else stop.route_id)

    target_in_ahead = next((s for s in ahead if s["id"] == stop_id), None)
    if not target_in_ahead and stop_id in visited:
        response = {"stop_id": stop_id, "stop_name": stop.name, "status": "passed"}
        await redis.setex(eta_cache_key, 60, json.dumps(response))
        return response

    if target_in_ahead and target_in_ahead.get("deviated"):
        response = {"stop_id": stop_id, "stop_name": stop.name, "status": "deviated"}
        await redis.setex(eta_cache_key, 60, json.dumps(response))
        return response

    is_forward = normalize_direction(direction) == "forward"
    target_eff = _effective_order(
        {"morning_order_index": stop.morning_order_index,
         "evening_order_index": stop.evening_order_index,
         "order_index": stop.order_index}, direction)
    if is_forward:
        stops_remaining = len([s for s in ahead if _effective_order(s, direction) <= target_eff])
    else:
        stops_remaining = len([s for s in ahead if _effective_order(s, direction) >= target_eff])
    snapped_bus_lat, snapped_bus_lon = await snap_to_road(latest.lat, latest.lon)

    elapsed_minutes = 0.0
    if active_trip and active_trip.started_at:
        trip_started = active_trip.started_at
        if trip_started.tzinfo is None:
            trip_started = trip_started.replace(tzinfo=timezone.utc)
        elapsed_minutes = max(0.0, (now - trip_started).total_seconds() / 60.0)

    prediction = await predict_eta(
        bus_lat=snapped_bus_lat, bus_lon=snapped_bus_lon, target_stop_lat=stop.lat, target_stop_lon=stop.lon,
        stops_remaining=stops_remaining, hour_of_day=now_ist.hour, day_of_week=now_ist.weekday(),
        # Use the ACTUAL trip direction (not a wall-clock guess) so the model's
        # direction feature matches reality even for early/late trips.
        trip_direction=1 if normalize_direction(direction) == "reverse" else 0, elapsed_minutes=elapsed_minutes, speed_last_3=speed_avg,
    )
    response = {"stop_id": stop_id, "stop_name": stop.name, "bus_lat": snapped_bus_lat, "bus_lon": snapped_bus_lon, **prediction}
    await redis.setex(eta_cache_key, 60, json.dumps(response))
    return response


# Redis cache for /eta/all.
# Key: "eta_all:{bucket_lat}:{bucket_lon}:{trip_id}" where bucket rounds to 3dp (~111 m).
# TTL: 30 seconds — short enough to stay fresh, long enough to absorb burst polling.
_ETA_ALL_TTL = 30


@app.get("/api/v1/eta/all")
async def get_all_etas(db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    result = await db.execute(
        select(GpsLog.id, GpsLog.lat, GpsLog.lon, GpsLog.speed)
        .where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.server_time)).limit(5)
    )
    recent_logs = result.all()
    if not recent_logs:
        return {}

    latest = recent_logs[0]
    speeds = [log.speed for log in recent_logs if log.speed is not None]
    speed_avg = sum(speeds) / len(speeds) if speeds else 20.0

    now = datetime.now(timezone.utc)
    now_ist = to_ist(now)
    all_stops = await _get_all_stops(db)
    active_trip = await get_active_trip(db, now_ist, all_stops)
    direction = active_trip.direction if active_trip else ("reverse" if (now_ist.hour * 60 + now_ist.minute >= 1050) else "forward")
    visited = active_trip.visited_stops if active_trip else []
    trip_id = active_trip.id if active_trip else 0

    # Build cache key from position bucket (~111 m resolution) + trip + direction
    blat = round(latest.lat, 3)
    blon = round(latest.lon, 3)
    cache_key = f"eta_all:{blat}:{blon}:{trip_id}:{direction}"

    # Redis cache
    try:
        redis = await get_redis()
        redis_cached = await redis.get(f"svc:{cache_key}")
        if redis_cached:
            return json.loads(redis_cached)
    except Exception:
        redis = None

    ahead = await get_stops_ahead(latest.lat, latest.lon, all_stops, direction, visited,
                                   active_trip.route_id if active_trip else 1)
    snapped_bus_lat, snapped_bus_lon = await snap_to_road(latest.lat, latest.lon)

    elapsed_minutes = 0.0
    if active_trip and active_trip.started_at:
        trip_started = active_trip.started_at
        if trip_started.tzinfo is None:
            trip_started = trip_started.replace(tzinfo=timezone.utc)
        elapsed_minutes = max(0.0, (now - trip_started).total_seconds() / 60.0)

    tasks = []
    for stop in ahead:
        is_forward = normalize_direction(direction) == "forward"
        target_eff = _effective_order(stop, direction)
        if is_forward:
            stops_remaining = len([s for s in ahead if _effective_order(s, direction) <= target_eff])
        else:
            stops_remaining = len([s for s in ahead if _effective_order(s, direction) >= target_eff])
        tasks.append(predict_eta(
            bus_lat=snapped_bus_lat, bus_lon=snapped_bus_lon, target_stop_lat=stop["lat"], target_stop_lon=stop["lon"],
            stops_remaining=stops_remaining, hour_of_day=now_ist.hour, day_of_week=now_ist.weekday(),
            # Use the ACTUAL trip direction (not a wall-clock guess) so every
            # stop's prediction shares the same, correct direction feature.
            trip_direction=1 if normalize_direction(direction) == "reverse" else 0, elapsed_minutes=elapsed_minutes, speed_last_3=speed_avg,
        ))

    predictions = await asyncio.gather(*tasks, return_exceptions=True)

    etas = {}
    last_eta = -1.0
    for idx, stop in enumerate(ahead):
        pred = predictions[idx]
        if not isinstance(pred, Exception):
            # Enforce monotonic ETAs (each stop must take at least 1 min longer than previous)
            current_eta = max(float(pred["eta_minutes"]), last_eta + 1.0)
            etas[stop["id"]] = current_eta
            last_eta = current_eta

    # Write back to Redis
    try:
        if redis:
            await redis.set(f"svc:{cache_key}", json.dumps(etas), ex=_ETA_ALL_TTL)
    except Exception:
        pass

    return etas


@app.get("/api/v1/history/{date_str}")
async def history_by_date(date_str: str, db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    start = datetime.combine(target_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
    end = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))

    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start, GpsLog.ist_time < end).order_by(GpsLog.server_time)
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
async def get_current_trace(db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    is_evening = now_ist.hour > 17 or (now_ist.hour == 17 and now_ist.minute >= 30)
    start_of_today = datetime.combine(now_ist.date(), datetime.min.time())

    result = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.ist_time >= start_of_today).order_by(GpsLog.server_time)
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

    raw_tuples = [(log.lat, log.lon, int(log.ist_time.timestamp()) if log.ist_time else i*10) for i, log in enumerate(filtered_logs)]
    filtered_waypoints = cluster_gps_points(raw_tuples, radius_km=0.005)
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

    waypoints = [(w[0], w[1]) for w in validated]
    coords = await get_osrm_match_geometry(waypoints)
    if not coords:
        coords = [[w[1], w[0]] for w in validated]
        
    return {"coordinates": coords, "trip_status": "active", "started_at": start_of_today.isoformat(), "ended_at": None}


@app.get("/api/v1/trip_trace/{trip_id}")
async def get_trip_trace(trip_id: int, db: AsyncSession = Depends(get_db), _user: str = Depends(require_user), _limit: None = Depends(_read_limit)):
    now = datetime.now(timezone.utc).timestamp()
    redis = await get_redis()
    trace_cache_key = f"cache:trip_trace:{trip_id}"
    cached_trace = await redis.get(trace_cache_key)
    if cached_trace:
        return json.loads(cached_trace)

    trip_res = await db.execute(select(Trip).where(Trip.id == trip_id))
    trip = trip_res.scalar_one_or_none()
    if not trip or not trip.started_at:
        return {"coordinates": []}

    is_permanent = trip.status in ("completed", "cancelled")

    end_time = trip.ended_at or datetime.now(timezone.utc)
    result = await db.execute(
        select(GpsLog).where(
            GpsLog.lat.isnot(None), GpsLog.server_time >= trip.started_at, GpsLog.server_time <= end_time
        ).order_by(GpsLog.server_time)
    )
    logs = result.scalars().all()

    if not logs:
        return {"coordinates": []}

    raw_tuples = [(log.lat, log.lon) for log in logs]
    filtered_waypoints = cluster_gps_points(raw_tuples, radius_km=0.005)
    if len(filtered_waypoints) < 2:
        return {"coordinates": [[w[1], w[0]] for w in filtered_waypoints], "trip_status": trip.status,
                "started_at": trip.started_at.isoformat(), "ended_at": end_time.isoformat() if trip.ended_at else None}

    validated = [filtered_waypoints[0]]
    for i in range(1, len(filtered_waypoints)):
        dist_m = haversine_km(validated[-1][0], validated[-1][1], filtered_waypoints[i][0], filtered_waypoints[i][1]) * 1000
        if dist_m <= 5000:
            validated.append(filtered_waypoints[i])

    coords = await get_osrm_route_geometry_multi(validated) if len(validated) >= 2 else []   # FIXED
    if not coords:
        coords = [[w[1], w[0]] for w in validated]

    resp = {
        "coordinates": coords,
        "trip_status": trip.status,
        "started_at": trip.started_at.isoformat(),
        "ended_at": trip.ended_at.isoformat() if trip.ended_at else None,
    }
    ttl = 86400 * 7 if is_permanent else 15
    await redis.setex(trace_cache_key, ttl, json.dumps(resp))
    return resp




@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    redis_status = "error"
    try:
        redis = await get_redis()
        if await redis.ping():
            redis_status = "ok"
    except Exception:
        pass

    status_code = 200 if db_status == "ok" and redis_status == "ok" else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if status_code == 200 else "degraded", "db": db_status, "redis": redis_status}
    )
