# services/gps-ingestion/main.py.   developed by - AARON R
"""
GPS Ingestion Service — Receives GPS data from the bus hardware,
processes it through the GPS pipeline, stores it, and publishes
realtime updates.

GPS processing flow:
  Hardware GPS
       ↓
  1. VALIDATE   — Parse and validate GPS coordinates and payload data
       ↓
  2. FILTER     — Remove GPS jitter using Redis-based filter state
       ↓
  3. SNAP       — Snap the GPS position to the nearest road using OSRM
       ↓
  4. CLUSTER    — Determine whether the bus has moved enough to broadcast
       ↓
  5. PERSIST    — Store processed GPS data in PostgreSQL
       ↓
  6. PUBLISH    — Publish bus position to Redis for realtime clients

GPS entry points:
  WebSocket /api/v1/ws/device — Authenticated hardware GPS connection
  HTTP POST /api/v1/gps        — HTTP GPS ingestion endpoint

Background polling:
  live_gps_polling_loop() checks for GPS records inserted directly into
  PostgreSQL with source IS NULL, processes them through the same pipeline,
  and marks processed records with source="pipeline" to prevent
  duplicate processing.
"""
import re
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, update, text

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from libs.duk_common.redis_client import get_redis, device_state_lock
from libs.duk_common.events import EventBus, GpsSnappedEvent
from libs.duk_common.state.gps_filter_redis import RedisGpsFilterState, apply_gps_filter_stateless
from libs.duk_common.state.snap_redis import RedisSnapState, apply_snap_stateless
from libs.duk_common.state.cluster_redis import RedisClusterState, apply_live_cluster_stateless
from libs.duk_common.state.route_state_redis import RedisRouteState
from libs.duk_common.state.route_snap import build_route_index, apply_route_snap_stateless, route_slice, snap_to_route
from libs.duk_common.osrm_client import snap_to_road, get_osrm_route_geometry, close_client as close_osrm_client
from libs.duk_common.security import constant_time_compare
from libs.duk_common.rate_limit_deps import make_rate_limit_dep
from libs.duk_common.geocoding import get_location_name
from libs.duk_common.partition_manager import ensure_gps_logs_partitions

# Public live feed for the website (RTDB). Self-guards when Firebase is unset.
from libs.duk_common.firebase_app import publish_public_bus_state

from database_local import get_db, AsyncSessionLocal # imported from database_local.py in gps-ingestion service
from libs.duk_common.models import GpsLog, GpsRealtime, Trip, TripTrace
from libs.duk_common.settings import get_settings
from libs.duk_common.geo import haversine_m as _haversine_m
from sqlalchemy import delete as sa_delete
import json as _json

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

GPS_API_KEY = os.environ.get("GPS_API_KEY", "")
if not GPS_API_KEY:
    raise RuntimeError("GPS_API_KEY environment variable must be set")
DEVICE_ID = os.environ.get("DEVICE_ID", "bus-1")

gps_http_limit = make_rate_limit_dep("gps:http", max_requests=60, window_seconds=60)


async def _get_active_trip_id(redis) -> int | None:
    """
    Look up the current active trip_id so GPS rows can be tagged for fast querying.

    Resolution order (audit Phase 4):
      1. Any trip actually running today (on_trip/active/late) — authoritative,
         regardless of the clock, so a delayed trip keeps its id/direction.
      2. Otherwise the hour window (morning→forward, evening→reverse) picks the
         scheduled trip. Direction-aware cache keys stop morning/evening cross-
         contamination; returns None when nothing is running or scheduled now.
    """
    try:
        from zoneinfo import ZoneInfo
        now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
        today = now_ist.date()
        hour = now_ist.hour

        # 1) An actually-running trip is authoritative, regardless of the clock.
        #    A morning trip delayed past 11:00 must keep its trip_id/direction
        #    instead of dropping to None and losing route pinning mid-trip
        #    (audit Phase 4 / D1 / D2). Only when nothing is running do we fall
        #    back to the hour window to find the scheduled trip.
        cached_running = await redis.get("cache:active_trip_id:running")
        if cached_running:
            return int(cached_running)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Trip.id).where(
                    Trip.date == today,
                    Trip.status.in_(["on_trip", "active", "late"]),
                ).order_by(Trip.id.desc()).limit(1)
            )
            running_id = result.scalars().first()
        if running_id:
            await redis.set("cache:active_trip_id:running", str(running_id), ex=60)
            return running_id

        # 2) Nothing running — use the current hour window to find the scheduled
        #    trip about to start. Direction-aware cache keys prevent the morning
        #    trip_id from being served during the evening window (and vice versa).
        if 6 <= hour < 11:
            direction = "forward"
            cache_key = "cache:active_trip_id:morning"
        elif hour >= 17:
            direction = "reverse"
            cache_key = "cache:active_trip_id:evening"
        else:
            # Outside trip windows and nothing running — clear stale cache.
            await redis.delete("cache:active_trip_id")
            return None

        # Check direction-specific cache (avoids morning/evening cross-contamination)
        cached = await redis.get(cache_key)
        if cached:
            return int(cached)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Trip.id).where(
                    Trip.date == today,
                    Trip.direction == direction,
                    Trip.status.in_(["scheduled", "on_trip", "active", "late"]),
                ).order_by(Trip.id.asc()).limit(1)
            )
            trip_id = result.scalars().first()
            if trip_id:
                # Cache for 60 seconds under direction-specific key
                await redis.set(cache_key, str(trip_id), ex=60)
            return trip_id
    except Exception as e:
        logger.warning("[GPS-INGEST] Could not resolve active trip_id: %s", e)
        return None


