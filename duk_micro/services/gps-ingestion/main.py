# services/gps-ingestion/main.py
"""
gps-ingestion-service — Hardware GPS intake, filter → snap → cluster → publish.
Replaces routers/gps.py from the monolith with stateless, Redis-backed logic.
Includes a background poller for the direct-to-database IoT architecture.
"""
import re
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

# Shared lib imports (installed as package or via PYTHONPATH)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from libs.duk_common.redis_client import get_redis
from libs.duk_common.events import EventBus, GpsSnappedEvent
from libs.duk_common.state.gps_filter_redis import RedisGpsFilterState, apply_gps_filter_stateless
from libs.duk_common.state.snap_redis import RedisSnapState, apply_snap_stateless
from libs.duk_common.state.cluster_redis import RedisClusterState, apply_live_cluster_stateless

from database_local import get_db, AsyncSessionLocal
from osrm_snap import snap_to_road
from models_local import GpsLog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

GPS_API_KEY = os.environ.get("GPS_API_KEY", "local-dev-key")
DEVICE_ID = os.environ.get("DEVICE_ID", "bus-1")

# ── Direct-to-DB Background Poller ────────────────────────────────────────────

async def live_gps_polling_loop():
    logger.info("[POLLER] Starting direct-to-db polling loop...")
    redis = await get_redis()
    bus = EventBus(redis)
    last_seen_id = None
    
    # Init last_seen_id
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
                query = select(GpsLog).where(GpsLog.lat.isnot(None)).order_by(GpsLog.id)
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

                # Filter
                filter_mgr = RedisGpsFilterState(redis, DEVICE_ID)
                filter_state = await filter_mgr.load()
                f_lat, f_lon, new_filter_state = apply_gps_filter_stateless(lat, lon, speed_kmh, filter_state)
                await filter_mgr.save(new_filter_state)

                # Snap
                snap_mgr = RedisSnapState(redis, DEVICE_ID)
                snap_state = await snap_mgr.load()
                bearing_param = str(int(snap_state["bearing"])) if snap_state.get("bearing") is not None else None
                s_lat, s_lon = await snap_to_road(f_lat, f_lon, bearing=bearing_param)
                new_snap_state = apply_snap_stateless(f_lat, f_lon, s_lat, s_lon, snap_state)
                await snap_mgr.save(new_snap_state)

                # Cluster
                cluster_mgr = RedisClusterState(redis, DEVICE_ID)
                cluster_state = await cluster_mgr.load()
                c_lat, c_lon, should_broadcast, new_cluster_state = apply_live_cluster_stateless(s_lat, s_lon, cluster_state)
                await cluster_mgr.save(new_cluster_state)

                # Publish to Event Stream (trip-lifecycle)
                snapped_event = GpsSnappedEvent(
                    device_id=DEVICE_ID,
                    lat=c_lat,
                    lon=c_lon,
                    speed_kmh=speed_kmh,
                    server_time=server_now,
                    should_broadcast=should_broadcast,
                )
                await bus.publish("gps.snapped", snapped_event)

                # Publish to Pub/Sub (realtime-gateway)
                if should_broadcast:
                    await redis.publish("bus:broadcast", snapped_event.model_dump_json())

                last_seen_id = log.id
                logger.debug("[POLLER] Processed DB row ID %d: broadcast=%s", log.id, should_broadcast)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("[POLLER] Error in polling loop: %s", e)
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


app = FastAPI(title="gps-ingestion-service", version="1.0.0", lifespan=lifespan)

