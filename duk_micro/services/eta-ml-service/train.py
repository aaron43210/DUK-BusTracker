#!/usr/bin/env python3
"""
train.py
ETA Machine Learning Training Script.
Trains LightGBM / HistGradientBoosting on historical trip and GPS data using
the canonical 9-feature schema shared with eta-ml-service inference:
[dist_to_stop, stops_ahead, hour, day_of_week, is_evening, elapsed_minutes, speed_kmh, osrm_eta_mins, from_osrm_flag]
"""
import os
import sys
import math
import joblib
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
import httpx
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

MODEL_PATH = os.environ.get("MODEL_PATH", "/app/models/eta_model.onnx")
RELOAD_URL = os.environ.get("RELOAD_URL", "http://127.0.0.1:8000/api/v1/ml/reload")
ML_INTERNAL_TOKEN = os.environ.get("ML_INTERNAL_TOKEN", "dev-ml-token-change-in-prod")
BUS_FACTOR = 1.35


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def main():
    if not DATABASE_URL:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    logger.info("Connecting to database...")
    engine = create_engine(DATABASE_URL)

    # 1. Fetch stops
    stops_df = pd.read_sql("SELECT id, lat, lon FROM bus_stops ORDER BY id", engine)
    if stops_df.empty:
        logger.warning("No bus stops found in database.")
        sys.exit(0)
    stops = stops_df.to_dict("records")

    # 2. Fetch completed/ended trips
    query_trips = """
        SELECT id, direction, started_at, ended_at, late_by_minutes, visited_stops 
        FROM trips 
        WHERE status IN ('completed', 'ended', 'on_trip')
    """
    df_trips = pd.read_sql(query_trips, engine)
    
    # 3. Fetch GPS logs
    query_gps = """
        SELECT id, trip_id, lat, lon, speed, ist_time, created_at 
        FROM gps_realtime 
        WHERE lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY id
    """
    df_gps = pd.read_sql(query_gps, engine)
    
    if len(df_trips) < 15:
        logger.info(f"[ML] Insufficient new trip data ({len(df_trips)} trips < 15). Skipping weekly training until next scheduled cycle.")
        sys.exit(0)

    if len(df_gps) < 500:
        logger.info(f"[ML] Insufficient new GPS data ({len(df_gps)} pts < 500). Skipping weekly training until next scheduled cycle.")
        sys.exit(0)

    logger.info(f"Loaded {len(df_trips)} trips and {len(df_gps)} GPS points for feature extraction.")

    # 4. Feature engineering (9 canonical features)
    X_rows = []
    y_rows = []

    # Group GPS by trip_id or time gaps
    gps_groups = df_gps.groupby("trip_id") if "trip_id" in df_gps.columns and df_gps["trip_id"].notnull().any() else [(1, df_gps)]

    for _, group in gps_groups:
        pts = group.to_dict("records")
        if len(pts) < 10:
            continue

        for i, pt in enumerate(pts):
            lat, lon = pt["lat"], pt["lon"]
            speed = pt.get("speed") or 25.0
            
            # Timestamp parsing
            t = pt.get("ist_time") or pt.get("created_at")
            if t is None:
                continue
            if isinstance(t, str):
                t = pd.to_datetime(t)

            hour = t.hour
            day_of_week = t.weekday()
            is_evening = 1 if hour >= 17 else 0
            elapsed_min = (hour * 60 + t.minute) - (450 if not is_evening else 1060)

            # Find upcoming stops
            for s in stops:
                dist_to_stop = haversine_km(lat, lon, s["lat"], s["lon"])
                if dist_to_stop > 35.0:
                    continue

                stops_ahead = max(1, abs(s["id"] - (i % 21)))
                osrm_eta_mins = (dist_to_stop / 25.0) * 60.0 * BUS_FACTOR

                # Approximate actual target arrival time
                target_eta = max(0.5, osrm_eta_mins)

                # 9 canonical features matching eta-ml-service/main.py:
                # [distance_km, stops_ahead, hour, day_of_week, direction, elapsed_minutes, speed_kmh, osrm_eta_mins, from_osrm_flag]
                X_rows.append([
                    dist_to_stop,
                    stops_ahead,
                    hour,
                    day_of_week,
                    is_evening,
                    elapsed_min,
                    speed,
                    osrm_eta_mins,
                    0
                ])
                y_rows.append(target_eta)

    if len(X_rows) < 50:
        logger.warning(f"Only {len(X_rows)} samples generated. Minimum 50 required.")
        sys.exit(0)

    X = np.array(X_rows, dtype=np.float32)
    y = np.array(y_rows, dtype=np.float32)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    logger.info(f"Training model on {len(X_train)} samples...")
    model = lgb.LGBMRegressor(
        n_estimators=150,
        num_leaves=31,
        learning_rate=0.1,
        random_state=42
    )
    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    mae = mean_absolute_error(y_test, predictions)
    logger.info(f"Model Evaluation - MAE: {mae:.2f} minutes")

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    
    # Convert and save to ONNX
    from onnxmltools import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType
    initial_type = [('float_input', FloatTensorType([None, 9]))]
    onx = convert_lightgbm(model, initial_types=initial_type)
    
    with open(MODEL_PATH, "wb") as f:
        f.write(onx.SerializeToString())
    
    logger.info(f"ONNX Model safely saved to {MODEL_PATH}")

    # Hot-reload backend service
    logger.info("Notifying backend to reload the model...")
    try:
        headers = {"X-Internal-Token": ML_INTERNAL_TOKEN} if ML_INTERNAL_TOKEN else {}
        resp = httpx.post(RELOAD_URL, headers=headers, timeout=3.0)
        if resp.status_code == 200:
            logger.info("Backend successfully reloaded the model.")
        else:
            logger.warning(f"Backend returned {resp.status_code} during reload: {resp.text}")
    except Exception as e:
        logger.warning(f"Could not notify backend to reload: {e}")

    logger.info("Training pipeline completed successfully.")


if __name__ == "__main__":
    main()
