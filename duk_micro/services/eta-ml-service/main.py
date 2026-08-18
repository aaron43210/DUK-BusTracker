# services/eta-ml-service/main.py
"""
ETA ML Service — Hybrid LightGBM + OSRM inference engine.
Replicates monolith services/eta_engine.py logic.
"""
import httpx
import joblib
import logging
import math
import os
import numpy as np
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="eta-ml-service")

MODEL_PATH     = os.environ.get("MODEL_PATH", "/app/models/eta_model.pkl")
OSRM_BASE_URL  = os.environ.get("OSRM_URL", "http://osrm-backend:5000")
BUS_FACTOR     = 1.35
IST            = ZoneInfo("Asia/Kolkata")
IST_OFFSET     = timedelta(hours=5, minutes=30)

model = None

@app.on_event("startup")
async def load_model():
    global model
    try:
        if os.path.exists(MODEL_PATH):
            model = joblib.load(MODEL_PATH)
            logger.info("[ETA] Loaded LightGBM model from %s", MODEL_PATH)
        else:
            logger.warning("[ETA] Model not found at %s. Will fallback to OSRM.", MODEL_PATH)
    except Exception as e:
        logger.warning("[ETA] Could not load ML model: %s", e)

@app.post("/api/v1/ml/reload")
async def reload_model():
    await load_model()
    return {"success": True}

class EtaRequest(BaseModel):
    lat: float
    lon: float
    speed_kmh: float
    direction: str
    stops_ahead: int
    hour: int
    minute: int
    day_of_week: int
    is_late: int
    late_by_minutes: int


# ── OSRM Helpers (internal to minimize shared lib coupling) ───────────────────
async def _get_osrm_route(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    url = f"{OSRM_BASE_URL}/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=false"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                return {
                    "distance_m": float(data["routes"][0]["distance"]),
                    "duration_s": float(data["routes"][0]["duration"]),
                    "from_osrm": True,
                }
    except Exception as e:
        logger.debug("[ETA] OSRM error: %s", e)

    # Haversine fallback
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2)
    dist_m = R * 2 * math.asin(math.sqrt(a))
    dur_s  = (dist_m / 1000.0 / 25.0) * 3600
    return {"distance_m": dist_m, "duration_s": dur_s, "from_osrm": False}


@app.post("/api/v1/ml/predict")
async def predict_eta(req: EtaRequest):
    # Determine destination coordinates
    if req.direction == "forward":
        dest_lat, dest_lon = 8.6158, 76.8527
    else:
        dest_lat, dest_lon = 8.5350, 76.9908

    route_info = await _get_osrm_route(req.lat, req.lon, dest_lat, dest_lon)
    distance_km = route_info["distance_m"] / 1000.0
    osrm_eta_mins = (route_info["duration_s"] / 60.0) * BUS_FACTOR
    from_osrm_flag = 1 if route_info["from_osrm"] else 0

    # ML Inference
    if model is not None:
        features = np.array([[
            distance_km,
            req.stops_ahead,
            req.hour,
            req.day_of_week,
            1 if req.direction == "reverse" else 0,
            (req.hour * 60 + req.minute) - (450 if req.direction == "forward" else 1060), # elapsed_minutes
            req.speed_kmh or 25.0,
            osrm_eta_mins,
            from_osrm_flag,
        ]])
        try:
            eta_minutes = float(model.predict(features)[0])
            eta_minutes = max(0.5, round(eta_minutes, 1))
            now_ist = datetime.now(timezone.utc) + IST_OFFSET
            arr_time = now_ist + timedelta(minutes=eta_minutes)
            return {
                "eta_minutes": eta_minutes,
                "arrival_time": arr_time.strftime("%-I:%M %p"),
                "source": "lgbm+osrm"
            }
        except Exception as e:
            logger.error("[ETA] Inference failed: %s", e)

    # Fallback to OSRM x Bus Factor
    now_ist = datetime.now(timezone.utc) + IST_OFFSET
    arr_time = now_ist + timedelta(minutes=osrm_eta_mins)
    return {
        "eta_minutes": round(osrm_eta_mins, 1),
        "arrival_time": arr_time.strftime("%-I:%M %p"),
        "source": "osrm_bus_factor"
    }


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": model is not None}
