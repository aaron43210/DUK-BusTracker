# services/ml-training-job/train_job.py
"""
ML Training Job for ETA Model.
Replicates monolith ml_scheduler.py and ml/train.py.
Triggered by Redis pub/sub channel 'ml:retrain_requested' (sent by admin-service)
or runs periodically.
"""
import asyncio
import httpx
import joblib
import json
import logging
import math
import os
import sys
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from redis.asyncio import Redis
from sklearn.model_selection import train_test_split
import lightgbm as lgb
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, Float, DateTime, String, Boolean

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:////data/tracker.db")
REDIS_URL    = os.environ.get("REDIS_URL", "redis://redis:6379/0")
MODEL_PATH   = Path(os.environ.get("MODEL_PATH", "/app/models/eta_model.onnx"))
ETA_ML_URL   = os.environ.get("ETA_ML_URL", "http://core-api:8000")
ML_INTERNAL_TOKEN = os.environ.get("ML_INTERNAL_TOKEN", "dev-ml-token-change-in-prod")

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class GpsLog(Base):
    __tablename__ = "gps_realtime"
    id          = Column(Integer, primary_key=True)
    lat         = Column(Float)
    lon         = Column(Float)
    server_time = Column("created_at", DateTime(timezone=True))
    gps_time    = Column(DateTime(timezone=True), nullable=True)
    source      = Column(String(20), nullable=True, index=True)

class BusStop(Base):
    __tablename__ = "bus_stops"
    id          = Column(Integer, primary_key=True)
    route_id    = Column(Integer)
    lat         = Column(Float)
    lon         = Column(Float)
    order_index = Column(Integer)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

def osrm_eta_minutes(lat1, lon1, lat2, lon2):
    return (haversine_km(lat1, lon1, lat2, lon2) / 25.0) * 60.0 * 1.35 # Rough fallback for training

def find_nearest_stop(lat, lon, stops):
    best, best_d = None, float("inf")
    for s in stops:
        d = haversine_km(lat, lon, s["lat"], s["lon"])
        if d < best_d:
            best_d = d
            best = s
    return best if best_d <= 0.4 else None


