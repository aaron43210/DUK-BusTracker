"""
ml/train.py — LightGBM ETA model training script.
Run offline (not in the web server) after accumulating enough GPS data.

Usage:
    cd backend
    python ml/train.py --db /path/to/gps_tracker.db --stops-json ml/stops.json

The script reads from the existing SQLite DB (or PostgreSQL via psycopg2)
and engineers features from completed trip GPS logs.
"""
import argparse
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path

# Need to import osrm_client which is in the parent directory
sys.path.append(str(Path(__file__).resolve().parent.parent))
from services.osrm_client import get_osrm_distance_m_sync

import joblib
import numpy as np
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "model.pkl"


# ── Haversine ─────────────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    return get_osrm_distance_m_sync(lat1, lon1, lat2, lon2) / 1000.0


# ── Load stops ────────────────────────────────────────────────────────────────
DEFAULT_STOPS = [
    {"id": 0,  "name": "Central Polytechnic",           "lat": 8.5350, "lon": 76.9908},
    {"id": 1,  "name": "Vattiyoorkavu Jn",              "lat": 8.5241, "lon": 76.9882},
    {"id": 2,  "name": "Manjadimoodu",                  "lat": 8.5233, "lon": 76.9852},
    {"id": 3,  "name": "Maruthankuzhi",                 "lat": 8.5138, "lon": 76.9790},
    {"id": 4,  "name": "Sasthamangalam",                "lat": 8.5129, "lon": 76.9710},
    {"id": 5,  "name": "Vellayambalam",                 "lat": 8.5116, "lon": 76.9626},
    {"id": 6,  "name": "Thampanoor",                    "lat": 8.4875, "lon": 76.9528},
    {"id": 7,  "name": "Chandrasekharan Nair Stadium",  "lat": 8.5046, "lon": 76.9512},
    {"id": 8,  "name": "PMG",                           "lat": 8.5084, "lon": 76.9500},
    {"id": 9,  "name": "Pattom",                        "lat": 8.5186, "lon": 76.9424},
    {"id": 10, "name": "Kesavadasapuram",               "lat": 8.5297, "lon": 76.9386},
    {"id": 11, "name": "Ulloor",                        "lat": 8.5299, "lon": 76.9288},
    {"id": 12, "name": "Pongumoodu",                    "lat": 8.5402, "lon": 76.9246},
    {"id": 13, "name": "Sreekaryam",                    "lat": 8.5488, "lon": 76.9172},
    {"id": 14, "name": "Chavadimukku",                  "lat": 8.5510, "lon": 76.9114},
    {"id": 15, "name": "Karyavattom",                   "lat": 8.5665, "lon": 76.8912},
    {"id": 16, "name": "IIITMK",                        "lat": 8.5588, "lon": 76.8790},
    {"id": 17, "name": "Technopark Front",              "lat": 8.5578, "lon": 76.8763},
    {"id": 18, "name": "Kazhakuttam",                   "lat": 8.5668, "lon": 76.8743},
    {"id": 19, "name": "Pallipuram",                    "lat": 8.5979, "lon": 76.8546},
    {"id": 20, "name": "Digital University Kerala",     "lat": 8.6158, "lon": 76.8527},
]


def load_stops(stops_json: str | None) -> list[dict]:
    if stops_json and Path(stops_json).exists():
        with open(stops_json) as f:
            return json.load(f)
    return DEFAULT_STOPS


def find_nearest_stop(lat, lon, stops, threshold_km=0.4):
    best, best_d = None, float("inf")
    for s in stops:
        d = haversine_km(lat, lon, s["lat"], s["lon"])
        if d < best_d:
            best_d = d
            best = s
    if best and best_d <= threshold_km:
        return best, best_d
    return None, None