# ── Smart snap helpers ────────────────────────────────────────────────────────
# Per-process cache: direction → list of [lon, lat] planned route coords.
# Populated from Redis on first GPS ping; reloaded when the Redis version bumps.
_planned_route_cache: dict[str, list] = {}
# direction → last-seen route-geometry version, so an admin route/stop edit
# (which bumps cache:route_geometry_version) forces a reload instead of snapping
# to a stale in-process route.
_planned_route_versions: dict[str, str] = {}

# How far the bus must deviate from the planned route before we call OSRM.
SNAP_OSRM_THRESHOLD_M = 50.0

# Linear-reference index (cumulative distance along the planned route), built
# once per direction from the planned polyline and rebuilt when the route edits.
_route_index_cache: dict[str, dict] = {}
_route_index_versions: dict[str, str] = {}


async def _load_route_index(direction: str, redis) -> dict:
    """Return a route_snap index for a direction, cached and version-aware."""
    if not direction:
        return {"pts": [], "cum": [], "total": 0.0}
    version = await redis.get(f"cache:route_geometry_version:{direction}")
    if isinstance(version, bytes):
        version = version.decode()
    if direction in _route_index_cache and _route_index_versions.get(direction) == version:
        return _route_index_cache[direction]
    planned = await _load_planned_route(direction, redis)
    index = build_route_index(planned)
    _route_index_cache[direction] = index
    _route_index_versions[direction] = version
    logger.info("[ROUTE-SNAP] Built index for direction=%s (%d vertices, %.0f m, v=%s)",
                direction, len(index.get("pts", [])), index.get("total", 0.0), version)
    return index


async def _route_snap_point(
    redis, f_lat: float, f_lon: float, speed_kmh, direction: str | None,
    trip_id: int | None = None,
) -> tuple[float, float, float | None, bool, float | None]:
    """
    Route-constrained snap for one filtered fix.

    Returns ``(s_lat, s_lon, route_s, off_route, bearing)``. While the bus is on
    its planned corridor this is pure math (no OSRM) and ``route_s`` tracks 1-D
    progress forward-only. On sustained deviation it falls back to OSRM
    ``snap_to_road`` and keeps the legacy bearing-lock state for the trail step.
    """
    index = await _load_route_index(direction, redis) if direction else {"pts": []}

    route_mgr = RedisRouteState(redis, DEVICE_ID)
    route_state = await route_mgr.load()
    # Progress `s` is only meaningful for one trip on one planned route. The key
    # is per device with a TTL refreshed on every ping, so without this the
    # morning trip's final `s` carried into the evening (reverse) route.
    route_key = f"{trip_id}:{direction}"
    if route_state.get("key") != route_key:
        route_state = {"key": route_key}
    s_lat, s_lon, off_route, new_route_state = apply_route_snap_stateless(
        f_lat, f_lon, speed_kmh, route_state, index
    )
    await route_mgr.save(new_route_state)
    route_s = new_route_state.get("s")

    if off_route:
        # Off the known route (or no route loaded) — ask OSRM for the road position.
        snap_mgr = RedisSnapState(redis, DEVICE_ID)
        snap_state = await snap_mgr.load()
        bearing = float(snap_state["bearing"]) if snap_state.get("bearing") is not None else None
        s_lat, s_lon = await snap_to_road(f_lat, f_lon, bearing=bearing)
        await snap_mgr.save(apply_snap_stateless(f_lat, f_lon, s_lat, s_lon, snap_state))
        return s_lat, s_lon, route_s, True, bearing

    return s_lat, s_lon, route_s, False, None


async def _build_trace_path(
    redis, direction: str | None,
    last_lat: float, last_lon: float, cur_lat: float, cur_lon: float,
    off_route: bool, bearing: float | None,
    route_s: float | None = None,
) -> list | None:
    """
    Geometry for the trail leg from the last trace point to the current point.
    Prefers a zero-OSRM slice of the planned route (via linear reference, so it
    cannot produce the giant wrong slices nearest-vertex slicing did); falls back
    to OSRM only when the bus is genuinely off-route.
    """
    if not off_route and direction:
        index = await _load_route_index(direction, redis)
        if index.get("pts"):
            # Anchor both endpoint snaps to the tracked progress s. Snapping each
            # endpoint with s_last=None does an unconstrained global search that,
            # after a GPS gap, can re-acquire onto the opposite (parallel) arm of
            # the out-and-back route and emit a giant wrong slice (audit S2).
            b = snap_to_route(cur_lat, cur_lon, index, route_s)
            s_cur = b[2] if b else route_s
            if s_cur is not None:
                # the previous trace point sits just behind cur — generous back
                # window, tiny forward window keeps it on the same arm.
                a = snap_to_route(last_lat, last_lon, index, s_cur,
                                  max_back_m=600.0, max_fwd_m=50.0)
            else:
                a = snap_to_route(last_lat, last_lon, index, None)
            if a and b:
                seg = route_slice(index, a[2], b[2])
                if len(seg) >= 2:
                    return seg
    route_pts = await get_osrm_route_geometry(last_lat, last_lon, cur_lat, cur_lon, bearing=bearing)
    if route_pts:
        return [[lon, lat] for lat, lon in route_pts]
    return None


