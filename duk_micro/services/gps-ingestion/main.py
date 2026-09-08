# services/gps-ingestion/main.py
"""
GPS Ingestion Service — receives GPS data from the hardware bus device and
processes it through a pipeline before storing it and broadcasting to clients.

Data flow (pipeline for every GPS point received):
  Hardware device sends string (e.g., "+QGPSLOC: 0.0,8.5581,76.9061,1.0,...")
       ↓
  1. VALIDATE   — Check lat/lon are within Trivandrum bounds (8.0–9.0°N, 76–77.5°E)
       ↓
  2. FILTER     — Remove jitter (tiny movements when bus is stationary)
                   Uses a Kalman-like filter stored in Redis
       ↓
  3. SNAP       — Move the point to the nearest road (OSRM local)
                   Corrects GPS drift off-road
       ↓
  4. CLUSTER    — Only broadcast if the bus has moved meaningfully (>30m)
                   Prevents flooding Redis with duplicate positions
       ↓
  5. PERSIST    — Insert row into PostgreSQL gps_logs table
                   Tagged source="pipeline" so the poller doesn't re-process it
       ↓
  6. GEOCODE    — Async Nominatim reverse geocoding (doesn't block pipeline)
       ↓
  7. PUBLISH    — If should_broadcast: PUBLISH to Redis "bus:broadcast" channel
                   → realtime-gateway immediately forwards to all PWA browsers

Two entry points for GPS data:
  WS  /ws/device      — Hardware device WebSocket (authenticated with GPS_API_KEY)
  POST /api/v1/gps    — HTTP endpoint for testing / fallback

Background poller (live_gps_polling_loop):
  Some deployments write directly to the database (bypassing this service).
  The poller picks up rows where source IS NULL and runs them through the
  same pipeline. Rows processed by this service are tagged source="pipeline"
  so the poller skips them (no double-processing).
"""
import re
import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from libs.duk_common.redis_client import get_redis
from libs.duk_common.events import EventBus, GpsSnappedEvent
from libs.duk_common.state.gps_filter_redis import RedisGpsFilterState, apply_gps_filter_stateless
from libs.duk_common.state.snap_redis import RedisSnapState, apply_snap_stateless
from libs.duk_common.state.cluster_redis import RedisClusterState, apply_live_cluster_stateless
from libs.duk_common.osrm_client import snap_to_road, close_client as close_osrm_client
from libs.duk_common.security import constant_time_compare
from libs.duk_common.rate_limit_deps import make_rate_limit_dep
from libs.duk_common.geocoding import get_location_name

from database_local import get_db, AsyncSessionLocal
from models_local import GpsLog

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


#  Direct-to-DB Background Poller (only picks up UN-pipelined rows) 
async def live_gps_polling_loop():
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
                )
                if last_seen_id is not None:
                    query = query.where(GpsLog.id > last_seen_id)
                result = await db.execute(query)
                new_logs = result.scalars().all()

            if not new_logs:
                continue

            for log in new_logs:
                lat, lon, speed_kmh = log.lat, log.lon, log.speed
                server_now = log.server_time or datetime.now(timezone.utc)
                if server_now.tzinfo is None:
                    server_now = server_now.replace(tzinfo=timezone.utc)

                filter_mgr = RedisGpsFilterState(redis, DEVICE_ID)
                filter_state = await filter_mgr.load()
                f_lat, f_lon, new_filter_state = apply_gps_filter_stateless(lat, lon, speed_kmh, filter_state)
                await filter_mgr.save(new_filter_state)

                snap_mgr = RedisSnapState(redis, DEVICE_ID)
                snap_state = await snap_mgr.load()
                bearing_param = str(int(snap_state["bearing"])) if snap_state.get("bearing") is not None else None
                s_lat, s_lon = await snap_to_road(f_lat, f_lon, bearing=float(bearing_param) if bearing_param else None)
                new_snap_state = apply_snap_stateless(f_lat, f_lon, s_lat, s_lon, snap_state)
                await snap_mgr.save(new_snap_state)

                cluster_mgr = RedisClusterState(redis, DEVICE_ID)
                cluster_state = await cluster_mgr.load()
                c_lat, c_lon, should_broadcast, new_cluster_state = apply_live_cluster_stateless(s_lat, s_lon, cluster_state)
                await cluster_mgr.save(new_cluster_state)

                # Mark this row as processed so it's never re-picked-up
                async with AsyncSessionLocal() as db2:
                    row_obj = await db2.get(GpsLog, log.id)
                    if row_obj:
                        row_obj.source = "pipeline"
                        row_obj.lat = c_lat
                        row_obj.lon = c_lon
                        await db2.commit()

                location_name = None
                if should_broadcast:
                    location_name = await get_location_name(c_lat, c_lon, redis)

                snapped_event = GpsSnappedEvent(
                    device_id=DEVICE_ID, lat=c_lat, lon=c_lon, speed_kmh=speed_kmh,
                    server_time=server_now, should_broadcast=should_broadcast,
                    location_name=location_name
                )
                await bus.publish("gps.snapped", snapped_event)
                if should_broadcast:
                    await redis.publish("bus:broadcast", snapped_event.model_dump_json())

                last_seen_id = log.id
                logger.debug("[POLLER] Processed DB row ID %d: broadcast=%s", log.id, should_broadcast)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("[POLLER] Error in polling loop")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    poller_task = asyncio.create_task(live_gps_polling_loop())
    yield
    poller_task.cancel()
    try:
        await poller_task
    except asyncio.CancelledError:
        pass
    await close_osrm_client()


