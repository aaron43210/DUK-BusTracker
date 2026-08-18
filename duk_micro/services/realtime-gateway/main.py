# services/realtime-gateway/main.py
"""
realtime-gateway — Holds tens of thousands of idle WebSocket connections from React/PWA.
Subscribes to Redis `bus:broadcast` and fans out.
Also handles bi-directional JSON-RPC over WebSocket, proxying to internal HTTP microservices.
"""
import asyncio
import logging
import os
import json
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.redis_client import get_redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="realtime-gateway", version="2.0.0")

# Internal service DNS names (Docker Compose / Kubernetes)
INTERNAL_SERVICES = {
    "auth": "http://auth-service:8004/api/v1/auth",
    "tracking": "http://tracking-service:8005/api/v1/tracking",
    "notifications": "http://notification-api:8007/api/v1/notifications"
}

# Mapping frontend 'actions' to internal HTTP routes
RPC_ROUTES = {
    # Auth
    "login": {"service": "auth", "method": "POST", "path": "/login"},
    "verify": {"service": "auth", "method": "POST", "path": "/verify"},
    
    # Tracking
    "get_stops": {"service": "tracking", "method": "GET", "path": "/stops"},
    "get_routes": {"service": "tracking", "method": "GET", "path": "/routes"},
    "get_latest": {"service": "tracking", "method": "GET", "path": "/latest"},
    "get_trip_state": {"service": "tracking", "method": "GET", "path": "/trip_state"},
    "get_eta": {"service": "tracking", "method": "GET", "path": "/eta"},
    
    # Notifications
    "save_preferences": {"service": "notifications", "method": "POST", "path": "/preferences"},
}


async def proxy_rpc_request(action: str, payload: dict) -> dict:
    """Proxy the RPC action to the underlying internal REST service."""
    route = RPC_ROUTES.get(action)
    if not route:
        return {"error": "Unknown action"}
        
    base_url = INTERNAL_SERVICES[route["service"]]
    url = f"{base_url}{route['path']}"
    
    # For GET requests, convert payload to query params
    params = payload if route["method"] == "GET" else None
    json_data = payload if route["method"] in ["POST", "PUT"] else None
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            if route["method"] == "GET":
                resp = await client.get(url, params=params)
            else:
                resp = await client.post(url, json=json_data)
                
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[RPC] HTTP error {e.response.status_code} for {url}")
            return {"error": f"Internal service error: {e.response.status_code}"}
        except Exception as e:
            logger.error(f"[RPC] Proxy error for {url}: {e}")
            return {"error": "Internal service unavailable"}


@app.websocket("/ws/bus")
async def client_websocket(websocket: WebSocket):
    await websocket.accept()
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe("bus:broadcast")
    
    logger.debug("[REALTIME] Client connected to gateway")
    
    async def redis_reader():
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    # Wrap broadcast events in a common format
                    raw_data = message["data"]
                    # If it's just raw json from ingestion, wrap it so WSClient knows it's an event
                    parsed = json.loads(raw_data)
                    wrapped = {"event": "gps.update", "data": parsed}
                    await websocket.send_json(wrapped)
                except Exception:
                    break

    reader_task = asyncio.create_task(redis_reader())
    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                req_id = msg.get("id")
                action = msg.get("action")
                payload = msg.get("payload", {})
                
                if req_id and action:
                    logger.debug(f"[RPC] Request {req_id} -> {action}")
                    result = await proxy_rpc_request(action, payload)
                    
                    if "error" in result:
                        await websocket.send_json({"id": req_id, "status": "error", "detail": result["error"]})
                    else:
                        await websocket.send_json({"id": req_id, "status": "success", "data": result})
            except json.JSONDecodeError:
                pass # ignore garbage
                
    except WebSocketDisconnect:
        logger.debug("[REALTIME] Client disconnected")
        reader_task.cancel()
        await pubsub.unsubscribe("bus:broadcast")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "realtime-gateway"}
