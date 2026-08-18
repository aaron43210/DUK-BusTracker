"""
services/eta_engine.py — Hybrid LightGBM + OSRM × Bus-Factor ETA inference engine.

Priority order:
  1. LightGBM (9 features including osrm_eta_minutes) — highest accuracy after retraining
  2. OSRM duration × BUS_FACTOR — good real-road estimate without historical data
  3. Distance / speed heuristic — last resort when OSRM is also down
"""
import logging
import joblib
import numpy as np
from pathlib import Path
from services.osrm_client import get_osrm_route

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent.parent / "ml" / "model.pkl"

# Bus is ~35% slower than OSRM car-speed-limit estimate due to:
#   - Passenger boarding / alighting at stops
#   - Bus speed cap (~60 km/h vs car 80-100 km/h)
#   - Longer idling at signals vs a car
BUS_FACTOR = 1.35

_model = None  # loaded lazily at first inference call


def _load_model():
    global _model
    if MODEL_PATH.exists():
        try:
            _model = joblib.load(MODEL_PATH)
            logger.info("[ETA] LightGBM model loaded from %s", MODEL_PATH)
        except Exception:
            logger.exception("[ETA] Failed to load model; will use OSRM fallback")
            _model = None
    else:
        logger.warning("[ETA] model.pkl not found; using OSRM x bus-factor until retraining")


def force_reload_model():
    """Called by the background training loop to hot-swap the new model."""
    logger.info("[ETA] Forcing reload of the LightGBM model from disk...")
    _load_model()


def _heuristic_eta(distance_km: float, avg_speed_kmh: float = 25.0) -> float:
    """Simple distance/speed fallback ETA in minutes (last resort if OSRM is also down)."""
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
    elapsed_minutes: float = 0.0,
    speed_last_3: float = 25.0,
    **kwargs,
) -> dict:
    """
    Predict ETA in minutes from current bus position to target stop.

    Feature vector (9 features):
        distance_km, stops_remaining, hour_of_day, day_of_week,
        trip_direction, elapsed_minutes, speed_last_3,
        osrm_eta_minutes, from_osrm_flag

    Returns {"eta_minutes": float, "confidence": str, "method": str,
             "osrm_eta_minutes": float}
    """
    global _model
    if _model is None:
        _load_model()

    # ── 1. OSRM route: real road distance + duration in one call ─────────────
    route_info    = await get_osrm_route(bus_lat, bus_lon, target_stop_lat, target_stop_lon)
    distance_km   = route_info["distance_m"] / 1000.0
    osrm_eta_mins = (route_info["duration_s"] / 60.0) * BUS_FACTOR  # bus-adjusted estimate
    from_osrm_flag = int(route_info["from_osrm"])

    # ── 2. LightGBM inference (9 features) ───────────────────────────────────
    if _model is not None:
        features = np.array([[
            distance_km,
            stops_remaining,
            hour_of_day,
            day_of_week,
            trip_direction,
            elapsed_minutes,
            speed_last_3,
            osrm_eta_mins,    # road-network estimate as primary smart feature
            from_osrm_flag,   # 1 if OSRM responded, 0 if heuristic fallback
        ]])
        try:
            eta_minutes = float(_model.predict(features)[0])
            eta_minutes = max(0.5, round(eta_minutes, 1))
            confidence  = "high" if (distance_km < 15 and route_info["from_osrm"]) else "medium"
            return {
                "eta_minutes":      eta_minutes,
                "confidence":       confidence,
                "method":           "lgbm+osrm",
                "osrm_eta_minutes": round(osrm_eta_mins, 1),
            }
        except Exception:
            logger.exception("[ETA] Model inference failed; falling back to OSRM estimate")

    # ── 3. OSRM × bus factor fallback (much better than raw distance/speed) ──
    if route_info["from_osrm"]:
        return {
            "eta_minutes":      round(osrm_eta_mins, 1),
            "confidence":       "medium",
            "method":           "osrm_bus_factor",
            "osrm_eta_minutes": round(osrm_eta_mins, 1),
        }

    # ── 4. Pure distance/speed heuristic (absolute last resort) ──────────────
    eta_minutes = _heuristic_eta(distance_km, speed_last_3 or 25.0)
    return {
        "eta_minutes":      eta_minutes,
        "confidence":       "low",
        "method":           "heuristic",
        "osrm_eta_minutes": round(osrm_eta_mins, 1),
    }