async def _load_planned_route(direction: str, redis) -> list:
    """Load the pre-computed grey route from Redis into local memory.
    Cached per direction, but reloaded when the Redis version key changes."""
    global _planned_route_cache, _planned_route_versions
    version = await redis.get(f"cache:route_geometry_version:{direction}")
    if isinstance(version, bytes):
        version = version.decode()
    if direction in _planned_route_cache and _planned_route_versions.get(direction) == version:
        return _planned_route_cache[direction]
    raw = await redis.get(f"cache:route_geometry:{direction}")
    if raw:
        data = _json.loads(raw)
        coords = data.get("coordinates", [])
        # coordinates may be a MultiLineString (list of [[lon,lat],...] segments).
        # Concatenating disjoint segments into one polyline injects a phantom
        # straight connector between them that corrupts the linear-reference
        # progress `s` (audit D4). Use the single longest segment instead — the
        # pinned fixed route is one clean line, so this is lossless for it.
        if coords and isinstance(coords[0][0], list):
            if len(coords) == 1:
                coords = coords[0]
            else:
                longest = max(coords, key=len)
                logger.warning(
                    "[SMART-SNAP] direction=%s has %d route segments; using the longest "
                    "(%d pts) to avoid a phantom connector in the snap index.",
                    direction, len(coords), len(longest),
                )
                coords = longest
        _planned_route_cache[direction] = coords
        _planned_route_versions[direction] = version
        logger.info("[SMART-SNAP] Loaded %d planned-route points for direction=%s (v=%s)", len(coords), direction, version)
        return coords
    return []


def _math_snap(lat: float, lon: float, planned: list) -> tuple[float, float, float]:
    """Find nearest point on the planned route using Haversine math only.
    Returns (snapped_lat, snapped_lon, distance_m).
    """
    best_dist = float("inf")
    best_lat, best_lon = lat, lon
    for p_lon, p_lat in planned:
        d = _haversine_m(lat, lon, p_lat, p_lon)
        if d < best_dist:
            best_dist = d
            best_lat, best_lon = p_lat, p_lon
    return best_lat, best_lon, best_dist


def _nearest_route_index(lat: float, lon: float, planned: list) -> tuple[int, float]:
    """Return (index, distance_m) of the nearest planned-route vertex to (lat, lon)."""
    best_idx, best_dist = -1, float("inf")
    for i, (p_lon, p_lat) in enumerate(planned):
        d = _haversine_m(lat, lon, p_lat, p_lon)
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx, best_dist


def _planned_trace_slice(
    last_lat: float, last_lon: float, cur_lat: float, cur_lon: float, planned: list
) -> list | None:
    """
    Build the trail leg between the last trace point and the current point by
    slicing the pre-computed planned-route polyline between their two nearest
    vertices. This follows the exact road shape the route was built on with ZERO
    OSRM calls and no per-ping drift — the source of trails cutting through small
    roads. Returns [[lon, lat], ...] in travel order, or None if it can't build a
    usable (≥2 point) slice (caller then falls back to OSRM).
    """
    if not planned or len(planned) < 2:
        return None
    i_from, d_from = _nearest_route_index(last_lat, last_lon, planned)
    i_to, d_to = _nearest_route_index(cur_lat, cur_lon, planned)
    # Both endpoints must actually sit on the route, else the slice is meaningless.
    if d_from > SNAP_OSRM_THRESHOLD_M or d_to > SNAP_OSRM_THRESHOLD_M:
        return None
    if i_from == i_to:
        return None
    lo, hi = (i_from, i_to) if i_from < i_to else (i_to, i_from)
    slice_pts = planned[lo : hi + 1]
    if len(slice_pts) < 2:
        return None
    # planned coords are [lon, lat]; preserve travel direction.
    return [list(p) for p in (slice_pts if i_from < i_to else list(reversed(slice_pts)))]


async def _get_trip_direction(trip_id: int | None, redis) -> str | None:
    """Return the direction ('forward'|'reverse') of a trip, using Redis cache."""
    if not trip_id:
        return None
    cache_key = f"cache:trip_direction:{trip_id}"
    cached = await redis.get(cache_key)
    if cached:
        return cached.decode() if isinstance(cached, bytes) else cached
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Trip.direction).where(Trip.id == trip_id))
        direction = result.scalar_one_or_none()
    if direction:
        await redis.set(cache_key, direction, ex=3600)
    return direction