# ── Feature engineering ───────────────────────────────────────────────────────
def extract_features(rows: list[dict], stops: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """
    Engineer training features from raw gps_logs rows.
    Returns (X, y) arrays.
    """
    logger.info("Extracting features from %d GPS rows", len(rows))

    # ── Segment rows into trips by looking for >30min gaps ────────────────────
    trips: list[list] = []
    current_trip: list = []

    for row in rows:
        try:
            if isinstance(row["server_time"], str):
                t = datetime.strptime(row["server_time"], "%Y-%m-%d %H:%M:%S")
            else:
                t = row["server_time"] # already a datetime object
        except Exception:
            continue
        if current_trip:
            prev_t = current_trip[-1][0]
            gap = (t - prev_t).total_seconds() / 60
            if gap > 30:
                if len(current_trip) >= 10:
                    trips.append(current_trip)
                current_trip = []
        current_trip.append((t, row["lat"], row["lon"]))

    if len(current_trip) >= 10:
        trips.append(current_trip)

    logger.info("Detected %d trip segments", len(trips))

    # ── Feature extraction ────────────────────────────────────────────────────
    X_rows, y_rows = [], []

    for trip in trips:
        trip_start = trip[0][0]
        
        # ── Strictly filter out any unofficial trips ──────────────────────────
        # Only learn from Morning (6 AM - 11 AM) and Evening (5 PM - 9 PM) trips
        if not (6 <= trip_start.hour < 11 or 17 <= trip_start.hour < 21):
            continue

        is_evening = trip_start.hour >= 17

        # Compute speed between consecutive pings
        speeds = [0.0]
        for i in range(1, len(trip)):
            dt_sec = (trip[i][0] - trip[i - 1][0]).total_seconds()
            if dt_sec > 0:
                d = haversine_km(trip[i - 1][1], trip[i - 1][2], trip[i][1], trip[i][2])
                speeds.append(d / dt_sec * 3600)  # km/h
            else:
                speeds.append(0.0)

        for i, (t, lat, lon) in enumerate(trip):
            speed_last_3 = float(np.mean(speeds[max(0, i - 3) : i + 1]))
            elapsed_min  = (t - trip_start).total_seconds() / 60

            # For each future stop, compute actual time-to-stop (target)
            for stop in stops:
                # Find when the bus actually reached this stop
                reached_at = None
                for j in range(i + 1, len(trip)):
                    d = haversine_km(trip[j][1], trip[j][2], stop["lat"], stop["lon"])
                    if d <= 0.35:
                        reached_at = trip[j][0]
                        break

                if reached_at is None:
                    continue  # bus never reached this stop in this trip segment

                actual_eta = (reached_at - t).total_seconds() / 60
                if actual_eta <= 0 or actual_eta > 120:
                    continue  # filter outliers

                dist_to_stop = haversine_km(lat, lon, stop["lat"], stop["lon"])

                # Count stops between bus and target (approximate via order_index)
                bus_nearest, _ = find_nearest_stop(lat, lon, stops)
                bus_order = bus_nearest["id"] if bus_nearest else 0
                stop_order = stop["id"]
                stops_remaining = abs(stop_order - bus_order)

                baseline_eta = (dist_to_stop / max(25.0, speed_last_3)) * 60
                
                X_rows.append([
                    dist_to_stop,       # distance_km
                    stops_remaining,    # stops_remaining
                    t.hour,             # hour_of_day
                    t.weekday(),        # day_of_week
                    int(is_evening),    # trip_direction
                    elapsed_min,        # elapsed_minutes
                    speed_last_3,       # speed_last_3
                    baseline_eta,       # baseline_eta (replaces leaked historical_avg_tt)
                ])
                y_rows.append(actual_eta)

    logger.info("Generated %d training samples", len(X_rows))
    return np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.float32)


# ── Training ──────────────────────────────────────────────────────────────────
def train(rows: list[dict], stops_json: str | None = None, validate_only: bool = False):
    stops  = load_stops(stops_json)
    X, y   = extract_features(rows, stops)

    if len(X) < 50:
        logger.error("Not enough samples (%d) to train. Collect more GPS data first.", len(X))
        return

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

    params = {
        "objective":       "regression",
        "metric":          "mae",
        "num_leaves":      63,
        "learning_rate":   0.05,
        "n_estimators":    500,
        "feature_fraction":0.8,
        "bagging_fraction":0.8,
        "bagging_freq":    5,
        "verbose":         -1,
        "min_child_samples": 5,
    }

    model = lgb.LGBMRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(100)],
    )

    y_pred = model.predict(X_val)
    mae    = mean_absolute_error(y_val, y_pred)
    r2     = r2_score(y_val, y_pred)
    logger.info("Validation — MAE: %.2f min  R²: %.4f", mae, r2)

    feature_names = [
        "distance_km", "stops_remaining", "hour_of_day", "day_of_week",
        "trip_direction", "elapsed_minutes", "speed_last_3", "baseline_eta",
    ]
    importance = dict(zip(feature_names, model.feature_importances_))
    logger.info("Feature importance: %s", importance)

    if not validate_only:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, MODEL_PATH)
        logger.info("Model saved to %s", MODEL_PATH)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train LightGBM ETA model")
    parser.add_argument("--db",           required=True, help="Path to gps_tracker SQLite DB")
    parser.add_argument("--stops-json",   default=None,  help="Optional stops JSON file")
    parser.add_argument("--validate-only",action="store_true", help="Evaluate without saving model")
    args = parser.parse_args()
    
    # Standalone SQLite fallback for CLI usage
    import sqlite3
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT server_time, lat, lon FROM gps_logs WHERE lat IS NOT NULL ORDER BY id"
    ).fetchall()
    conn.close()
    
    train([dict(r) for r in rows], args.stops_json, args.validate_only)
