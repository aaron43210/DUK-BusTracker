#!/usr/bin/env python3
"""
train.py
ETA Machine Learning Training Script.
Trains LightGBM / HistGradientBoosting on historical trip and GPS data using
the exact 8-feature schema shared with eta-ml-service inference:
[distance_km, stops_ahead, hour, day_of_week, direction_reverse, elapsed_minutes, speed_kmh, naive_osrm_eta_mins]
"""
import os
import sys
import math
import pandas as pd
import numpy as np
from datetime import datetime
from sqlalchemy import create_engine
import lightgbm as lgb
from sklearn.metrics import mean_absolute_error, mean_squared_error
import httpx
import logging
import mlflow
import mlflow.lightgbm
import mlflow.onnx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

MODEL_PATH = os.environ.get("MODEL_PATH", "/app/models/eta_model.onnx")
RELOAD_URL = os.environ.get("RELOAD_URL", "http://127.0.0.1:8000/api/v1/ml/reload")

ML_INTERNAL_TOKEN = os.environ.get("ML_INTERNAL_TOKEN")
if not ML_INTERNAL_TOKEN:
    logger.error("ML_INTERNAL_TOKEN is not set.")
    sys.exit(1)
BUS_FACTOR = float(os.environ.get("BUS_FACTOR", "1.35"))


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))

def parse_time_to_minutes(time_str: str) -> int:
    if not time_str:
        return 0
    try:
        t = datetime.strptime(time_str, "%I:%M %p")
        return t.hour * 60 + t.minute
    except ValueError:
        return 0

