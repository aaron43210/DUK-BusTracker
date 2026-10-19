#!/usr/bin/env python3
# services/ml-training-job/train_job.py
"""
ML Training Job for ETA Model.
Replicates monolith ml_scheduler.py and ml/train.py.
Triggered by Redis pub/sub channel 'ml:retrain_requested' (sent by admin-service)
or runs periodically.
"""
import asyncio
import httpx
import logging
import math
import os
import uuid
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from redis.asyncio import Redis
from sklearn.metrics import mean_absolute_error, mean_squared_error
import lightgbm as lgb
import mlflow
import mlflow.lightgbm
import mlflow.onnx
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, Float, DateTime, String, Date

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")
REDIS_URL    = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
MODEL_PATH   = Path(os.environ.get("MODEL_PATH", "/app/models/eta_model.onnx"))
ETA_ML_URL   = os.environ.get("ETA_ML_URL", "http://127.0.0.1:8000")
ML_INTERNAL_TOKEN = os.environ.get("ML_INTERNAL_TOKEN")
if not ML_INTERNAL_TOKEN:
    raise RuntimeError("ML_INTERNAL_TOKEN is required")
BUS_FACTOR = float(os.environ.get("BUS_FACTOR", "1.35"))

# ── Training data-quality knobs (audit E1 / data filtering) ─────────────────────
# How far back to pull trips. Default 365 days (covers full historical school year).
TRAIN_LOOKBACK_DAYS = int(os.environ.get("TRAIN_LOOKBACK_DAYS", "365"))
# Recency weighting: a sample's weight halves every RECENCY_HALFLIFE_DAYS of trip
# age, so recent completed trips dominate the fit while older ones still inform it.
RECENCY_HALFLIFE_DAYS = float(os.environ.get("RECENCY_HALFLIFE_DAYS", "30"))
# Minimum metres between two kept GPS points — collapses stop-dwell clusters and
# jitter so we don't train on thousands of duplicate near-identical samples.
GPS_DEDUP_M = float(os.environ.get("GPS_DEDUP_M", "10"))
# A genuinely-completed trip must have driven from near its origin stop to near its
# final stop. This rejects skipped / aborted / partial trips whose status slipped
# through as completed.
TRIP_COVERAGE_M = float(os.environ.get("TRIP_COVERAGE_M", "800"))
# Schedule: 'weekly' (Sunday 02:00 IST) or 'monthly' (1st of month 02:00 IST).
TRAIN_INTERVAL = os.environ.get("TRAIN_INTERVAL", "weekly").lower()
# Minimum completed trips required to trigger training (default 5)
MIN_TRIPS = int(os.environ.get("MIN_TRIPS", "5"))
# Minimum GPS points required to trigger training (default 100)
MIN_GPS_ROWS = int(os.environ.get("MIN_GPS_ROWS", "100"))

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class GpsLog(Base):
    # Train on gps_logs — the historical, trip-tagged, snapped track written by the
    # ingestion bridge. NOT gps_realtime: that table UPSERTs one row per trip
    # (trip_id is UNIQUE there), so it holds a single live point per trip and is
    # useless for training. This was the "wrong place" the data was read from.
    __tablename__ = "gps_logs"
    id          = Column(Integer, primary_key=True)
    trip_id     = Column(Integer, nullable=True, index=True)
    lat         = Column(Float)
    lon         = Column(Float)
    server_time = Column("created_at", DateTime(timezone=True))
    gps_time    = Column(DateTime(timezone=True), nullable=True)
    speed       = Column(Float, nullable=True)

class Trip(Base):
    __tablename__ = "trips"
    id              = Column(Integer, primary_key=True)
    route_id        = Column(Integer)
    date            = Column(Date, nullable=True)
    direction       = Column(String(50))
    started_at      = Column(DateTime(timezone=True))
    ended_at        = Column(DateTime(timezone=True))
    late_by_minutes = Column(Integer)
    status          = Column(String(50))