#  Direct-to-DB Background Poller (only picks up UN-pipelined rows) 
async def live_gps_polling_loop():
    '''    Polls unprocessed GPS records, runs them through the GPS pipeline,

    broadcasts updates, and persists the processed data.'''

    logger.info("[POLLER] Starting direct-to-db polling loop...")
    redis = await get_redis()
    bus = EventBus(redis)
    last_seen_id = None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GpsLog.id).order_by(desc(GpsLog.id)).limit(1))
        row = result.scalars().first()
        if row:
            last_seen_id = row
    logger.info("[POLLER] Started polling from ID %s", last_seen_id)

    while True:
        try:
            await asyncio.sleep(1.5)
            async with AsyncSessionLocal() as db:
                query = (
                    select(GpsLog)
                    .where(GpsLog.lat.isnot(None), GpsLog.source.is_(None))  # <-- guard: skip pipeline rows
                    .order_by(GpsLog.id)
                    .limit(100)
                )
                if last_seen_id is not None:
                    query = query.where(GpsLog.id > last_seen_id)
                result = await db.execute(query)
                new_logs = result.scalars().all()

            if not new_logs:
                continue

            processed: dict[int, tuple[float, float]] = {}
            for log in new_logs:
                lat, lon, speed_kmh = log.lat, log.lon, log.speed
                server_now = log.server_time or datetime.now(timezone.utc)
                if server_now.tzinfo is None:
                    server_now = server_now.replace(tzinfo=timezone.utc)

                # Serialize this device's filter→snap→cluster state so the
                # polling loop, realtime bridge, and device-push path can't
                # interleave a load-modify-save and lose each other's updates.
                async with device_state_lock(redis, DEVICE_ID):
                    filter_mgr = RedisGpsFilterState(redis, DEVICE_ID)
                    filter_state = await filter_mgr.load()
                    f_lat, f_lon, new_filter_state = apply_gps_filter_stateless(
                        lat, lon, speed_kmh, filter_state, timestamp_s=server_now.timestamp()
                    )
                    await filter_mgr.save(new_filter_state)

                    # Route-constrained snap (math on the planned corridor; OSRM
                    # only on sustained deviation). Keeps the live dot on-road and
                    # advances 1-D progress forward-only (no backtrack/jitter).
                    _trip_id = await _get_active_trip_id(redis)
                    _direction = await _get_trip_direction(_trip_id, redis)
                    s_lat, s_lon, route_s, off_route, _bearing = await _route_snap_point(
                        redis, f_lat, f_lon, speed_kmh, _direction, _trip_id
                    )

                    cluster_mgr = RedisClusterState(redis, DEVICE_ID)
                    cluster_state = await cluster_mgr.load()
                    c_lat, c_lon, should_broadcast, new_cluster_state = apply_live_cluster_stateless(s_lat, s_lon, cluster_state)
                    await cluster_mgr.save(new_cluster_state)

                processed[log.id] = (c_lat, c_lon)

                location_name = None
                if should_broadcast:
                    location_name = await get_location_name(c_lat, c_lon, redis)

                snapped_event = GpsSnappedEvent(
                    device_id=DEVICE_ID, lat=c_lat, lon=c_lon, speed_kmh=speed_kmh,
                    server_time=server_now, should_broadcast=should_broadcast,
                    location_name=location_name,
                    route_s=route_s, off_route=off_route
                )
                await bus.publish("gps.snapped", snapped_event)
                if should_broadcast:
                    await redis.publish("bus:broadcast", snapped_event.model_dump_json())
                    
                    asyncio.create_task(publish_public_bus_state({
                        "lat": c_lat,
                        "lon": c_lon,
                        "speed_kmh": speed_kmh,
                        "is_live": True,
                        "location_name": location_name,
                        "timestamp": server_now.isoformat(),
                    }))

                logger.debug("[POLLER] Processed DB row ID %d: broadcast=%s", log.id, should_broadcast)

            # Batch-update all processed rows in a single DB round-trip
            if new_logs:
                active_trip_id = await _get_active_trip_id(redis)
                
                # Pre-build lookup dictionary for O(1) access to avoid O(N^2) loop
                logs_by_id = {log.id: log for log in new_logs}
                
                updates = {log.id: (processed[log.id]) for log in new_logs if log.id in processed}
                if updates:
                    async with AsyncSessionLocal() as db_batch:
                        for log_id, (c_lat, c_lon) in updates.items():
                            # Compute ist_time from original server_time stored in the log
                            orig_log = logs_by_id.get(log_id)
                            srv_time = orig_log.server_time if orig_log else datetime.now(timezone.utc)
                            if srv_time and srv_time.tzinfo is None:
                                srv_time = srv_time.replace(tzinfo=timezone.utc)
                            ist_time = srv_time.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None) if srv_time else None
                            await db_batch.execute(
                                update(GpsLog)
                                .where(GpsLog.id == log_id)
                                .values(
                                    source="pipeline",
                                    lat=c_lat,
                                    lon=c_lon,
                                    ist_time=ist_time,
                                    trip_id=active_trip_id,
                                )
                            )
                        await db_batch.commit()

                last_seen_id = new_logs[-1].id

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("[POLLER] Error in polling loop")
            await asyncio.sleep(5)


