"""
routers/gps.py — Hardware GPS ingestion + WebSocket real-time stream.

Two ingestion paths for the GPS hardware:
  POST /api/v1/gps              — legacy HTTP polling  (X-API-Key header)
  WS   /api/v1/ws/device?key=   — preferred real-time  (key query param)

One subscription path for the mobile app:
  WS   /api/v1/ws/bus           — subscribe-only broadcast stream
"""
import re
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import (
    APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Request, Query, Depends,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc

from database import get_db
from models.gps import GpsLog
from config import get_settings
from services.trip_lifecycle import handle_power_on, handle_power_off, handle_gps_update
from services.osrm_client import haversine_m_math, snap_live_gps

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter(prefix="/api/v1", tags=["gps"])


# ── WebSocket connection manager (mobile app subscribers) ─────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("[WS] Client connected. Total: %d", len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info("[WS] Client disconnected. Total: %d", len(self.active))

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.active:
                self.active.remove(ws)


manager = ConnectionManager()


# ── Shared GPS payload parser ─────────────────────────────────────────────────
async def process_raw_payload(raw: str, db: AsyncSession, server_now: datetime) -> dict:
    """
    Parse a raw payload string (QGPSLOC or POWER event) from the GPS hardware,
    persist to DB, run lifecycle transitions, and broadcast to mobile clients.
    Shared by both the HTTP POST and the WebSocket device endpoints.
    Raises ValueError on parse failure.
    """

    # ── Power events ──────────────────────────────────────────────────────────
    if any(x in raw for x in ["POWER_LOST", "POWER_RESTORE", "POWER_ON", "POWER_OFF"]):
        is_off = any(x in raw for x in ["POWER_LOST", "POWER_OFF"])
        event  = "POWER_LOST" if is_off else "POWER_RESTORE"

        db.add(GpsLog(server_time=server_now, event=event))
        await db.commit()
        logger.info("[GPS] Power event: %s", event)

        if is_off:
            await handle_power_off(db, manager, server_now)
        else:
            await handle_power_on(db, manager, server_now)

        return {"type": "power", "event": event}

    # ── QGPSLOC parse ─────────────────────────────────────────────────────────
    match = re.search(r"\+QGPSLOC:\s*([^\r\n]+)", raw)
    if not match:
        raise ValueError("QGPSLOC not found in payload")

    parts = match.group(1).strip().split(",")
    if len(parts) < 10:
        raise ValueError("Incomplete GPS data")

    time_raw  = parts[0]
    lat       = float(parts[1])
    lon       = float(parts[2])
    speed_raw = float(parts[7]) if len(parts) > 7 else None
    date_raw  = parts[9]

    if not (8.0 <= lat <= 9.0):
        raise ValueError(f"Invalid latitude: {lat}")
    if not (76.0 <= lon <= 77.5):
        raise ValueError(f"Invalid longitude: {lon}")

    speed_kmh    = round(speed_raw * 1.852, 2) if speed_raw is not None else None
    gps_time_str = (
        f"20{date_raw[4:6]}-{date_raw[2:4]}-{date_raw[0:2]}"
        f"T{time_raw[0:2]}:{time_raw[2:4]}:{time_raw[4:6]}+00:00"
    )
    gps_time = datetime.fromisoformat(gps_time_str)

    prev_res = await db.execute(
        select(GpsLog).where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.id)).limit(1)
    )
    prev = prev_res.scalar_one_or_none()

    if prev and prev.lat and prev.lon and speed_kmh is not None and speed_kmh < 1.0:
        dist_m = haversine_m_math(prev.lat, prev.lon, lat, lon)
        if dist_m < 10.0:  # Only suppress genuine stationary jitter (<10m at true standstill <1 km/h)
            lat = prev.lat
            lon = prev.lon
    # ──────────────────────────────────────────────────────────────────────────
    log = GpsLog(server_time=server_now, gps_time=gps_time, lat=lat, lon=lon, speed=speed_kmh)
    db.add(log)

    # Lifecycle: link log to active trip + detect movement / destination proximity
    await handle_gps_update(db, manager, lat, lon, server_now, log)

    await db.commit()

    # Broadcast raw coordinates immediately — don't wait for OSRM snap
    await manager.broadcast({
        "type":        "gps",
        "lat":         lat,
        "lon":         lon,
        "speed_kmh":   speed_kmh,
        "server_time": server_now.isoformat(),
    })

    # Snap to road asynchronously and send a correction frame if snapped coords differ
    asyncio.create_task(_snap_and_correct(lat, lon, manager))

    logger.debug("[GPS] Logged: %.5f, %.5f  speed=%.1f kmh", lat, lon, speed_kmh or 0)
    return {"type": "gps", "lat": lat, "lon": lon}


