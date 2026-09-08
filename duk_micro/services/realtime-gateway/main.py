# services/realtime-gateway/main.py
"""
Realtime Gateway — WebSocket server for the PWA's live location feed.

This service does two things simultaneously for every connected browser:

  1. PUSH: Subscribes to Redis "bus:broadcast" channel and immediately
     forwards every GPS update to all connected PWA clients.
     This is how the bus marker moves on the map in real-time.

  2. PULL (JSON-RPC): The PWA can also send requests over the same
     WebSocket (e.g., "get_latest", "get_stops") and this service
     proxies them to the appropriate internal microservice.
     This avoids the PWA needing to open multiple HTTP connections.

Connection lifecycle:
    PWA opens WS → realtime-gateway accepts → subscribes to Redis pub/sub
    GPS device sends data → gps-ingestion publishes to Redis "bus:broadcast"
    realtime-gateway receives from Redis → wraps in {event: "gps.update", data: ...}
    → forwards to all connected PWA browsers instantly

Endpoints:
    WS /api/v1/ws/bus  — Primary WebSocket endpoint (via Nginx)
    WS /ws/bus         — Legacy alias
    GET /health        — Health check
"""
import asyncio
import logging
import os
import json
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.redis_client import get_redis
from libs.duk_common.auth import decode_token
from libs.duk_common.security import constant_time_compare
from libs.duk_common.settings import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="realtime-gateway", version="2.1.0")
settings = get_settings()

# Max seconds to wait for any client message before closing an idle connection
_IDLE_TIMEOUT_SECONDS = 120
# Max bytes per inbound WebSocket message
_MAX_MESSAGE_BYTES = 4096

#  Internal service URLs 
# Note: In single-process (mega_gateway) mode these are unreachable HTTP addresses.
# RPC calls in that mode are handled by direct ASGI routing, not HTTP.
# These URLs are only relevant if running services as separate Docker containers.
INTERNAL_SERVICES = {
    "auth": "http://auth-service:8004/api/v1/auth",
    "tracking": "http://tracking-service:8005/api/v1",
    "notifications": "http://notification-api:8007/api/v1/notifications",
}

#  RPC routing table 
# Maps WebSocket action names → which internal service + HTTP path to call.
# NOTE: register and verify are intentionally NOT here — authentication
# endpoints must go through the HTTP auth service where rate limiting
# is applied. Exposing them via WebSocket would bypass HTTP rate limits.
RPC_ROUTES = {
    # Tracking service
    "get_stops":       {"service": "tracking",      "method": "GET",  "path": "/stops"},
    "get_routes":      {"service": "tracking",      "method": "GET",  "path": "/routes"},
    "get_latest":      {"service": "tracking",      "method": "GET",  "path": "/latest"},
    "get_trip_state":  {"service": "tracking",      "method": "GET",  "path": "/trip_state"},
    "get_eta":         {"service": "tracking",      "method": "GET",  "path": "/eta"},
    # Notification service
    "save_preferences": {"service": "notifications", "method": "POST", "path": "/preferences"},
}