app = FastAPI(title="gps-ingestion-service", version="1.0.0", lifespan=lifespan)


async def process_payload(raw: str, db: AsyncSession, server_now: datetime) -> dict | None:
    """
    Parse raw QGPSLOC / POWER event from hardware.
    Filter → Snap → Cluster → Persist (tagged source="pipeline") → Publish.
    """
    redis = await get_redis()
    bus = EventBus(redis)

    if any(x in raw for x in ["POWER_LOST", "POWER_RESTORE", "POWER_ON", "POWER_OFF"]):
        is_off = any(x in raw for x in ["POWER_LOST", "POWER_OFF"])
        event = "POWER_LOST" if is_off else "POWER_RESTORE"
        db.add(GpsLog(server_time=server_now, event=event, source="pipeline"))
        await db.commit()
        logger.info("[GPS-INGEST] Power event: %s", event)
        await redis.publish("bus:power", f'{{"event":"{event}","server_time":"{server_now.isoformat()}"}}')
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

    if not (8.2 <= lat <= 9.0):
        raise ValueError(f"Latitude out of service bounds: {lat}")
    if not (76.5 <= lon <= 77.5):
        raise ValueError(f"Longitude out of service bounds: {lon}")

    speed_kmh = round(speed_raw * 1.852, 2) if speed_raw is not None else None
    
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

    filter_mgr = RedisGpsFilterState(redis, DEVICE_ID)
    filter_state = await filter_mgr.load()
    f_lat, f_lon, new_filter_state = apply_gps_filter_stateless(lat, lon, speed_kmh, filter_state)
    await filter_mgr.save(new_filter_state)

    snap_mgr = RedisSnapState(redis, DEVICE_ID)
    snap_state = await snap_mgr.load()
    bearing = float(snap_state["bearing"]) if snap_state.get("bearing") is not None else None
    s_lat, s_lon = await snap_to_road(f_lat, f_lon, bearing=bearing)
    new_snap_state = apply_snap_stateless(f_lat, f_lon, s_lat, s_lon, snap_state)
    await snap_mgr.save(new_snap_state)

    cluster_mgr = RedisClusterState(redis, DEVICE_ID)
    cluster_state = await cluster_mgr.load()
    c_lat, c_lon, should_broadcast, new_cluster_state = apply_live_cluster_stateless(s_lat, s_lon, cluster_state)
    await cluster_mgr.save(new_cluster_state)

    log = GpsLog(
        server_time=server_now,
        gps_time=gps_time,
        lat=c_lat,
        lon=c_lon,
        speed=speed_kmh,
        source="pipeline",   # <-- tag so the poller ignores this row
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    location_name = None
    if should_broadcast:
        location_name = await get_location_name(c_lat, c_lon, redis)

    snapped_event = GpsSnappedEvent(
        device_id=DEVICE_ID, lat=c_lat, lon=c_lon, speed_kmh=speed_kmh,
        server_time=server_now, should_broadcast=should_broadcast,
        location_name=location_name
    )
    await bus.publish("gps.snapped", snapped_event)

    if should_broadcast:
        await redis.publish("bus:broadcast", snapped_event.model_dump_json())

    logger.debug("[GPS-INGEST] Published gps.snapped: (%.5f, %.5f) broadcast=%s", c_lat, c_lon, should_broadcast)
    return {"type": "gps", "lat": c_lat, "lon": c_lon}




@app.websocket("/ws/device")
async def device_websocket(websocket: WebSocket, key: Optional[str] = Query(None)):
    if not key or not constant_time_compare(key, GPS_API_KEY):
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
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=300.0)
                except asyncio.TimeoutError:
                    logger.warning("[GPS-INGEST] Device connection timed out")
                    break

                raw = raw.strip()
                server_now = datetime.now(timezone.utc)
                if not raw:
                    continue
                if len(raw) > 2048:
                    logger.warning("[GPS-INGEST] Oversized payload rejected (%d bytes)", len(raw))
                    continue

                try:
                    result = await process_payload(raw, db, server_now)
                    logger.debug("[GPS-INGEST] Processed: %s", result)
                except ValueError as exc:
                    logger.warning("[GPS-INGEST] Parse error: %s", exc)
                except Exception:
                    logger.exception("[GPS-INGEST] Unexpected error processing payload")
    except WebSocketDisconnect:
        logger.info("[GPS-INGEST] Hardware device disconnected")


@app.post("/api/v1/gps")
async def receive_gps_http(request: Request, _limit: None = Depends(gps_http_limit)):
    provided_key = request.headers.get("X-API-Key", "")
    if not constant_time_compare(provided_key, GPS_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")

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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gps-ingestion-service"}
