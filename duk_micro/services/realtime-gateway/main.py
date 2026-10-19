# services/realtime-gateway/main.py
"""
Realtime Gateway — WebSocket server for the PWA live bus feed.

Provides two main functions:
  1. Subscribes to Redis "bus:broadcast" and forwards GPS updates
     to connected PWA clients in real time.
  2. Handles authenticated JSON-RPC requests from the PWA and
     proxies supported actions to internal services.

Connection flow:
  PWA
    ↓
  WebSocket authentication
    ↓
  Redis subscription
    ↓
  GPS updates → PWA
    or
  JSON-RPC request → Internal service → PWA response

Endpoints:
  WS  /api/v1/ws/bus — Primary PWA WebSocket endpoint
  WS  /ws/bus        — Legacy WebSocket alias
  GET /health        — Redis health check
"""
import asyncio
import logging
import os
import json
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.redis_client import get_redis
from libs.duk_common.auth import decode_token
from libs.duk_common.settings import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="realtime-gateway", version="2.1.0", docs_url=None, redoc_url=None)
settings = get_settings()

# Clients (PWA, admin dashboard) only receive on this socket and never send, so
# an inbound-idle timeout used to drop every client every 2 minutes. Instead, when
# nothing has arrived for this long we send an app-level keepalive; a dead
# connection then surfaces as a send failure and is closed.
_KEEPALIVE_INTERVAL_SECONDS = 30
# Max bytes per inbound WebSocket message
_MAX_MESSAGE_BYTES = 4096

#  Process-wide Redis fan-out
# A SINGLE subscriber to "bus:broadcast" runs for the whole process. It parses each
# message once and fans it out to every connected client's private asyncio.Queue.
# This replaces the old per-client pubsub()+reader model, which opened one Redis
# connection and one listen() loop per browser — that does not scale to thousands
# of concurrent PWA clients on a single worker.
_client_queues: set["asyncio.Queue"] = set()
_broadcaster_task: "asyncio.Task | None" = None
# Bound each client's queue so a slow/stalled browser cannot grow memory without
# limit; GPS updates are ephemeral, so dropping the oldest when full is safe.
_CLIENT_QUEUE_MAXSIZE = 256


async def _broadcast_loop():
    """
    Single process-wide task: subscribes once to Redis "bus:broadcast", parses
    each message once, stamps the outbound envelope, and fans it out to every
    registered client queue. Reconnects on Redis failure.
    """
    while True:
        pubsub = None
        try:
            redis = await get_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe("bus:broadcast")
            logger.info("[REALTIME] Process-wide bus:broadcast subscriber started")
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    parsed = json.loads(message["data"])
                    if "event" not in parsed:
                        parsed["event"] = "gps.update"
                    if "type" not in parsed:
                        parsed["type"] = "gps"
                except Exception as e:
                    logger.warning("[REALTIME] Dropping malformed redis message: %s", e)
                    continue
                # Fan out to every connected client; never let one slow client
                # block the others.
                for q in list(_client_queues):
                    try:
                        q.put_nowait(parsed)
                    except asyncio.QueueFull:
                        # Drop the oldest buffered update to make room for the newest.
                        try:
                            q.get_nowait()
                            q.put_nowait(parsed)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[REALTIME] Broadcast subscriber error, retrying in 2s: %s", e)
            await asyncio.sleep(2)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe("bus:broadcast")
                    await pubsub.aclose()
                except Exception:
                    pass


@app.on_event("startup")
async def _startup():
    """Launch the single process-wide broadcast subscriber."""
    global _broadcaster_task
    if _broadcaster_task is None:
        _broadcaster_task = asyncio.create_task(_broadcast_loop())

