"""
services/ml_scheduler.py
Background asyncio task that automatically trains the LightGBM ETA model.
Runs on a weekly schedule (Sunday at Midnight IST) to adapt to macro-level traffic patterns
without overfitting to daily anomalies.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from database import AsyncSessionLocal
from models.gps import GpsLog
from services.eta_engine import force_reload_model

logger = logging.getLogger(__name__)

from constants import IST_OFFSET

def _now_ist() -> datetime:
    return datetime.now(timezone.utc) + IST_OFFSET

async def _run_training():
    logger.info("[ML_SCHEDULER] Starting automated ML training...")
    try:
        # 1. Fetch all valid GPS logs from Postgres
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(GpsLog)
                .where(GpsLog.lat.isnot(None))
                .order_by(GpsLog.id)
            )
            logs = result.scalars().all()
            
            # Convert SQLAlchemy objects to dicts for train.py
            rows = [
                {
                    "server_time": log.server_time,
                    "lat": log.lat,
                    "lon": log.lon
                } for log in logs
            ]

        if not rows:
            logger.warning("[ML_SCHEDULER] No GPS data found. Skipping training.")
            return

        # 2. Run the heavy ML training inside a background thread (so it doesn't block FastAPI)
        from ml.train import train
        loop = asyncio.get_running_loop()
        
        logger.info("[ML_SCHEDULER] Handing off %d rows to LightGBM trainer thread...", len(rows))
        # validate_only=False will overwrite the model.pkl
        await loop.run_in_executor(None, train, rows, None, False)
        
        # 3. Hot-reload the live engine
        logger.info("[ML_SCHEDULER] Training completed. Hot-reloading live ETA engine.")
        force_reload_model()
        
    except Exception as exc:
        logger.exception("[ML_SCHEDULER] Automated training failed: %s", exc)

async def ml_training_loop():
    logger.info("[ML_SCHEDULER] Automated ML trainer started (Weekly: Sunday @ Midnight IST)")
    
    last_run_date = None
    
    while True:
        try:
            now = _now_ist()
            # Run only on Sundays (weekday() == 6) exactly between 00:00 and 00:05
            if now.weekday() == 6 and now.hour == 0 and now.minute < 5:
                if last_run_date != now.date():
                    last_run_date = now.date()
                    await _run_training()
        except Exception as exc:
            logger.error("[ML_SCHEDULER] Unhandled error: %s", exc)
            
        # Check the time every 60 seconds
        await asyncio.sleep(60)