class BusStop(Base):
    __tablename__ = "bus_stops"
    id          = Column(Integer, primary_key=True)
    route_id    = Column(Integer)
    name        = Column(String(200))
    lat         = Column(Float)
    lon         = Column(Float)
    order_index           = Column(Integer)
    morning_order_index   = Column(Integer, nullable=True)
    evening_order_index   = Column(Integer, nullable=True)
    morning_time = Column(String(10), nullable=True)
    evening_time = Column(String(10), nullable=True)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

def parse_time_to_minutes(time_str: str) -> int:
    if not time_str:
        return 0
    try:
        t = datetime.strptime(time_str, "%I:%M %p")
        return t.hour * 60 + t.minute
    except ValueError:
        return 0


def _dedup_points(pts, min_sep_m: float):
    """Drop consecutive points closer than min_sep_m — removes stop-dwell clusters
    and GPS jitter so a stationary bus doesn't flood training with duplicates."""
    out = []
    for p in pts:
        if not out or haversine_km(out[-1]["lat"], out[-1]["lon"], p["lat"], p["lon"]) * 1000.0 >= min_sep_m:
            out.append(p)
    return out


def _trip_is_complete(pts, route_stops, eff_order, coverage_m: float) -> bool:
    """A correctly-completed trip must pass near BOTH its origin and final stop.
    Rejects skipped/aborted/partial trips whose status slipped through."""
    if not route_stops:
        return False
    origin = min(route_stops, key=eff_order)
    dest = max(route_stops, key=eff_order)
    near_origin = min(haversine_km(p["lat"], p["lon"], origin["lat"], origin["lon"]) for p in pts) * 1000.0
    near_dest = min(haversine_km(p["lat"], p["lon"], dest["lat"], dest["lon"]) for p in pts) * 1000.0
    return near_origin <= coverage_m and near_dest <= coverage_m