async def run_training():
    logger.info("[ML] Fetching data for training...")
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(GpsLog).where(GpsLog.lat.isnot(None)).order_by(GpsLog.id))
        logs = res.scalars().all()
        rows = [{"server_time": l.server_time, "lat": l.lat, "lon": l.lon, "source": l.source} for l in logs]

        res = await db.execute(select(BusStop).order_by(BusStop.id))
        stops = [{"id": s.id, "lat": s.lat, "lon": s.lon} for s in res.scalars().all()]

    if len(rows) < 500:
        logger.info("[ML] Insufficient new GPS data (%d rows < 500). Skipping training.", len(rows))
        return

    logger.info("[ML] Extracting features...")
    trips = []
    current_trip = []
    for r in rows:
        t = r["server_time"]
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if current_trip:
            gap = (t - current_trip[-1][0]).total_seconds() / 60
            if gap > 30:
                if len(current_trip) >= 10: trips.append(current_trip)
                current_trip = []
        current_trip.append((t, r["lat"], r["lon"], r["source"]))
    if len(current_trip) >= 10: trips.append(current_trip)
    
    if len(trips) < 15:
        logger.info("[ML] Insufficient new trip data (%d trips < 15). Skipping training.", len(trips))
        return

    X_rows, y_rows = [], []
    for trip in trips:
        trip_start = trip[0][0]
        
        # Source column is null in current dataset, fallback to strict time/day bounds
        if trip_start.weekday() >= 5: # Skip weekends
            continue
        if not (6 <= trip_start.hour < 11 or 17 <= trip_start.hour < 21):
            continue
            
        is_evening = 17 <= trip_start.hour < 21

        is_deviated = False
        for _, lat, lon, _ in trip:
            nearest = find_nearest_stop(lat, lon, stops)
            if nearest is None: # find_nearest_stop threshold is 0.4km
                is_deviated = True
                break
        if is_deviated:
            logger.info("[ML] Dropping deviated trip starting at %s", trip_start)
            continue

        speeds = [0.0]
        for i in range(1, len(trip)):
            dt_sec = (trip[i][0] - trip[i-1][0]).total_seconds()
            d = haversine_km(trip[i-1][1], trip[i-1][2], trip[i][1], trip[i][2])
            speeds.append(d / dt_sec * 3600 if dt_sec > 0 else 0)

        for i, (t, lat, lon, _) in enumerate(trip):
            speed_last_3 = float(np.mean(speeds[max(0, i-3):i+1]))
            elapsed_min = (t - trip_start).total_seconds() / 60

            for stop in stops:
                reached_at = None
                for j in range(i+1, len(trip)):
                    if haversine_km(trip[j][1], trip[j][2], stop["lat"], stop["lon"]) <= 0.35:
                        reached_at = trip[j][0]
                        break
                if not reached_at: continue
                
                actual_eta = (reached_at - t).total_seconds() / 60
                if not (0 < actual_eta <= 120): continue

                dist_to_stop = haversine_km(lat, lon, stop["lat"], stop["lon"]) * 1.2 # road factor
                eta_osrm = (dist_to_stop / 25.0) * 60.0 * 1.35
                
                bus_nearest = find_nearest_stop(lat, lon, stops)
                bus_order = bus_nearest["id"] if bus_nearest else 0
                stops_remaining = abs(stop["id"] - bus_order)

                X_rows.append([
                    dist_to_stop, stops_remaining, t.hour, t.weekday(),
                    int(is_evening), elapsed_min, speed_last_3, eta_osrm, 0
                ])
                y_rows.append(actual_eta)

    X, y = np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.float32)
    if len(X) < 50:
        logger.info("[ML] Not enough valid samples (%d). Skipping training.", len(X))
        return

    logger.info("[ML] Training LightGBM on %d samples...", len(X))
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2)
    model = lgb.LGBMRegressor(n_estimators=100, learning_rate=0.1, num_leaves=31)
    
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)])
    
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    from onnxmltools import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType
    initial_type = [('float_input', FloatTensorType([None, 9]))]
    onx = convert_lightgbm(model, initial_types=initial_type)
    
    with open(MODEL_PATH, "wb") as f:
        f.write(onx.SerializeToString())
        
    logger.info("[ML] ONNX Model safely saved to %s", MODEL_PATH)

    # Signal eta-ml-service to reload
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            headers = {"X-Internal-Token": ML_INTERNAL_TOKEN} if ML_INTERNAL_TOKEN else {}
            await client.post(f"{ETA_ML_URL}/api/v1/ml/reload", headers=headers)
        logger.info("[ML] Signaled eta-ml-service to reload model")
    except Exception as e:
        logger.error("[ML] Failed to signal reload: %s", e)


async def weekly_training_loop():
    """Background task to run training every Sunday at 02:00 IST."""
    IST_OFFSET = timedelta(hours=5, minutes=30)
    while True:
        now_ist = datetime.now(timezone.utc) + IST_OFFSET
        # Check if it's Sunday (6) and 2:00 AM
        if now_ist.weekday() == 6 and now_ist.hour == 2 and now_ist.minute == 0:
            logger.info("[ML] Scheduled weekly training cycle started.")
            try:
                await run_training()
            except Exception as e:
                logger.exception("[ML] Scheduled training failed: %s", e)
            await asyncio.sleep(60) # Prevent multiple triggers within the same minute
        await asyncio.sleep(30)


async def main():
    logger.info("[ML] Training job starting. Listening on redis: ml:retrain_requested")
    
    # Start the weekly schedule loop in the background
    asyncio.create_task(weekly_training_loop())

    redis = Redis.from_url(REDIS_URL)
    pubsub = redis.pubsub()
    await pubsub.subscribe("ml:retrain_requested")

    async for msg in pubsub.listen():
        if msg["type"] == "message":
            logger.info("[ML] Retrain requested by admin!")
            try:
                await run_training()
            except Exception as e:
                logger.exception("[ML] Admin training trigger failed: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