async def process_payload(raw: str, db: AsyncSession, server_now: datetime) -> dict | None:
    """
    Parse raw QGPSLOC / POWER event from hardware.
    Filter → Snap → Cluster → Persist → Publish.
    Returns result dict or None on parse failure.
    """
    redis = await get_redis()
    bus = EventBus(redis)

    # ── Power events ──────────────────────────────────────────────────────
    if any(x in raw for x in ["POWER_LOST", "POWER_RESTORE", "POWER_ON", "POWER_OFF"]):
        is_off = any(x in raw for x in ["POWER_LOST", "POWER_OFF"])
        event = "POWER_LOST" if is_off else "POWER_RESTORE"

        db.add(GpsLog(server_time=server_now, event=event))
        await db.commit()
        logger.info("[GPS-INGEST] Power event: %s", event)

        # Publish power event so trip-lifecycle-service can react
        await redis.publish("bus:power", f'{{"event":"{event}","server_time":"{server_now.isoformat()}"}}')

        # Reset snap state on power restore
        if not is_off:
            snap_state_mgr = RedisSnapState(redis, DEVICE_ID)
            await snap_state_mgr.reset()

        return {"type": "power", "event": event}

    # ── QGPSLOC parse ─────────────────────────────────────────────────────
    match = re.search(r"\+QGPSLOC:\s*([^\r\n]+)", raw)
    if not match:
        raise ValueError("QGPSLOC not found in payload")

    parts = match.group(1).strip().split(",")
    if len(parts) < 10:
        raise ValueError("Incomplete GPS data")

    time_raw = parts[0]
    lat = float(parts[1])
    lon = float(parts[2])
    speed_raw = float(parts[7]) if len(parts) > 7 else None
    date_raw = parts[9]

    if not (8.0 <= lat <= 9.0):
        raise ValueError(f"Invalid latitude: {lat}")
    if not (76.0 <= lon <= 77.5):
        raise ValueError(f"Invalid longitude: {lon}")

    speed_kmh = round(speed_raw * 1.852, 2) if speed_raw is not None else None
    gps_time_str = (
        f"20{date_raw[4:6]}-{date_raw[2:4]}-{date_raw[0:2]}"
        f"T{time_raw[0:2]}:{time_raw[2:4]}:{time_raw[4:6]}+00:00"
    )
    gps_time = datetime.fromisoformat(gps_time_str)

    # ── Stage 1: GPS Filter (Redis-backed state) ───────────────────────────
    filter_mgr = RedisGpsFilterState(redis, DEVICE_ID)
    filter_state = await filter_mgr.load()
    f_lat, f_lon, new_filter_state = apply_gps_filter_stateless(lat, lon, speed_kmh, filter_state)
    await filter_mgr.save(new_filter_state)

    logger.debug("[GPS-INGEST] Raw=(%.5f,%.5f) Filtered=(%.5f,%.5f)", lat, lon, f_lat, f_lon)

    # ── Stage 2: OSRM Snap (Redis-backed bearing lock) ────────────────────
    snap_mgr = RedisSnapState(redis, DEVICE_ID)
    snap_state = await snap_mgr.load()

    bearing_param = ""
    if snap_state.get("bearing") is not None:
        bearing_param = str(int(snap_state["bearing"]))

    s_lat, s_lon = await snap_to_road(f_lat, f_lon, bearing=bearing_param if bearing_param else None)
    new_snap_state = apply_snap_stateless(f_lat, f_lon, s_lat, s_lon, snap_state)
    await snap_mgr.save(new_snap_state)

    # ── Stage 3: Live Clustering (Redis-backed anchor) ─────────────────────
    cluster_mgr = RedisClusterState(redis, DEVICE_ID)
    cluster_state = await cluster_mgr.load()
    c_lat, c_lon, should_broadcast, new_cluster_state = apply_live_cluster_stateless(s_lat, s_lon, cluster_state)
    await cluster_mgr.save(new_cluster_state)

    # ── Stage 4: Persist to DB ─────────────────────────────────────────────
    log = GpsLog(
        server_time=server_now,
        gps_time=gps_time,
        lat=c_lat,
        lon=c_lon,
        speed=speed_kmh,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    # ── Stage 5: Publish event to Redis Streams (for trip-lifecycle) ───────
    snapped_event = GpsSnappedEvent(
        device_id=DEVICE_ID,
        lat=c_lat,
        lon=c_lon,
        speed_kmh=speed_kmh,
        server_time=server_now,
        should_broadcast=should_broadcast,
    )
    await bus.publish("gps.snapped", snapped_event)

    # ── Stage 6: Pub/Sub broadcast to realtime-gateway ────────────────────
    if should_broadcast:
        await redis.publish(
            "bus:broadcast",
            snapped_event.model_dump_json(),
        )

    logger.debug("[GPS-INGEST] Published gps.snapped: (%.5f, %.5f) broadcast=%s", c_lat, c_lon, should_broadcast)
    return {"type": "gps", "lat": c_lat, "lon": c_lon}


# ── WebSocket: Mobile app subscribers (subscribe-only, read from Redis pub/sub) ─
@app.websocket("/ws/bus")
async def bus_websocket_compat(websocket: WebSocket):
    await websocket.accept()
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe("bus:broadcast")
    try:
        async def redis_reader():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        await websocket.send_text(message["data"])
                    except Exception:
                        break

        reader_task = asyncio.create_task(redis_reader())
        while True:
            await websocket.receive_text()  # keepalive
    except WebSocketDisconnect:
        reader_task.cancel()
        await pubsub.unsubscribe("bus:broadcast")


# ── WebSocket: Hardware device ingestion ─────────────────────────────────────
@app.websocket("/ws/device")
async def device_websocket(
    websocket: WebSocket,
    key: Optional[str] = Query(None),
):
    if key != GPS_API_KEY:
        await websocket.close(code=4001)
        logger.warning("[GPS-INGEST] Rejected device connection — invalid key")
        return

    await websocket.accept()
    logger.info("[GPS-INGEST] Hardware device connected")

    try:
        async with AsyncSessionLocal() as db:
            while True:
                raw = (await websocket.receive_text()).strip()
                server_now = datetime.now(timezone.utc)
                if not raw:
                    continue
                try:
                    result = await process_payload(raw, db, server_now)
                    logger.debug("[GPS-INGEST] Processed: %s", result)
                except ValueError as exc:
                    logger.warning("[GPS-INGEST] Parse error: %s | raw=%r", exc, raw[:80])
                except Exception:
                    logger.exception("[GPS-INGEST] Unexpected error on payload: %r", raw[:80])
    except WebSocketDisconnect:
        logger.info("[GPS-INGEST] Hardware device disconnected")


# ── Legacy HTTP POST ──────────────────────────────────────────────────────────
@app.post("/api/v1/gps")
async def receive_gps_http(request: Request):
    if request.headers.get("X-API-Key") != GPS_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw = (await request.body()).decode("utf-8", errors="ignore").strip()
    server_now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        try:
            result = await process_payload(raw, db, server_now)
            return {"status": "ok", **(result or {})}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            logger.exception("[GPS-INGEST] HTTP parse error: %r", raw[:120])
            raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gps-ingestion-service"}
