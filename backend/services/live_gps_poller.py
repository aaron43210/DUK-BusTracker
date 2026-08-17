import asyncio
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc

from database import AsyncSessionLocal
from models.gps import GpsLog
from services.gps_filter import apply_gps_filter
from services.osrm_client import snap_live_gps
from services.live_cluster import apply_live_clustering
from services.trip_lifecycle import handle_gps_update

logger = logging.getLogger(__name__)

async def live_gps_polling_loop(manager):
    """
    Polls for new GPS log rows and broadcasts to WebSocket clients.
    Uses a single session per poll cycle (not per log row).
    """
    last_seen_id: Optional[int] = None
    
    # Initialize last_seen_id to the current max ID to avoid broadcasting old points
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GpsLog.id).order_by(desc(GpsLog.id)).limit(1))
        row = result.scalars().first()
        if row:
            last_seen_id = row
            logger.info(f"[POLLER] Started polling from ID {last_seen_id}")

    while True:
        try:
            await asyncio.sleep(1.5)  # Poll every 1.5 seconds

            async with AsyncSessionLocal() as db:
                query = (
                    select(GpsLog.id, GpsLog.lat, GpsLog.lon, GpsLog.speed, GpsLog.server_time)
                    .where(GpsLog.lat.isnot(None))
                    .order_by(GpsLog.id)
                )
                if last_seen_id is not None:
                    query = query.where(GpsLog.id > last_seen_id)
                
                result = await db.execute(query)
                new_logs = result.all()

            if not new_logs:
                continue

            async def _process_log(log):
                f_lat, f_lon, _ = apply_gps_filter(log.lat, log.lon, log.speed, log.server_time)
                s_lat, s_lon = await snap_live_gps(f_lat, f_lon)
                c_lat, c_lon, should_broadcast = apply_live_clustering(s_lat, s_lon)
                if should_broadcast:
                    await manager.broadcast({
                        "type": "gps",
                        "lat": c_lat,
                        "lon": c_lon,
                        "speed_kmh": log.speed,
                        "server_time": log.server_time.isoformat() if log.server_time else None
                    })
                    
                async with AsyncSessionLocal() as local_db:
                    await handle_gps_update(local_db, manager, c_lat, c_lon, log.server_time, log)
                    await local_db.commit()

            await asyncio.gather(*(_process_log(log) for log in new_logs))
            last_seen_id = new_logs[-1].id

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[POLLER] Error in polling loop: {e}")
            await asyncio.sleep(5)
