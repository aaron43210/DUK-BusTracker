# services/eta-ml-service/main.py
"""
ETA ML Service — Predicts bus arrival times using a hybrid ML + OSRM approach.

How ETA prediction works:
  Step 1: Try the ML model (LightGBM, trained on historical trip data)
          - Features: lat, lon, speed, direction, stops_ahead, hour, day_of_week, delay
          - Output: predicted minutes until arrival at the next stop
          - If model file doesn't exist (fresh install), falls back to OSRM

  Step 2: OSRM fallback (if ML model unavailable or fails)
          - Uses road routing to calculate travel time
          - Applies BUS_FACTOR (1.35×) to account for stop dwells + traffic
          - Always works because OSRM runs locally

Why two methods?
  The ML model learns patterns over time (e.g., "traffic on NH66 at 8 AM adds 12 min").
  OSRM is used as a reliable fallback and is accurate enough for most cases.

The model file is loaded once at startup from MODEL_PATH.
If retrained, call POST /api/v1/ml/reload to hot-reload without restarting.

Endpoints:
  POST   /api/v1/ml/predict   — Predict ETA for a given bus position
  POST   /api/v1/ml/reload    — Hot-reload the ML model from disk
  GET    /health              — Health check
"""
import httpx
import onnxruntime as ort
import logging
import math
import os
import secrets
import numpy as np
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Header
from typing import Optional
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="eta-ml-service")

MODEL_PATH     = os.environ.get("MODEL_PATH", "/app/models/eta_model.onnx")
OSRM_BASE_URL  = os.environ.get("OSRM_URL", "http://osrm-backend:5000")
BUS_FACTOR     = 1.35
IST            = ZoneInfo("Asia/Kolkata")
IST_OFFSET     = timedelta(hours=5, minutes=30)

# Internal-only token used to gate the /ml/reload endpoint.
# Must be set via ML_INTERNAL_TOKEN env var. If not set, reload is disabled.
_ML_INTERNAL_TOKEN: str = os.environ.get("ML_INTERNAL_TOKEN", "")

model = None

_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=OSRM_BASE_URL,
            timeout=httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client

@app.on_event("shutdown")
async def _shutdown_client():
    if _client:
        await _client.aclose()


@app.on_event("startup")
async def load_model():
    global model
    try:
        if os.path.exists(MODEL_PATH):
            model = ort.InferenceSession(MODEL_PATH)
            logger.info("[ETA] Loaded ONNX ML model from %s", MODEL_PATH)
        else:
            logger.warning("[ETA] Model not found at %s. Will fallback to OSRM.", MODEL_PATH)
    except Exception as e:
        logger.warning("[ETA] Could not load ML model: %s", e)

@app.post("/api/v1/ml/reload")
async def reload_model(
    x_internal_token: Optional[str] = Header(default=None, alias="X-Internal-Token"),
):
    """
    Hot-reload the ML model from disk.

    This endpoint is restricted to internal callers (ml-training-job service).
    Requires the X-Internal-Token header to match ML_INTERNAL_TOKEN env var.
    """
    if not _ML_INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Model reload is disabled: ML_INTERNAL_TOKEN not configured.",
        )
    if not x_internal_token or not secrets.compare_digest(
        x_internal_token.encode(), _ML_INTERNAL_TOKEN.encode()
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    await load_model()
    return {"success": True}

class EtaRequest(BaseModel):
    lat: float
    lon: float
    target_lat: float
    target_lon: float
    speed_kmh: float
    direction: str
    stops_ahead: int
    hour: int
    minute: int
    day_of_week: int
    is_late: int
    late_by_minutes: int


#  OSRM Helpers (internal to minimize shared lib coupling) 
async def _get_osrm_route(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    url = f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=false"
    try:
        resp = await _get_client().get(url)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            return {
                "distance_m": float(data["routes"][0]["distance"]),
                "duration_s": float(data["routes"][0]["duration"]),
                "from_osrm": True,
            }
    except Exception as e:
        logger.debug("[ETA] OSRM error: %s", e)

    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    dist_m = R * 2 * math.asin(math.sqrt(a))
    dur_s = (dist_m / 1000.0 / 25.0) * 3600
    return {"distance_m": dist_m, "duration_s": dur_s, "from_osrm": False}


@app.post("/api/v1/ml/predict")
async def predict_eta(req: EtaRequest):
    # Use the requested target coordinates instead of a hardcoded route end
    route_info = await _get_osrm_route(req.lat, req.lon, req.target_lat, req.target_lon)
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
        ]], dtype=np.float32)
        try:
            input_name = model.get_inputs()[0].name
            eta_minutes = float(model.run(None, {input_name: features})[0][0][0])
            eta_minutes = max(0.5, round(eta_minutes, 1))
            now_ist = datetime.now(timezone.utc) + IST_OFFSET
            arr_time = now_ist + timedelta(minutes=eta_minutes)
            return {
                "eta_minutes": eta_minutes,
                "arrival_time": arr_time.strftime("%-I:%M %p"),
                "source": "onnx+osrm"
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
