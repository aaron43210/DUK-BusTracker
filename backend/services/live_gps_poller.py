import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc

from database import AsyncSessionLocal
from models.gps import GpsLog
from services.gps_filter import apply_gps_filter
from services.osrm_client import snap_live_gps
from services.live_cluster import apply_live_clustering

logger = logging.getLogger(__name__)

async def live_gps_polling_loop(manager):
    """
    Background task that polls the database for new GPS logs (since hardware writes
    directly to Supabase), processes them (filter -> snap -> cluster), and broadcasts 
    to the WebSocket manager.
    """
    last_seen_id = None
    
    # Initialize last_seen_id to the current max ID to avoid broadcasting old points
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GpsLog).order_by(desc(GpsLog.id)).limit(1))
        latest = result.scalars().first()
        if latest:
            last_seen_id = latest.id
            logger.info(f"[POLLER] Started polling from ID {last_seen_id}")

    while True:
        try:
            await asyncio.sleep(1.5)  # Poll every 1.5 seconds

            async with AsyncSessionLocal() as db:
                query = select(GpsLog)
                if last_seen_id is not None:
                    query = query.where(GpsLog.id > last_seen_id)
                query = query.order_by(GpsLog.id)
                
                result = await db.execute(query)
                new_logs = result.scalars().all()

                for log in new_logs:
                    if log.lat is not None and log.lon is not None:
                        # 1. Filter
                        f_lat, f_lon, _ = apply_gps_filter(log.lat, log.lon, log.speed, log.server_time)
                        
                        # 2. Snap
                        s_lat, s_lon = await snap_live_gps(f_lat, f_lon)
                        
                        # 3. Cluster
                        c_lat, c_lon, should_broadcast = apply_live_clustering(s_lat, s_lon)
                        
                        # 4. Broadcast
                        if should_broadcast:
                            await manager.broadcast({
                                "type": "gps",
                                "lat": c_lat,
                                "lon": c_lon,
                                "speed_kmh": log.speed,
                                "server_time": log.server_time.isoformat() if log.server_time else None
                            })
                    
                    last_seen_id = log.id

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[POLLER] Error in polling loop: {e}")
            await asyncio.sleep(5)
