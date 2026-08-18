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
import lightgbm as lgb
from datetime import datetime, timezone
from pathlib import Path
from redis.asyncio import Redis
from sklearn.model_selection import train_test_split
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, Float, DateTime, String, Boolean

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:////data/tracker.db")
REDIS_URL    = os.environ.get("REDIS_URL", "redis://redis:6379/0")
MODEL_PATH   = Path(os.environ.get("MODEL_PATH", "/data/models/eta_model.pkl"))
ETA_ML_URL   = os.environ.get("ETA_ML_URL", "http://eta-ml-service:8000")

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class GpsLog(Base):
    __tablename__ = "gps_realtime"
    id          = Column(Integer, primary_key=True)
    lat         = Column(Float)
    lon         = Column(Float)
    server_time = Column("created_at", DateTime(timezone=True))

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
        rows = [{"server_time": l.server_time, "lat": l.lat, "lon": l.lon} for l in logs]

        res = await db.execute(select(BusStop).order_by(BusStop.id))
        stops = [{"id": s.id, "lat": s.lat, "lon": s.lon} for s in res.scalars().all()]

    if len(rows) < 100:
        logger.warning("[ML] Not enough GPS data (%d rows) to train.", len(rows))
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
        current_trip.append((t, r["lat"], r["lon"]))
    if len(current_trip) >= 10: trips.append(current_trip)

    X_rows, y_rows = [], []
    for trip in trips:
        trip_start = trip[0][0]
        if not (6 <= trip_start.hour < 11 or 17 <= trip_start.hour < 21): continue
        is_evening = trip_start.hour >= 17

        speeds = [0.0]
        for i in range(1, len(trip)):
            dt_sec = (trip[i][0] - trip[i-1][0]).total_seconds()
            d = haversine_km(trip[i-1][1], trip[i-1][2], trip[i][1], trip[i][2])
            speeds.append(d / dt_sec * 3600 if dt_sec > 0 else 0)

        for i, (t, lat, lon) in enumerate(trip):
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
        logger.warning("[ML] Not enough valid samples (%d).", len(X))
        return

    logger.info("[ML] Training LightGBM on %d samples...", len(X))
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2)
    model = lgb.LGBMRegressor(n_estimators=100, learning_rate=0.1, num_leaves=31)
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)])
    
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    logger.info("[ML] Model saved to %s", MODEL_PATH)

    # Signal eta-ml-service to reload
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"{ETA_ML_URL}/api/v1/ml/reload")
        logger.info("[ML] Signaled eta-ml-service to reload model")
    except Exception as e:
        logger.error("[ML] Failed to signal reload: %s", e)


async def main():
    logger.info("[ML] Training job starting. Listening on redis: ml:retrain_requested")
    redis = Redis.from_url(REDIS_URL)
    pubsub = redis.pubsub()
    await pubsub.subscribe("ml:retrain_requested")

    async for msg in pubsub.listen():
        if msg["type"] == "message":
            logger.info("[ML] Retrain requested by admin!")
            await run_training()

if __name__ == "__main__":
    asyncio.run(main())