async def live_gps_realtime_bridge_loop():
    """
    Bridges GPS data from gps_realtime (written by hardware device via UPSERT)
    into gps_logs and publishes to Redis for live PWA updates.

    CRITICAL: gps_realtime uses UPSERT — the hardware device UPDATEs the same
    row in-place each GPS ping. This means created_at (server_time) is FROZEN
    at the original INSERT date and never changes. We MUST use gps_time (the
    actual GPS hardware clock) as our watermark — it changes on every update.
    """
    logger.info("[BRIDGE] Starting gps_realtime -> gps_logs bridge loop (watermark: gps_time)...")
    redis = await get_redis()
    bus = EventBus(redis)

    # Watermark: track the last gps_time we processed (changes every GPS ping)
    last_seen_gps_time = None

    # Seed watermark to current latest gps_time so we don't replay old history
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(GpsRealtime.gps_time).order_by(desc(GpsRealtime.gps_time)).limit(1)
        )
        row = result.scalars().first()
        if row:
            last_seen_gps_time = row if row.tzinfo else row.replace(tzinfo=timezone.utc)
    logger.info("[BRIDGE] gps_time watermark seeded to: %s", last_seen_gps_time)

    # Track last committed trace point to avoid flooding trip_trace with stationary pings
    last_trace_lat: float | None = None
    last_trace_lon: float | None = None
    MIN_TRACE_DIST_M = 10.0  # only write a new trace point if bus moved at least 10m

    # Fix #2: Seed last_trace from DB so the OSRM trail is continuous across service restarts.
    # Without this, last_trace_lat/lon starts as None on every restart, causing the first
    # OSRM trail segment to be skipped until the bus moves 10m from an unrecorded position.
    try:
        async with AsyncSessionLocal() as db_seed:
            active_trip_id_seed = await _get_active_trip_id(redis)
            if active_trip_id_seed:
                seed_res = await db_seed.execute(
                    select(TripTrace.snapped_lat, TripTrace.snapped_lon)
                    .where(TripTrace.trip_id == active_trip_id_seed)
                    .order_by(desc(TripTrace.gps_time))
                    .limit(1)
                )
                seed_row = seed_res.first()
                if seed_row and seed_row.snapped_lat is not None:
                    last_trace_lat = seed_row.snapped_lat
                    last_trace_lon = seed_row.snapped_lon
                    logger.info("[BRIDGE] Seeded last_trace from DB: (%.5f, %.5f)", last_trace_lat, last_trace_lon)
    except Exception:
        logger.warning("[BRIDGE] Could not seed last_trace from DB — will start fresh.")

    while True:
        try:
            await asyncio.sleep(2.0)
            async with AsyncSessionLocal() as db:
                query = (
                    select(GpsRealtime)
                    .where(GpsRealtime.lat.isnot(None))
                    .where(GpsRealtime.source.notin_(['ws', 'pipeline']))  # Ignore WS and pipeline pings to prevent double processing
                    .order_by(GpsRealtime.gps_time)
                )
                if last_seen_gps_time is not None:
                    # gps_time changes on every UPSERT — this correctly detects new pings
                    query = query.where(GpsRealtime.gps_time > last_seen_gps_time)
                result = await db.execute(query)
                new_rows = result.scalars().all()

            if not new_rows:
                continue

            for row in new_rows:
                lat, lon, speed_kmh = row.lat, row.lon, row.speed
                # Use gps_time as the authoritative timestamp (it's the hardware GPS clock)
                server_now = row.gps_time or row.server_time or datetime.now(timezone.utc)
                if server_now.tzinfo is None:
                    server_now = server_now.replace(tzinfo=timezone.utc)

                # Serialize this device's filter→snap→cluster state (see the
                # polling loop) so concurrent ingestion paths can't lose updates.
                async with device_state_lock(redis, DEVICE_ID):
                    # Step 1: GPS Filter
                    filter_mgr = RedisGpsFilterState(redis, DEVICE_ID)
                    filter_state = await filter_mgr.load()
                    f_lat, f_lon, new_filter_state = apply_gps_filter_stateless(
                        lat, lon, speed_kmh, filter_state, timestamp_s=server_now.timestamp()
                    )
                    await filter_mgr.save(new_filter_state)

                    # Step 2: Route-constrained snap. On the planned corridor this
                    # is pure math (no OSRM) with forward-only 1-D progress, which
                    # removes the backtrack/loop and per-ping jitter. OSRM is used
                    # only on sustained off-route deviation.
                    active_trip_id = await _get_active_trip_id(redis)
                    trip_direction = await _get_trip_direction(active_trip_id, redis)
                    s_lat, s_lon, route_s, off_route, bearing = await _route_snap_point(
                        redis, f_lat, f_lon, speed_kmh, trip_direction, active_trip_id
                    )

                    # Step 3: Cluster
                    cluster_mgr = RedisClusterState(redis, DEVICE_ID)
                    cluster_state = await cluster_mgr.load()
                    c_lat, c_lon, should_broadcast, new_cluster_state = apply_live_cluster_stateless(
                        s_lat, s_lon, cluster_state
                    )
                    await cluster_mgr.save(new_cluster_state)

                # Step 4: Persist into gps_logs with trip tagging
                ist_time = server_now.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
                async with AsyncSessionLocal() as db_write:
                    db_write.add(GpsLog(
                        server_time=server_now,
                        ist_time=ist_time,
                        lat=c_lat,
                        lon=c_lon,
                        speed=speed_kmh,
                        trip_id=active_trip_id,
                        source="bridge",
                        gps_time=server_now,  # Ensure gps_time is set so trigger doesn't overwrite to NULL
                    ))
                    await db_write.commit()

                # Step 4b: Append OSRM/math-snapped point to trip_trace (scheduled trips only)
                # Only write if the bus has actually moved ≥ MIN_TRACE_DIST_M since the last trace.
                osrm_path = None
                if active_trip_id:
                    dist_from_last_m = (
                        _haversine_m(last_trace_lat, last_trace_lon, s_lat, s_lon)
                        if last_trace_lat is not None else float('inf')
                    )
                    if dist_from_last_m >= MIN_TRACE_DIST_M:
                        if last_trace_lat is not None and last_trace_lon is not None:
                            osrm_path = await _build_trace_path(
                                redis, trip_direction,
                                last_trace_lat, last_trace_lon, s_lat, s_lon,
                                off_route, bearing, route_s,
                            )
                        async with AsyncSessionLocal() as db_trace:
                            db_trace.add(TripTrace(
                                trip_id=active_trip_id,
                                raw_lat=f_lat,
                                raw_lon=f_lon,
                                snapped_lat=s_lat,
                                snapped_lon=s_lon,
                                speed_kmh=speed_kmh,
                                osrm_path=osrm_path,
                                gps_time=server_now,
                            ))
                            await db_trace.commit()
                        last_trace_lat = s_lat
                        last_trace_lon = s_lon
                # Unscheduled (no active_trip_id): skip trip_trace — nothing to write

                # Step 5: Publish to Redis so WebSocket gateway pushes to PWA
                location_name = None
                if should_broadcast:
                    location_name = await get_location_name(c_lat, c_lon, redis)

                snapped_event = GpsSnappedEvent(
                    device_id=DEVICE_ID, lat=c_lat, lon=c_lon, speed_kmh=speed_kmh,
                    server_time=server_now, should_broadcast=should_broadcast,
                    location_name=location_name,
                    osrm_path=osrm_path,
                    route_s=route_s, off_route=off_route
                )
                await bus.publish("gps.snapped", snapped_event)
                if should_broadcast:
                    await redis.publish("bus:broadcast", snapped_event.model_dump_json())

                # Advance the watermark to this ping's gps_time
                last_seen_gps_time = row.gps_time if row.gps_time else last_seen_gps_time
                logger.info(
                    "[BRIDGE] gps_realtime -> gps_logs: (%.5f, %.5f) gps_time=%s trip_id=%s broadcast=%s",
                    c_lat, c_lon, last_seen_gps_time, active_trip_id, should_broadcast
                )

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("[BRIDGE] Error in gps_realtime bridge loop")
            await asyncio.sleep(5)