async def _snap_and_correct(lat: float, lon: float, mgr: "ConnectionManager") -> None:
    """Background task: snap GPS to nearest road and broadcast a correction frame if it moved."""
    try:
        snapped_lat, snapped_lon = await snap_live_gps(lat, lon)
        # Only broadcast correction if OSRM moved the point by more than 3m
        dist_m = haversine_m_math(lat, lon, snapped_lat, snapped_lon)
        if dist_m > 3.0:
            await mgr.broadcast({
                "type":    "gps_snap",
                "lat":     snapped_lat,
                "lon":     snapped_lon,
            })
    except Exception as e:
        logger.debug("[GPS] Snap correction failed: %s", e)


# ── Mobile app subscription (subscribe-only) ──────────────────────────────────
@router.websocket("/ws/bus")
async def bus_websocket(websocket: WebSocket):
    """Real-time GPS broadcast stream for the mobile app — subscribe only."""
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep-alive ping/pong
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Hardware device WebSocket (real-time ingestion) ───────────────────────────
@router.websocket("/ws/device")
async def device_websocket(
    websocket: WebSocket,
    key: Optional[str] = Query(None),
    db:  AsyncSession  = Depends(get_db),
):
    """
    Real-time WebSocket ingestion endpoint for the GPS hardware module.

    Hardware handshake should use:
      GET /api/v1/ws/device?key=<GPS_API_KEY> HTTP/1.1
      Host: <server>

    Once connected, send raw QGPSLOC strings or POWER_ON / POWER_OFF as text
    frames.  The server parses each message, persists it, drives the trip
    lifecycle state machine, and broadcasts real-time updates to all mobile
    app clients via the shared manager.

    Authentication: GPS_API_KEY as ?key= query parameter.
    Close code 4001 = rejected (bad key).
    """
    if key != settings.GPS_API_KEY:
        await websocket.close(code=4001)
        logger.warning("[WS-DEVICE] Rejected connection — invalid key")
        return

    await websocket.accept()
    logger.info("[WS-DEVICE] GPS hardware connected")

    try:
        while True:
            raw        = (await websocket.receive_text()).strip()
            server_now = datetime.now(timezone.utc)

            if not raw:
                continue  # ignore empty keep-alive frames

            try:
                result = await process_raw_payload(raw, db, server_now)
                logger.debug("[WS-DEVICE] Processed: %s", result)
            except ValueError as exc:
                logger.warning("[WS-DEVICE] Parse error: %s  raw=%r", exc, raw[:80])
            except Exception:
                logger.exception("[WS-DEVICE] Unexpected error on payload: %r", raw[:80])

    except WebSocketDisconnect:
        logger.info("[WS-DEVICE] GPS hardware disconnected")


# ── Legacy HTTP POST ingestion ────────────────────────────────────────────────
@router.post("/gps")
async def receive_gps(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Legacy HTTP POST endpoint — kept for backwards compatibility.
    Prefer the /ws/device WebSocket endpoint for real-time operation.
    Auth: X-API-Key header.
    """
    if request.headers.get("X-API-Key") != settings.GPS_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw        = (await request.body()).decode("utf-8", errors="ignore").strip()
    server_now = datetime.now(timezone.utc)

    try:
        result = await process_raw_payload(raw, db, server_now)
        return {"status": "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("[GPS] Parse error on payload: %r", raw[:120])
        raise HTTPException(status_code=500, detail="Internal server error")