#  Shared HTTP client (reused across all RPC calls) 
_CLIENT: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """
    Returns the shared async HTTP client for proxying RPC requests.

    Input:  nothing
    Output: httpx.AsyncClient — reused singleton to avoid per-call overhead

    Timeouts are tight (1s connect, 5s read) since these are internal calls.
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=1.0, read=5.0, write=2.0, pool=2.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
        )
    return _CLIENT


@app.on_event("shutdown")
async def _shutdown():
    """Close the HTTP client gracefully on server shutdown."""
    if _CLIENT:
        await _CLIENT.aclose()


async def proxy_rpc_request(action: str, payload: dict) -> dict:
    """
    Forwards a JSON-RPC action from the WebSocket to an internal HTTP service.

    Input:
        action  - Action name (e.g., "get_latest", "get_stops")
        payload - Parameters to pass (GET = query params, POST = JSON body)

    Output:
        dict - The JSON response from the internal service, or {"error": "..."} on failure

    If the action isn't in RPC_ROUTES, returns {"error": "Unknown action"}.
    """
    route = RPC_ROUTES.get(action)
    if not route:
        return {"error": "Unknown action"}

    base_url = INTERNAL_SERVICES[route["service"]]
    url = f"{base_url}{route['path']}"

    params    = payload if route["method"] == "GET" else None
    json_data = payload if route["method"] in ("POST", "PUT") else None

    client = _get_client()
    try:
        if route["method"] == "GET":
            resp = await client.get(url, params=params)
        else:
            resp = await client.post(url, json=json_data)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("[RPC] HTTP error %s for %s", e.response.status_code, url)
        return {"error": f"Internal service error: {e.response.status_code}"}
    except Exception as e:
        logger.error("[RPC] Proxy error for %s: %s", url, e)
        return {"error": "Internal service unavailable"}


@app.websocket("/api/v1/ws/bus")
@app.websocket("/ws/bus")
async def client_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None, alias="token"),
):
    """
    Main WebSocket handler for connected PWA clients.

    Authentication:
        The client must pass a valid JWT as a query parameter:
            wss://host/api/v1/ws/bus?token=<JWT>
        The connection is rejected (403) before accept() if the token is
        missing, invalid, or revoked.

    Input:  WebSocket connection from an authenticated browser
    Output: Continuous stream of {event: "gps.update", data: {...}} messages

    Two concurrent tasks run for each connected client:
      - redis_reader: Listens to Redis "bus:broadcast" and pushes to browser
      - Main loop:    Reads incoming messages from browser and handles RPC calls

    Message format from browser (RPC request):
        {"id": "req-123", "action": "get_latest", "payload": {}}

    Message format to browser (GPS update):
        {"event": "gps.update", "data": {"lat": 8.55, "lon": 76.90, ...}}

    Message format to browser (RPC response):
        {"id": "req-123", "status": "success", "data": {...}}
        {"id": "req-123", "status": "error", "detail": "..."}
    """
    # --- Authenticate before accepting the WebSocket ---
    payload = None
    if token:
        if constant_time_compare(token, settings.ADMIN_TOKEN):
            payload = {"sub": "admin", "role": "admin"}
        else:
            payload = decode_token(token, settings.SECRET_KEY, settings.ALGORITHM)

    if not payload:
        # Reject unauthenticated connections before the WS handshake completes
        await websocket.close(code=4003)  # 4003 = custom "Forbidden"
        logger.warning("[REALTIME] Rejected unauthenticated WS connection")
        return

    # Check blocklist for the token's JTI
    try:
        redis = await get_redis()
        from libs.duk_common.security import TokenBlocklist
        bl = TokenBlocklist(redis)
        jti = payload.get("jti", "")
        if jti and await bl.is_revoked(jti):
            await websocket.close(code=4001)  # 4001 = custom "Unauthorized"
            logger.warning("[REALTIME] Rejected revoked token for user %s", payload.get("sub"))
            return
    except Exception:
        pass

    await websocket.accept()
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe("bus:broadcast")

    user_id = payload.get("sub", "unknown")
    logger.info("[REALTIME] Client connected: user=%s", user_id)

    async def redis_reader():
        """Background task: reads from Redis and pushes GPS updates to browser."""
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    parsed = json.loads(message["data"])
                    parsed["event"] = "gps.update"
                    parsed["type"] = "gps"
                    await websocket.send_json(parsed)
                except Exception:
                    break

    reader_task = asyncio.create_task(redis_reader())
    try:
        while True:
            try:
                text = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=_IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.info("[REALTIME] Idle timeout for user=%s, closing", user_id)
                break

            if len(text) > _MAX_MESSAGE_BYTES:
                logger.warning("[REALTIME] Oversized message from user=%s (%d bytes), ignoring", user_id, len(text))
                continue

            try:
                msg = json.loads(text)
                req_id = msg.get("id")
                action = msg.get("action")
                payload_data = msg.get("payload", {})

                if req_id and action:
                    logger.debug("[RPC] Request %s -> %s", req_id, action)
                    result = await proxy_rpc_request(action, payload_data)
                    if "error" in result:
                        await websocket.send_json({"id": req_id, "status": "error", "detail": result["error"]})
                    else:
                        await websocket.send_json({"id": req_id, "status": "success", "data": result})
            except json.JSONDecodeError:
                pass  # Ignore non-JSON messages from browser
    except WebSocketDisconnect:
        logger.info("[REALTIME] Client disconnected: user=%s", user_id)
    finally:
        reader_task.cancel()
        await pubsub.unsubscribe("bus:broadcast")


@app.get("/health")
async def health():
    """
    Health check endpoint.

    Output: {"status": "ok", "service": "realtime-gateway"}
    Used by the systemd service and load balancers to verify the service is alive.
    """
    return {"status": "ok", "service": "realtime-gateway"}