async def run_training():
    logger.info("[ML] Fetching data for training...")
    
    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch stops
            res = await db.execute(select(BusStop).order_by(BusStop.route_id, BusStop.morning_order_index.nullslast()))
            stops_raw = res.scalars().all()
            if not stops_raw:
                logger.warning("[ML] No bus stops found in database.")
                return
                
            stops_by_route = {}
            for s in stops_raw:
                stops_by_route.setdefault(s.route_id, []).append({
                    "id": s.id,
                    "route_id": s.route_id,
                    "lat": s.lat,
                    "lon": s.lon,
                    "order_index":         s.order_index,
                    "morning_order_index": s.morning_order_index if s.morning_order_index is not None else s.order_index,
                    "evening_order_index": s.evening_order_index if s.evening_order_index is not None else s.order_index,
                    "morning_time": s.morning_time,
                    "evening_time": s.evening_time
                })

            # 2. Fetch ONLY correctly-completed morning/evening trips from the recent
            #    window. Excludes on_trip (still running), cancelled, and anything
            #    without a real start+end — i.e. skipped/aborted trips never train.
            cutoff = datetime.now(timezone.utc) - timedelta(days=TRAIN_LOOKBACK_DAYS)
            res = await db.execute(
                select(Trip).where(
                    Trip.status.in_(['completed', 'ended']),
                    Trip.direction.in_(['forward', 'reverse', 'morning', 'evening']),
                    or_(Trip.date >= cutoff.date(), Trip.started_at >= cutoff, Trip.date.is_(None)),
                )
            )
            trips_raw = res.scalars().all()
            if len(trips_raw) < MIN_TRIPS:
                logger.info("[ML] Insufficient new trip data (%d trips < %d). Skipping training.", len(trips_raw), MIN_TRIPS)
                return
                
            trips_dict = {
                t.id: {
                    "id": t.id,
                    "route_id": t.route_id,
                    "direction": t.direction,
                    "started_at": t.started_at,
                    "ended_at": t.ended_at
                } for t in trips_raw
            }

            # 3. Fetch GPS logs for these trips
            trip_ids = list(trips_dict.keys())
            res = await db.execute(select(GpsLog).where(GpsLog.lat.isnot(None), GpsLog.lon.isnot(None), GpsLog.trip_id.in_(trip_ids)).order_by(GpsLog.id))
            logs = res.scalars().all()
            
            if len(logs) < MIN_GPS_ROWS:
                logger.info("[ML] Insufficient new GPS data (%d rows < %d). Skipping training.", len(logs), MIN_GPS_ROWS)
                return
                
            gps_by_trip = {}
            for l in logs:
                t = l.gps_time or l.server_time
                if t and t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if not t:
                    continue
                gps_by_trip.setdefault(l.trip_id, []).append({
                    "lat": l.lat,
                    "lon": l.lon,
                    "ts": t,
                    "speed": l.speed or 25.0
                })
        except Exception:
            await db.rollback()
            raise

    logger.info("[ML] Loaded %d trips and %d GPS points. Extracting features...", len(trips_dict), len(logs))
    
    train_rows = []
    test_rows = []
    
    trip_id_list = list(trips_dict.keys())
    np.random.seed(42)
    np.random.shuffle(trip_id_list)
    split_idx = int(len(trip_id_list) * 0.8)
    train_trip_ids = set(trip_id_list[:split_idx])
    test_trip_ids = set(trip_id_list[split_idx:])
    
    for trip_id, pts in gps_by_trip.items():
        if trip_id not in trips_dict:
            continue
        trip_info = trips_dict[trip_id]
        route_id = trip_info["route_id"]
        raw_dir = (trip_info.get("direction") or "").lower().strip()
        direction = "reverse" if raw_dir in ("reverse", "evening", "e") else "forward"
        is_reverse = 1 if direction == "reverse" else 0

        # Recency weight for this trip's samples — recent completed trips dominate
        # the fit while older history still contributes (user request).
        started = trip_info.get("started_at")
        if started is not None and getattr(started, "tzinfo", None) is None:
            started = started.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - started).total_seconds() / 86400.0 if started else 0.0
        recency_w = 0.5 ** (max(0.0, age_days) / RECENCY_HALFLIFE_DAYS)

        route_stops = stops_by_route.get(route_id, [])
        if not route_stops:
            continue
            
        pts = sorted(pts, key=lambda x: x["ts"])
        if len(pts) < 10:
            continue

        # Explicit None-check order index (a real index of 0 must not be dropped).
        is_fwd = (direction == "forward")
        def _eff(s, fwd=is_fwd):
            primary = s.get("morning_order_index") if fwd else s.get("evening_order_index")
            return primary if primary is not None else s.get("order_index", 0)

        # Drop dwell/jitter duplicates, then require the trip to actually span the
        # whole route (origin→destination) — skips partial/aborted runs.
        pts = _dedup_points(pts, GPS_DEDUP_M)
        if len(pts) < 10:
            continue
        if not _trip_is_complete(pts, route_stops, _eff, TRIP_COVERAGE_M):
            logger.info("[ML] Trip %s rejected: GPS does not cover full route (skipped/partial).", trip_id)
            continue

        visited_stops = set()
        stop_arrivals = {}
        for pt in pts:
            lat, lon, ts = pt["lat"], pt["lon"], pt["ts"]
            
            unvisited = [s for s in route_stops if s["id"] not in visited_stops]
            reached = []
            for s in unvisited:
                if haversine_km(lat, lon, s["lat"], s["lon"]) <= 0.250: # 250m
                    reached.append(s)
                    
            if reached:
                furthest = max(reached, key=_eff)

                for s in route_stops:
                    if s["id"] in visited_stops: continue
                    if (is_fwd and _eff(s) <= _eff(furthest)) or \
                       (not is_fwd and _eff(s) >= _eff(furthest)):
                        visited_stops.add(s["id"])
                        if s["id"] not in stop_arrivals:
                            stop_arrivals[s["id"]] = ts
                            
        if not stop_arrivals:
            continue

        # Determine origin departure time for elapsed_minutes
        origin_stop = min(route_stops, key=_eff)
            
        sched_time_str = origin_stop.get("morning_time") if direction == "forward" else origin_stop.get("evening_time")
        depart_mins = parse_time_to_minutes(sched_time_str)
        if depart_mins == 0:
            depart_mins = 450 if direction == "forward" else 1060
            
        last_visited_order = _eff(origin_stop)
        visited_set = set()
        
        for pt in pts:
            lat, lon, ts = pt["lat"], pt["lon"], pt["ts"]
            speed = pt["speed"]
            
            hour = ts.hour
            day_of_week = ts.weekday()
            elapsed_min = (hour * 60 + ts.minute) - depart_mins
            
            for s in route_stops:
                if s["id"] not in visited_set and s["id"] in stop_arrivals and ts >= stop_arrivals[s["id"]]:
                    visited_set.add(s["id"])
                    if is_fwd and _eff(s) > last_visited_order:
                        last_visited_order = _eff(s)
                    elif not is_fwd and _eff(s) < last_visited_order:
                        last_visited_order = _eff(s)

            for s in route_stops:
                if s["id"] in visited_set: continue
                if s["id"] not in stop_arrivals: continue
                
                arr_ts = stop_arrivals[s["id"]]
                if arr_ts <= ts: continue
                
                dist_to_stop = haversine_km(lat, lon, s["lat"], s["lon"])
                if dist_to_stop > 35.0: continue
                
                stops_ahead = abs(_eff(s) - last_visited_order)
                if stops_ahead == 0: stops_ahead = 1
                
                naive_osrm_eta_mins = (dist_to_stop / 25.0) * 60.0 * BUS_FACTOR
                target_eta = (arr_ts - ts).total_seconds() / 60.0
                
                if target_eta <= 0: continue
                
                row = [
                    dist_to_stop,          # 0: distance_km
                    stops_ahead,           # 1: stops_ahead
                    hour,                  # 2: hour
                    day_of_week,           # 3: day_of_week
                    is_reverse,            # 4: direction_reverse
                    elapsed_min,           # 5: elapsed_minutes
                    speed,                 # 6: speed_kmh
                    naive_osrm_eta_mins,   # 7: naive_osrm_eta_mins
                    target_eta,            # Y: actual target (2nd-last column)
                    recency_w,             # sample weight (last column; not a feature/Y)
                ]
                
                if trip_id in train_trip_ids:
                    train_rows.append(row)
                else:
                    test_rows.append(row)

    # Drop exact-duplicate samples so repeated identical observations don't bias
    # the fit or leak between the train/test split. Key on features+target only
    # (exclude the trailing recency-weight column).
    def _dedup_rows(rows):
        if not rows:
            return []
        seen, out = set(), []
        for r in rows:
            key = tuple(round(v, 4) for v in r[:-1])
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    train_rows = _dedup_rows(train_rows) or []
    test_rows = _dedup_rows(test_rows) or []

    if (not test_rows or len(test_rows) < 10) and len(train_rows) >= 50:
        split = int(len(train_rows) * 0.8)
        test_rows = train_rows[split:]
        train_rows = train_rows[:split]

    if len(train_rows) < 40 or len(test_rows) < 10:
        logger.warning("[ML] Only %d train and %d test samples generated. Minimum 50 required.", len(train_rows), len(test_rows))
        return

    train_data = np.array(train_rows, dtype=np.float32)
    test_data = np.array(test_rows, dtype=np.float32)

    # Last column is the per-sample recency weight; second-last is the target Y.
    X_train = train_data[:, :-2]
    y_train = train_data[:, -2]
    w_train = train_data[:, -1]
    # Normalise so the most-recent trip has weight 1.0. Keeps the *relative* recency
    # weighting but avoids a uniformly tiny absolute weight mass, which can fall below
    # LightGBM's min_sum_hessian_in_leaf and make it refuse to split (constant model).
    wmax = float(w_train.max()) if w_train.size else 1.0
    if wmax > 0:
        w_train = w_train / wmax

    X_test = test_data[:, :-2]
    y_test = test_data[:, -2]

    logger.info("[ML] Training model on %d samples (recency-weighted)...", len(X_train))
    model = lgb.LGBMRegressor(
        n_estimators=150,
        num_leaves=31,
        learning_rate=0.1,
        random_state=42
    )
    model.fit(X_train, y_train, sample_weight=w_train)

    predictions = model.predict(X_test)
    mae = mean_absolute_error(y_test, predictions)
    mse = mean_squared_error(y_test, predictions)
    logger.info("[ML] Model Evaluation - MAE: %.2f minutes", mae)

    # Save ONNX model directly to disk first
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    logger.info("[ML] ONNX Model safely saved to %s", MODEL_PATH)

    # MLflow tracking (best-effort; failures do not block model deployment)
    try:
        mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5002")
        mlflow.set_tracking_uri(mlflow_uri)
        mlflow.set_experiment("ETA_Prediction")
        with mlflow.start_run():
            mlflow.set_tags({
                "service": "ml-training-job",
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
                "recency_halflife_days": float(RECENCY_HALFLIFE_DAYS),
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
                logger.warning("[ML] MLflow artifact logging skipped: %s", exc)
    except Exception as exc:
        logger.warning("[ML] MLflow tracking skipped: %s", exc)

    # Signal eta-ml-service to reload
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            headers = {"X-Internal-Token": ML_INTERNAL_TOKEN}
            await client.post(f"{ETA_ML_URL}/api/v1/ml/reload", headers=headers)
        logger.info("[ML] Signaled eta-ml-service to reload model")
    except Exception as e:
        logger.error("[ML] Failed to signal reload: %s", e)

async def _attempt_training(redis: Redis, lock_key: str, lock_ttl: int):
    lock_token = str(uuid.uuid4())
    acquired = await redis.set(lock_key, lock_token, nx=True, ex=lock_ttl)
    if not acquired:
        logger.info("[ML] Training job already running (lock active).")
        return

    try:
        await run_training()
    finally:
        # Atomic release only if we still hold the lock
        release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await redis.eval(release_script, 1, lock_key, lock_token)

async def weekly_training_loop(redis: Redis):
    """Background task to run training on the configured schedule at 02:00 IST.
    TRAIN_INTERVAL='weekly' → every Sunday; 'monthly' → the 1st of each month."""
    IST_OFFSET = timedelta(hours=5, minutes=30)
    monthly = TRAIN_INTERVAL == "monthly"
    logger.info("[ML] Training schedule: %s at 02:00 IST", "monthly (1st)" if monthly else "weekly (Sunday)")
    while True:
        now_ist = datetime.now(timezone.utc) + IST_OFFSET
        due = (now_ist.day == 1) if monthly else (now_ist.weekday() == 6)
        if due and now_ist.hour == 2 and now_ist.minute == 0:
            logger.info("[ML] Scheduled %s training cycle started.", "monthly" if monthly else "weekly")
            try:
                await _attempt_training(redis, "ml:training:lock", 600)
            except Exception as e:
                logger.exception("[ML] Scheduled training failed: %s", e)
            await asyncio.sleep(60)  # Prevent multiple triggers within the same minute
        await asyncio.sleep(30)


async def main():
    logger.info("[ML] Training job starting. Running on weekly schedule.")
    
    redis = Redis.from_url(REDIS_URL)
    
    # Start the weekly schedule loop in the background (or foreground)
    # We await it directly so the script stays alive forever
    try:
        await weekly_training_loop(redis)
    finally:
        await redis.aclose()

if __name__ == "__main__":
    asyncio.run(main())
