"""
services/eta_engine.py — LightGBM ETA inference engine.
Loads the trained model at startup. Falls back to a simple speed-based
heuristic if the model file doesn't exist yet (before first training run).
"""
import logging
import joblib
import numpy as np
from pathlib import Path
from services.osrm_client import get_osrm_distance_m

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent.parent / "ml" / "model.pkl"

_model = None  # loaded lazily at first inference call


def _load_model():
    global _model
    if MODEL_PATH.exists():
        try:
            _model = joblib.load(MODEL_PATH)
            logger.info("[ETA] LightGBM model loaded from %s", MODEL_PATH)
        except Exception:
            logger.exception("[ETA] Failed to load model; will use heuristic fallback")
            _model = None
    else:
        logger.warning("[ETA] model.pkl not found; using heuristic fallback until training completes")


def force_reload_model():
    """Called by the background training loop to hot-swap the new model."""
    logger.info("[ETA] Forcing reload of the LightGBM model from disk...")
    _load_model()


def _heuristic_eta(distance_km: float, avg_speed_kmh: float = 25.0) -> float:
    """Simple distance/speed fallback ETA in minutes."""
    if avg_speed_kmh <= 0:
        avg_speed_kmh = 25.0
    return round((distance_km / avg_speed_kmh) * 60, 1)


async def predict_eta(
    bus_lat: float,
    bus_lon: float,
    target_stop_lat: float,
    target_stop_lon: float,
    stops_remaining: int,
    hour_of_day: int,
    day_of_week: int,
    trip_direction: int,      # 0 = morning, 1 = evening
    elapsed_minutes: float,
    speed_last_3: float,      # moving avg speed km/h
) -> dict:
    """
    Predict ETA in minutes from current bus position to target stop.
    Returns {"eta_minutes": float, "confidence": str, "method": str}
    """
    global _model
    if _model is None:
        _load_model()

    distance_m = await get_osrm_distance_m(bus_lat, bus_lon, target_stop_lat, target_stop_lon)
    distance_km = distance_m / 1000.0

    if _model is not None:
        features = np.array([[
            distance_km,
            stops_remaining,
            hour_of_day,
            day_of_week,
            trip_direction,
            elapsed_minutes,
            speed_last_3,
            _heuristic_eta(distance_km, speed_last_3),
        ]])
        try:
            eta_minutes = float(_model.predict(features)[0])
            eta_minutes = max(0.5, round(eta_minutes, 1))   # clamp to at least 30s
            confidence  = "high" if distance_km < 10 else "medium"
            return {"eta_minutes": eta_minutes, "confidence": confidence, "method": "lgbm"}
        except Exception:
            logger.exception("[ETA] Model inference failed; falling back to heuristic")

    # Fallback
    eta_minutes = _heuristic_eta(distance_km, speed_last_3 or 25.0)
    return {"eta_minutes": eta_minutes, "confidence": "low", "method": "heuristic"}