#  Internal service URLs 
# Note: In single-process (mega_gateway) mode these are unreachable HTTP addresses.
# RPC calls in that mode are handled by direct ASGI routing, not HTTP.
# These URLs are only relevant if running services as separate Docker containers.
GATEWAY_SELF_URL = os.environ.get("GATEWAY_SELF_URL", "http://127.0.0.1:8000")
INTERNAL_SERVICES = {
    "auth": f"{GATEWAY_SELF_URL}/api/v1/auth",
    "tracking": f"{GATEWAY_SELF_URL}/api/v1",
    "notifications": f"{GATEWAY_SELF_URL}/api/v1/notifications",
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

    If running under mega_gateway, uses ASGITransport for direct in-memory
    routing (avoiding HTTP loopback latency and failures)
    The shared client provides:connection pooling, keep-alive connections, lower latency, fewer TCP connections.
    """
    global _CLIENT
    if _CLIENT is None:
        import sys
        
        asgi_app = None
        if "mega_gateway" in sys.modules:
            asgi_app = sys.modules["mega_gateway"].app
            logger.info("[RPC] Mega-gateway detected, enabling direct ASGI bypass (no HTTP loopbacks)")

        if asgi_app:
            _CLIENT = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=asgi_app),
                base_url="http://mega-gateway"
            )
        else:
            _CLIENT = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=1.0, read=5.0, write=2.0, pool=2.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
            )
    return _CLIENT


@app.on_event("shutdown")
async def _shutdown():
    """Close the HTTP client and stop the broadcaster gracefully on shutdown."""
    global _broadcaster_task
    if _broadcaster_task is not None:
        _broadcaster_task.cancel()
        try:
            await _broadcaster_task
        except (asyncio.CancelledError, Exception):
            pass
        _broadcaster_task = None
    if _CLIENT:
        await _CLIENT.aclose()


async def proxy_rpc_request(action: str, payload: dict, token: Optional[str] = None) -> dict:
    """
    Forwards a JSON-RPC action from the WebSocket to an internal HTTP service.

    Input:
        action  - Action name (e.g., "get_latest", "get_stops")
        payload - Parameters to pass (GET = query params, POST = JSON body)
        token   - Optional JWT token from the WebSocket query param

    Output:
        dict - The JSON response from the internal service, or {"error": "..."} on failure

    If the action isn't in RPC_ROUTES, returns {"error": "Unknown action"}.
    """
    route = RPC_ROUTES.get(action)
    if not route:
        return {"error": "Unknown action"}

    base_url = INTERNAL_SERVICES[route["service"]]
    # If ASGITransport is used, replace base_url host to match the client's base_url
    if "mega_gateway" in sys.modules:
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        base_url = f"http://mega-gateway{parsed.path}"
        
    url = f"{base_url}{route['path']}"

    params    = payload if route["method"] == "GET" else None
    json_data = payload if route["method"] in ("POST", "PUT") else None
    headers   = {"Authorization": f"Bearer {token}"} if token else {}

    client = _get_client()
    try:
        if route["method"] == "GET":
            resp = await client.get(url, params=params, headers=headers)
        else:
            resp = await client.post(url, json=json_data, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("[RPC] HTTP error %s for %s", e.response.status_code, url)
        return {"error": f"Internal service error: {e.response.status_code}"}
    except Exception as e:
        logger.error("[RPC] Proxy error for %s: %s", url, e)
        return {"error": "Internal service unavailable"}


def _extract_ws_token(websocket: WebSocket, query_token: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve the client's JWT for a WebSocket handshake WITHOUT taking it from the
    query string (query strings leak into access logs, proxies and browser
    history). Resolution order:
      1. Sec-WebSocket-Protocol subprotocol — the client offers
         ["bearer", "<jwt>"]; we return the token and the subprotocol to echo
         back on accept (browsers can't set arbitrary WS headers, so this is the
         standard way to carry a credential).
      2. Cookie (`access_token` or `admin_session`) — sent automatically on a
         same-origin handshake; used by the cookie-based admin dashboard.
      3. Legacy `?token=` query param — transitional fallback only.
    Returns (token, chosen_subprotocol).
    """
    offered = [p.strip() for p in (websocket.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    if offered and offered[0] == "bearer" and len(offered) >= 2:
        # Echo "bearer" back as the negotiated subprotocol so the handshake
        # completes; the second value is the credential.
        return offered[1], "bearer"

    cookie_token = websocket.cookies.get("access_token") or websocket.cookies.get("admin_session")
    if cookie_token:
        return cookie_token, None

    if query_token:
        return query_token, None

    return None, None


@app.websocket("/api/v1/ws/bus")
@app.websocket("/ws/bus")
async def client_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None, alias="token"),
):
    """
    Main WebSocket handler for connected PWA clients.

    Authentication:
        The client authenticates with a valid JWT carried out-of-band of the URL
        query string — via the Sec-WebSocket-Protocol subprotocol ("bearer",
        "<JWT>") or a session cookie. A legacy ?token= query param is still
        accepted as a transitional fallback. The connection is rejected (4003)
        before data flows if the token is missing, invalid, or revoked.

    Input:  WebSocket connection from an authenticated browser
    Output: Continuous stream of {event: "gps.update", data: {...}} messages

    Two concurrent tasks run for each connected client:
      - queue_reader: drains the per-client queue and pushes to the browser
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
    # Real admins authenticate via their normal JWT; there is deliberately no
    # shared-static-secret shortcut here (a single ADMIN_TOKEN over the wire would
    # be a standing admin bypass if leaked).
    token, chosen_subprotocol = _extract_ws_token(websocket, token)
    payload = None
    if token:
        payload = decode_token(token, settings.SECRET_KEY, settings.ALGORITHM)

    if not payload:
        # Accept the connection first so the browser receives the custom close code
        await websocket.accept(subprotocol=chosen_subprotocol)
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
            await websocket.accept(subprotocol=chosen_subprotocol)
            await websocket.close(code=4001)  # 4001 = custom "Unauthorized"
            logger.warning("[REALTIME] Rejected revoked token for user %s", payload.get("sub"))
            return
    except Exception as e:
        # Redis is temporarily unavailable. Use 4008 (custom "service unavailable")
        # instead of 4001 ("revoked") so the client retries rather than logs out.
        # 4001 means "your session was explicitly revoked" — sending it on a Redis
        # blip caused every connected user to be logged out during outages.
        if payload.get("jti"):
            logger.error("[REALTIME] Blocklist check failed — rejecting connection (infra error, not revocation): %s", e)
            await websocket.accept(subprotocol=chosen_subprotocol)
            await websocket.close(code=4008)  # 4008 = service unavailable / retry
            return
        logger.warning("[REALTIME] Blocklist check failed for token without jti — allowing: %s", e)

    await websocket.accept(subprotocol=chosen_subprotocol)

    user_id = payload.get("sub", "unknown")
    logger.info("[REALTIME] Client connected: user=%s", user_id)

    # Register this client with the process-wide broadcaster. Each client owns a
    # private bounded queue; the single _broadcast_loop task fills it.
    queue: asyncio.Queue = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
    _client_queues.add(queue)

    async def queue_reader():
        """Background task: drains this client's queue and pushes updates to browser."""
        while True:
            parsed = await queue.get()
            try:
                await websocket.send_json(parsed)
            except Exception as e:
                logger.warning(f"[REALTIME] Send failed for user {user_id}: {e}")
                break

    reader_task = asyncio.create_task(queue_reader())
    try:
        while True:
            try:
                text = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=_KEEPALIVE_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                if reader_task.done():
                    # The sender hit an error — the client is gone.
                    break
                # Routed through the client's queue so only queue_reader ever
                # writes to the socket. No lat/lon and type != "gps", so existing
                # clients ignore it. A full queue means data is flowing anyway.
                try:
                    queue.put_nowait({"type": "ping", "event": "keepalive"})
                except asyncio.QueueFull:
                    pass
                continue

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
                    result = await proxy_rpc_request(action, payload_data, token=token)
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
        _client_queues.discard(queue)




@app.get("/health")
async def health():
    """
    Health check endpoint.
    """
    redis_status = "error"
    try:
        redis = await get_redis()
        if await redis.ping():
            redis_status = "ok"
    except Exception:
        pass

    status_code = 200 if redis_status == "ok" else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if status_code == 200 else "degraded", "service": "realtime-gateway", "redis": redis_status}
    )