async def trip_trace_cleanup_loop():
    """
    Deletes trip_trace rows for scheduled trips that ended more than 1 hour ago.

    Lifecycle:
    - Morning trip ends ~9:30 AM; rows deleted at ~10:30 AM.
    - Evening trip starts at 5 PM with a clean trip_trace (no morning bleed).
    - Unscheduled trips never write to trip_trace, so nothing to clean up.
    """
    while True:
        try:
            await asyncio.sleep(300)  # run every 5 minutes
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Trip.id).where(
                        Trip.ended_at.isnot(None),
                        Trip.ended_at < cutoff,
                    )
                )
                old_ids = [r[0] for r in result.all()]
                if old_ids:
                    await db.execute(
                        sa_delete(TripTrace).where(TripTrace.trip_id.in_(old_ids))
                    )
                    await db.commit()
                    logger.info("[CLEANUP] Cleared trip_trace for completed trip IDs: %s", old_ids)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("[CLEANUP] Error in trip_trace cleanup loop")
            await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes GPS partitions and starts both the gps_logs poller, the
    gps_realtime bridge loop, and the trip_trace cleanup loop."""
    async with AsyncSessionLocal() as db:
        await ensure_gps_logs_partitions(db)
    poller_task  = asyncio.create_task(live_gps_polling_loop())
    bridge_task  = asyncio.create_task(live_gps_realtime_bridge_loop())
    cleanup_task = asyncio.create_task(trip_trace_cleanup_loop())
    yield
    for task in (poller_task, bridge_task, cleanup_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await close_osrm_client()


app = FastAPI(title="gps-ingestion-service", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)


async def process_payload(raw: str, db: AsyncSession, server_now: datetime) -> dict | None:
    """
    Parse raw QGPSLOC / POWER event from hardware.
    Filter → Snap → Cluster → Persist (tagged source="pipeline") → Publish.
    """
    logger.debug(f"[DEBUG RAW] {raw}")
    if raw.strip() == "PING":
        return {"type": "ping"}

    redis = await get_redis()
    bus = EventBus(redis)

    # ── Attempt to parse as JSON first (from HTTP Keep-Alive client) ──
    try:
        payload_data = _json.loads(raw)
        is_json = True
    except Exception:
        is_json = False

    if is_json and isinstance(payload_data, dict):
        event_type = payload_data.get("event")
        
        if event_type in ["POWER_LOST", "POWER_RESTORE", "POWER_ON", "POWER_OFF"]:
            is_off = event_type in ["POWER_LOST", "POWER_OFF"]
            event = "POWER_LOST" if is_off else "POWER_RESTORE"

            # Read last known position from gps_realtime so POWER events have a location
            last_pos_result = await db.execute(
                text("SELECT lat, lon FROM gps_realtime ORDER BY ist_time DESC NULLS LAST LIMIT 1")
            )
            last_pos_row = last_pos_result.first()
            last_lat = last_pos_row.lat if last_pos_row else None
            last_lon = last_pos_row.lon if last_pos_row else None

            ist_now = server_now.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
            db.add(GpsLog(
                server_time=server_now,
                ist_time=ist_now,
                lat=last_lat,
                lon=last_lon,
                event=event,
                source="pipeline"
            ))
            await db.commit()
            logger.info("[GPS-INGEST] Power event: %s at (%.5f, %.5f)", event, last_lat or 0, last_lon or 0)
            await redis.publish("bus:power", _json.dumps({"event": event, "server_time": server_now.isoformat()}))
            if not is_off:
                snap_state_mgr = RedisSnapState(redis, DEVICE_ID)
                await snap_state_mgr.reset()
            return {"type": "power", "event": event}

        elif event_type == "GPS":
            lat = float(payload_data.get("lat"))
            lon = float(payload_data.get("lon"))
            speed_kmh = float(payload_data.get("speed", 0.0))
            gps_time = server_now
            date_raw = ""
            time_raw = ""
            speed_raw = None
        elif event_type == "PING":
            return {"type": "ping"}
        else:
            raise ValueError(f"Unknown JSON event type: {event_type}")

    # ── Fallback to RAW string parsing (for WebSockets / Legacy) ──
    else:
        if any(x in raw for x in ["POWER_LOST", "POWER_RESTORE", "POWER_ON", "POWER_OFF"]):
            is_off = any(x in raw for x in ["POWER_LOST", "POWER_OFF"])
            event = "POWER_LOST" if is_off else "POWER_RESTORE"
    
            # Read last known position from gps_realtime so POWER events have a location
            last_pos_result = await db.execute(
                text("SELECT lat, lon FROM gps_realtime ORDER BY ist_time DESC NULLS LAST LIMIT 1")
            )
            last_pos_row = last_pos_result.first()
            last_lat = last_pos_row.lat if last_pos_row else None
            last_lon = last_pos_row.lon if last_pos_row else None
    
            ist_now = server_now.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
            db.add(GpsLog(
                server_time=server_now,
                ist_time=ist_now,
                lat=last_lat,
                lon=last_lon,
                event=event,
                source="pipeline"
            ))
            await db.commit()
            logger.info("[GPS-INGEST] Power event: %s at (%.5f, %.5f)", event, last_lat or 0, last_lon or 0)
            await redis.publish("bus:power", _json.dumps({"event": event, "server_time": server_now.isoformat()}))
            if not is_off:
                snap_state_mgr = RedisSnapState(redis, DEVICE_ID)
                await snap_state_mgr.reset()
            return {"type": "power", "event": event}

        match = re.search(r"\+QGPSLOC:\s*([^\r\n]+)", raw)
        if not match:
            raise ValueError("QGPSLOC not found in payload")
    
        parts = match.group(1).strip().split(",")
        if len(parts) < 10:
            raise ValueError("Incomplete GPS data")
    
        time_raw = parts[0].strip()
        lat = float(parts[1])
        lon = float(parts[2])
        speed_raw = float(parts[7]) if len(parts) > 7 and parts[7].strip() else None
        date_raw = parts[9].strip() if len(parts) > 9 else ""
        speed_kmh = round(speed_raw * 1.852, 2) if speed_raw is not None else None

    if not (settings.GPS_MIN_LAT <= lat <= settings.GPS_MAX_LAT):
        raise ValueError(f"Latitude out of service bounds: {lat}")
    if not (settings.GPS_MIN_LON <= lon <= settings.GPS_MAX_LON):
        raise ValueError(f"Longitude out of service bounds: {lon}")

    # For RAW parsing, calculate speed from knots. For JSON, speed_kmh is already set.
    if speed_raw is not None:
        speed_kmh = round(speed_raw * 1.852, 2)
    
    if not is_json or "gps_time" not in locals():
        gps_time = None
    if len(date_raw) >= 6 and len(time_raw) >= 6 and date_raw[:6].isdigit() and time_raw[:6].isdigit():
        try:
            gps_time_str = (
                f"20{date_raw[4:6]}-{date_raw[2:4]}-{date_raw[0:2]}"
                f"T{time_raw[0:2]}:{time_raw[2:4]}:{time_raw[4:6]}+00:00"
            )
            gps_time = datetime.fromisoformat(gps_time_str)
        except Exception:
            gps_time = server_now

    # Serialize this device's filter→snap→cluster state (see the polling loop)
    # so the device-push path can't interleave with the loops for the same device.
    async with device_state_lock(redis, DEVICE_ID):
        filter_mgr = RedisGpsFilterState(redis, DEVICE_ID)
        filter_state = await filter_mgr.load()
        timestamp_val = gps_time.timestamp() if gps_time else server_now.timestamp()
        f_lat, f_lon, new_filter_state = apply_gps_filter_stateless(
            lat, lon, speed_kmh, filter_state, timestamp_s=timestamp_val
        )
        await filter_mgr.save(new_filter_state)

        # Route-constrained snap (math on-corridor; OSRM only off-route).
        _trip_id = await _get_active_trip_id(redis)
        _direction = await _get_trip_direction(_trip_id, redis)
        s_lat, s_lon, route_s, off_route, bearing = await _route_snap_point(
            redis, f_lat, f_lon, speed_kmh, _direction, _trip_id
        )

        cluster_mgr = RedisClusterState(redis, DEVICE_ID)
        cluster_state = await cluster_mgr.load()
        c_lat, c_lon, should_broadcast, new_cluster_state = apply_live_cluster_stateless(s_lat, s_lon, cluster_state)
        await cluster_mgr.save(new_cluster_state)

    now_ist = server_now.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)

    active_trip_id = _trip_id

    # Step: Write to TripTrace
    if active_trip_id:
        last_trace = await db.execute(
            select(TripTrace)
            .where(TripTrace.trip_id == active_trip_id)
            .order_by(desc(TripTrace.gps_time))
            .limit(1)
        )
        last_row = last_trace.scalars().first()
        dist_from_last_m = float('inf')
        if last_row and last_row.snapped_lat is not None and last_row.snapped_lon is not None:
            dist_from_last_m = _haversine_m(last_row.snapped_lat, last_row.snapped_lon, s_lat, s_lon)

        if dist_from_last_m >= 10.0:
            osrm_path = None
            if last_row and last_row.snapped_lat is not None and last_row.snapped_lon is not None:
                osrm_path = await _build_trace_path(
                    redis, _direction,
                    last_row.snapped_lat, last_row.snapped_lon, s_lat, s_lon,
                    off_route, bearing, route_s,
                )
            
            db.add(TripTrace(
                trip_id=active_trip_id,
                raw_lat=f_lat,
                raw_lon=f_lon,
                snapped_lat=s_lat,
                snapped_lon=s_lon,
                speed_kmh=speed_kmh,
                osrm_path=osrm_path,
                gps_time=server_now,
            ))

    log = GpsLog(
        server_time=server_now,
        gps_time=gps_time,
        ist_time=now_ist,
        lat=c_lat,
        lon=c_lon,
        speed=speed_kmh,
        trip_id=active_trip_id,
        source="pipeline",   # <-- tag so the poller ignores this row
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    # 3. Even when no active trip, the last known position is always available (trip_id=0 sentinel)
    upsert_trip_id = 0
    ist_now = server_now.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
    await db.execute(
        text("""
            INSERT INTO gps_realtime (lat, lon, speed, ist_time, gps_time, trip_id, source)
            VALUES (:lat, :lon, :speed, :ist_time, :gps_time, :trip_id, 'ws')
            ON CONFLICT (trip_id) DO UPDATE SET
                lat      = EXCLUDED.lat,
                lon      = EXCLUDED.lon,
                speed    = EXCLUDED.speed,
                ist_time = EXCLUDED.ist_time,
                gps_time = EXCLUDED.gps_time,
                source   = 'ws'
        """),
        {
            "lat": c_lat, "lon": c_lon, "speed": speed_kmh,
            "ist_time": ist_now,
            "gps_time": gps_time or server_now,
            "trip_id": upsert_trip_id,
        }
    )
    await db.commit()

    location_name = None
    if should_broadcast:
        location_name = await get_location_name(c_lat, c_lon, redis)

    snapped_event = GpsSnappedEvent(
        device_id=DEVICE_ID, lat=c_lat, lon=c_lon, speed_kmh=speed_kmh,
        server_time=server_now, should_broadcast=should_broadcast,
        location_name=location_name,
        osrm_path=osrm_path if active_trip_id and dist_from_last_m >= 10.0 else None,
        route_s=route_s, off_route=off_route
    )
    await bus.publish("gps.snapped", snapped_event)

    if should_broadcast:
        await redis.publish("bus:broadcast", snapped_event.model_dump_json())
        
        await publish_public_bus_state({
            "lat": c_lat,
            "lon": c_lon,
            "speed_kmh": speed_kmh,
            "is_live": True,
            "location_name": location_name,
            "timestamp": server_now.isoformat(),
        })

    logger.debug("[GPS-INGEST] Published gps.snapped: (%.5f, %.5f) broadcast=%s", c_lat, c_lon, should_broadcast)
    return {"type": "gps", "lat": c_lat, "lon": c_lon}




@app.websocket("/api/v1/ws/device")
async def device_websocket(
    websocket: WebSocket,
    key: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Authenticates the GPS device and continuously receives and processes GPS payloads over a WebSocket connection.

    The device key is read from the X-API-Key header (same credential the HTTP
    ingest path uses), keeping it out of the URL query string where it would leak
    into access logs and proxies. The legacy ?key= query param is still accepted
    as a transitional fallback for firmware that can't yet set a header.
    """
    device_key = x_api_key or key
    if not device_key or not constant_time_compare(device_key, GPS_API_KEY):
        await websocket.close(code=4001)
        logger.warning("[GPS-INGEST] Rejected device connection — invalid key from %s",
                        websocket.client.host if websocket.client else "unknown")
        return

    await websocket.accept()
    logger.info("[GPS-INGEST] Hardware device connected from %s",
                websocket.client.host if websocket.client else "unknown")

    try:
        async with AsyncSessionLocal() as db:
            while True:
                try:
                    # BUG3 FIX: tight 90s timeout (3× the 30s device ping interval).
                    # If device is silent for 90s something is wrong; close so it
                    # reconnects rather than holding a phantom connection for 5 min.
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=90.0)
                except asyncio.TimeoutError:
                    # BUG1 FIX: send an application-level ping before giving up.
                    # Device firmware responds to any receive; this also flushes the
                    # WS layer so a server-side protocol ping/pong can complete.
                    try:
                        await asyncio.wait_for(
                            websocket.send_json({"type": "ping"}), timeout=5.0
                        )
                        logger.debug("[GPS-INGEST] App-level ping sent (device silent >90s)")
                        # Give device 30s to reply before declaring it dead
                        raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                    except Exception:
                        logger.warning("[GPS-INGEST] Device timed out — closing connection")
                        break

                raw = raw.strip()
                server_now = datetime.now(timezone.utc)
                if not raw:
                    continue
                # Ignore pong replies to our ping
                if raw in ('{"type":"pong"}', '{"type":"ping"}'):
                    continue
                if len(raw) > 2048:
                    logger.warning("[GPS-INGEST] Oversized payload rejected (%d bytes)", len(raw))
                    continue

                try:
                    result = await process_payload(raw, db, server_now)
                    logger.debug("[GPS-INGEST] Processed: %s", result)
                except ValueError as exc:
                    logger.warning("[GPS-INGEST] Parse error: %s", exc)
                    await websocket.send_json({"status": "error", "detail": "Invalid GPS payload format"})
                except Exception:
                    logger.exception("[GPS-INGEST] Unexpected error processing payload")
                    await websocket.send_json({"status": "error", "detail": "Internal server error"})
    except WebSocketDisconnect:
        logger.info("[GPS-INGEST] Hardware device disconnected")


@app.post("/api/v1/gps")
async def receive_gps_http(request: Request):
    """Receives, authenticates, and processes a GPS payload through HTTP."""
    provided_key = request.headers.get("X-API-Key", "")
    if not constant_time_compare(provided_key, GPS_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Rate-limit only AFTER authenticating, so unauthenticated spam from a
    # shared IP can't use up the real device's quota and block GPS ingest.
    await gps_http_limit(request)

    body = await request.body()
    if len(body) > 2048:
        raise HTTPException(status_code=413, detail="Payload too large")

    raw = body.decode("utf-8", errors="ignore").strip()
    server_now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        try:
            result = await process_payload(raw, db, server_now)
            return {"status": "ok", **(result or {})}
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid GPS payload format")
        except Exception:
            logger.exception("[GPS-INGEST] HTTP endpoint error")
            raise HTTPException(status_code=500, detail="Internal server error")




@app.get("/api/v1/gps/health")
async def health(db: AsyncSession = Depends(get_db)):
    """Checks PostgreSQL and Redis connectivity and returns the service health status."""
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
        content={"status": "ok" if status_code == 200 else "degraded", "service": "gps-ingestion-service", "db": db_status, "redis": redis_status}
    )