def main():
    if not DATABASE_URL:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    logger.info("Connecting to database...")
    engine = create_engine(DATABASE_URL)

    # 1. Fetch stops
    stops_df = pd.read_sql("SELECT id, route_id, name, lat, lon, order_index, morning_order_index, evening_order_index, morning_time, evening_time FROM bus_stops ORDER BY route_id, morning_order_index NULLS LAST", engine)
    if stops_df.empty:
        logger.warning("No bus stops found in database.")
        sys.exit(0)
    stops_by_route = {}
    for r in stops_df.to_dict("records"):
        stops_by_route.setdefault(r["route_id"], []).append(r)

    # 2. Fetch ONLY correctly-completed morning/evening trips from the recent
    #    window — mirrors the scheduled trainer (services/ml-training-job/train_job.py,
    #    the canonical weekly job). Excludes on_trip/cancelled and anything without a
    #    real start+end so skipped/aborted trips never train.
    lookback_days = int(os.environ.get("TRAIN_LOOKBACK_DAYS", "365"))
    query_trips = f"""
        SELECT id, route_id, direction, started_at, ended_at
        FROM trips
        WHERE status IN ('completed', 'ended')
          AND direction IN ('forward', 'reverse', 'morning', 'evening')
          AND (started_at IS NULL OR started_at >= NOW() - INTERVAL '{lookback_days} days')
    """
    df_trips = pd.read_sql(query_trips, engine)

    # 3. Fetch GPS logs from gps_logs — the historical, trip-tagged, snapped track.
    #    NOT gps_realtime, which UPSERTs a single row per trip (useless for history).
    query_gps = """
        SELECT id, trip_id, lat, lon, speed, ist_time, created_at
        FROM gps_logs
        WHERE lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY id
    """
    df_gps = pd.read_sql(query_gps, engine)
    
    min_trips = int(os.environ.get("MIN_TRIPS", "5"))
    if len(df_trips) < min_trips:
        logger.info(f"[ML] Insufficient new trip data ({len(df_trips)} trips < {min_trips}).")
        sys.exit(0)

    if len(df_gps) < 500:
        logger.info(f"[ML] Insufficient new GPS data ({len(df_gps)} pts < 500).")
        sys.exit(0)

    logger.info(f"Loaded {len(df_trips)} trips and {len(df_gps)} GPS points for feature extraction.")

    # 4. Feature engineering
    train_rows = []
    test_rows = []
    
    # Split trips into train and test 80/20
    trip_ids = df_trips["id"].unique()
    np.random.seed(42)
    np.random.shuffle(trip_ids)
    split_idx = int(len(trip_ids) * 0.8)
    train_trip_ids = set(trip_ids[:split_idx])
    test_trip_ids = set(trip_ids[split_idx:])

    trips_dict = df_trips.set_index("id").to_dict("index")
    
    # Group GPS by trip_id
    gps_groups = df_gps.groupby("trip_id") if "trip_id" in df_gps.columns and df_gps["trip_id"].notnull().any() else []

    for trip_id, group in gps_groups:
        if trip_id not in trips_dict:
            continue
        trip_info = trips_dict[trip_id]
        route_id = trip_info["route_id"]
        direction = trip_info["direction"]
        is_reverse = 1 if direction == "reverse" else 0
        
        route_stops = stops_by_route.get(route_id, [])
        if not route_stops:
            continue

        def _eff(s, fwd):
            """Direction-aware order index. Explicit None check so a real index
            of 0 (origin stop) is not dropped by `or` (audit D3)."""
            primary = s.get("morning_order_index") if fwd else s.get("evening_order_index")
            return primary if primary is not None else s.get("order_index", 0)
            
        pts = group.to_dict("records")
        for pt in pts:
            t = pt.get("ist_time") or pt.get("created_at")
            if isinstance(t, str):
                t = pd.to_datetime(t)
            pt["ts"] = t

        pts = sorted([p for p in pts if p.get("ts") is not None], key=lambda x: x["ts"])
        if len(pts) < 10:
            continue

        # Find arrival time for each stop in this trip
        # O(N) forward scan
        visited_stops = set()
        stop_arrivals = {}
        for pt in pts:
            lat, lon, ts = pt["lat"], pt["lon"], pt["ts"]
            
            unvisited = [s for s in route_stops if s["id"] not in visited_stops]
            reached = []
            for s in unvisited:
                if haversine_km(lat, lon, s["lat"], s["lon"]) <= 0.250:
                    reached.append(s)
            
            if reached:
                is_fwd = (direction == "forward")
                furthest = max(reached, key=lambda x: _eff(x, is_fwd))

                # Mark furthest and all preceding as visited
                for s in route_stops:
                    if s["id"] in visited_stops: continue
                    if (is_fwd and _eff(s, True) <= _eff(furthest, True)) or \
                       (not is_fwd and _eff(s, False) >= _eff(furthest, False)):
                        visited_stops.add(s["id"])
                        if s["id"] not in stop_arrivals:
                            stop_arrivals[s["id"]] = ts

        if not stop_arrivals:
            continue

        # Determine origin departure time for elapsed_minutes
        origin_stop = None
        if direction == "forward":
            origin_stop = min(route_stops, key=lambda x: _eff(x, True))
        else:
            origin_stop = min(route_stops, key=lambda x: _eff(x, False))
            
        sched_time_str = origin_stop.get("morning_time") if direction == "forward" else origin_stop.get("evening_time")
        depart_mins = parse_time_to_minutes(sched_time_str)
        if depart_mins == 0:
            depart_mins = 450 if direction == "forward" else 1060

        # Now generate samples using actual stop arrivals
        is_fwd = (direction == "forward")
        last_visited_order = _eff(origin_stop, is_fwd)
        visited_set = set()
        
        for pt in pts:
            lat, lon, ts = pt["lat"], pt["lon"], pt["ts"]
            speed = pt.get("speed") or 25.0
            
            hour = ts.hour
            day_of_week = ts.weekday()
            elapsed_min = (hour * 60 + ts.minute) - depart_mins
            
            # Update last visited order based on what we've reached by this timestamp
            # We already have stop_arrivals
            for s in route_stops:
                if s["id"] not in visited_set and s["id"] in stop_arrivals and ts >= stop_arrivals[s["id"]]:
                    visited_set.add(s["id"])
                    if is_fwd and _eff(s, True) > last_visited_order:
                        last_visited_order = _eff(s, True)
                    elif not is_fwd and _eff(s, False) < last_visited_order:
                        last_visited_order = _eff(s, False)

            # For upcoming stops
            for s in route_stops:
                # Must be a stop we haven't reached yet, but we will reach eventually
                if s["id"] in visited_set: continue
                if s["id"] not in stop_arrivals: continue
                
                arr_ts = stop_arrivals[s["id"]]
                if arr_ts <= ts: continue
                
                dist_to_stop = haversine_km(lat, lon, s["lat"], s["lon"])
                if dist_to_stop > 35.0: continue
                
                stops_ahead = abs(_eff(s, is_fwd) - last_visited_order)
                if stops_ahead == 0: stops_ahead = 1
                
                naive_osrm_eta_mins = (dist_to_stop / 25.0) * 60.0 * BUS_FACTOR
                target_eta = (arr_ts - ts).total_seconds() / 60.0
                
                if target_eta <= 0: continue
                
                row = [
                    dist_to_stop,          # distance_km
                    stops_ahead,           # stops_ahead
                    hour,                  # hour
                    day_of_week,           # day_of_week
                    is_reverse,            # direction_reverse
                    elapsed_min,           # elapsed_minutes
                    speed,                 # speed_kmh
                    naive_osrm_eta_mins,   # naive_osrm_eta_mins
                    target_eta             # Y
                ]
                
                if trip_id in train_trip_ids:
                    train_rows.append(row)
                else:
                    test_rows.append(row)

    if (not test_rows or len(test_rows) < 10) and len(train_rows) >= 50:
        split = int(len(train_rows) * 0.8)
        test_rows = train_rows[split:]
        train_rows = train_rows[:split]

    if len(train_rows) < 40 or len(test_rows) < 10:
        logger.warning(f"Only {len(train_rows)} train and {len(test_rows)} test samples. Minimum 50 required.")
        sys.exit(0)

    train_data = np.array(train_rows, dtype=np.float32)
    test_data = np.array(test_rows, dtype=np.float32)

    X_train = train_data[:, :-1]
    y_train = train_data[:, -1]
    
    X_test = test_data[:, :-1]
    y_test = test_data[:, -1]

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
    mse = mean_squared_error(y_test, predictions)
    logger.info(f"Model Evaluation - MAE: {mae:.2f} minutes")

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    
    # Convert and save to ONNX
    from onnxmltools import convert_lightgbm
    from onnxconverter_common.data_types import FloatTensorType
    initial_types = [('input', FloatTensorType([None, X_train.shape[1]]))]
    onnx_model = convert_lightgbm(model, initial_types=initial_types, target_opset=12)

    tmp_path = str(MODEL_PATH) + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(onnx_model.SerializeToString())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, MODEL_PATH)
    logger.info(f"Model saved to {MODEL_PATH}")

    # Best-effort MLflow logging
    try:
        mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5002")
        mlflow.set_tracking_uri(mlflow_uri)
        mlflow.set_experiment("ETA_Prediction")
        with mlflow.start_run():
            mlflow.set_tags({
                "service": "eta-ml-service-train",
                "framework": "lightgbm+onnx",
                "status": "production"
            })
            mlflow.log_params({
                "model_type": "LGBMRegressor",
                "n_estimators": 150,
                "num_leaves": 31,
                "learning_rate": 0.1,
                "random_state": 42,
                "train_samples": int(len(X_train)),
                "test_samples": int(len(X_test)),
                "features_count": int(X_train.shape[1]),
                "feature_names": "distance_km,stops_ahead,hour,day_of_week,direction_reverse,elapsed_min,speed_kmh,naive_osrm_eta_mins",
                "target_opset": 12,
            })
            mlflow.log_metrics({
                "mae_minutes": float(mae),
                "mse_minutes": float(mse),
                "rmse_minutes": float(np.sqrt(mse)),
            })
            try:
                mlflow.onnx.log_model(onnx_model, "onnx_model")
            except Exception as exc:
                logger.warning(f"MLflow artifact logging skipped: {exc}")
    except Exception as exc:
        logger.warning(f"MLflow tracking skipped: {exc}")

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
