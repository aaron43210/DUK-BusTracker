# libs/duk_common/mega_gateway.py
"""
Mega-Gateway — single-process ASGI router for all microservices.

In production, all 7 microservices run inside a single Python/uvicorn process.
This file is the entry point. It loads each service's FastAPI app and routes
incoming requests to the correct service based on the URL path.

Why single process (not Docker containers)?
  - Simpler to run and monitor on a small Linux server
  - Shared memory (no inter-process serialization overhead)
  - One systemd service to manage, not 7

Routing table:
  /api/v1/gps*          → gps-ingestion     (GPS device data intake)
  /ws/device*           → gps-ingestion     (Hardware WebSocket connection)
  /api/v1/ws/bus*       → realtime-gateway  (PWA WebSocket — live location)
  /api/v1/auth*         → auth-service      (OTP login)
  /api/v1/stops*        → tracking-service  (Stop list, ETA, GPS state)
  /api/v1/latest*       → tracking-service
  /api/v1/trip_state*   → tracking-service
  ... (see routing code below for full list)
  /api/v1/admin*        → admin-service     (Admin dashboard)
  /api/v1/notifications → notification-api  (Push notifications)
  /api/v1/ml*           → eta-ml-service    (ML-based ETA predictions)
"""
import sys
import os
import asyncio
import importlib.util
import contextlib
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from dotenv import load_dotenv
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("mega_gateway")

# Load environment variables from .env file before any service starts
from pathlib import Path
base_dir = Path(__file__).resolve().parent
load_dotenv(base_dir / ".env")

# Make sure libs/ is importable (e.g., libs.duk_common.geocoding)
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))


def load_app(path: str, module_name: str):
    """
    Dynamically loads a service's main.py and returns its FastAPI app object.

    Input:
        path        - Absolute filesystem path to the service's main.py
        module_name - Unique Python module name to register it under (e.g., "gps_ingestion")

    Output:
        FastAPI app — the service's ASGI application, ready to receive requests

    Why dynamic loading?
        Each service has its own local modules (database_local.py, models_local.py).
        We load them in isolation and clear conflicting names from sys.modules before each
        load to prevent one service's "models_local" overwriting another's.
    """
    dir_path = os.path.dirname(path)
    
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    
    sys.modules[module_name] = module
    
    # Clear local modules to prevent collisions between microservices
    for k in ["database_local", "models_local"]:
        if k in sys.modules:
            del sys.modules[k]
    
    original_path = sys.path.copy()
    sys.path.insert(0, dir_path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path = original_path
    
    return getattr(module, "app")


#  Load all microservice apps 
logger.info("Loading Microservices...")
base = os.path.dirname(__file__)

gps_app        = load_app(os.path.join(base, "services/gps-ingestion/main.py"),    "gps_ingestion")
realtime_app   = load_app(os.path.join(base, "services/realtime-gateway/main.py"), "realtime_gateway")
auth_app       = load_app(os.path.join(base, "services/auth-service/main.py"),     "auth_service")
tracking_app   = load_app(os.path.join(base, "services/tracking-service/main.py"), "tracking_service")
admin_app      = load_app(os.path.join(base, "services/admin-service/main.py"),    "admin_service")
notification_app = load_app(os.path.join(base, "services/notification-api/main.py"), "notification_api")
eta_app        = load_app(os.path.join(base, "services/eta-ml-service/main.py"),   "eta_ml_service")



# Global exit stack to manage lifespans of all sub-apps
_lifespan_stack: contextlib.AsyncExitStack | None = None
_startup_complete = asyncio.Event()
_startup_lock = asyncio.Lock()

async def app(scope, receive, send):
    """
    Main ASGI entry point — routes every request to the correct microservice.

    Input:
        scope   - ASGI scope dict (contains path, headers, method, etc.)
        receive - ASGI receive callable
        send    - ASGI send callable

    Output: nothing (delegates to sub-app which writes the response)

    Routing is done by URL path prefix matching, identical to how Nginx/Traefik
    would route to separate containers in a Docker deployment.
    """
    global _lifespan_stack
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                async with _startup_lock:
                    try:
                        _lifespan_stack = contextlib.AsyncExitStack()
                        for sub_app in (gps_app, realtime_app, auth_app, tracking_app, admin_app, notification_app, eta_app):
                            await _lifespan_stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
                        _startup_complete.set()
                        await send({"type": "lifespan.startup.complete"})
                    except Exception as e:
                        logger.error("Startup failed: %s", e, exc_info=True)
                        await send({"type": "lifespan.startup.failed", "message": str(e)})
                        return
            elif message["type"] == "lifespan.shutdown":
                try:
                    if _lifespan_stack:
                        await _lifespan_stack.aclose()
                    await send({"type": "lifespan.shutdown.complete"})
                except Exception as e:
                    logger.error("Shutdown failed: %s", e)
                    await send({"type": "lifespan.shutdown.failed", "message": str(e)})
                finally:
                    pass
        return

    assert scope["type"] in ("http", "websocket")
    path = scope["path"]

    if path.startswith("/api/v1/gps") or path.startswith("/api/v1/ws/device"):
        # Hardware GPS device → gps-ingestion service
        await gps_app(scope, receive, send)

    elif path.startswith("/api/v1/ws/bus"):
        # PWA WebSocket for live location updates → realtime-gateway
        await realtime_app(scope, receive, send)

    elif path.startswith("/api/v1/auth"):
        # OTP login / registration → auth-service
        await auth_app(scope, receive, send)

    elif (
        path.startswith("/api/v1/tracking") or
        path.startswith("/api/v1/stops") or
        path.startswith("/api/v1/routes") or
        path.startswith("/api/v1/latest") or
        path.startswith("/api/v1/trip_state") or
        path.startswith("/api/v1/eta") or
        path.startswith("/api/v1/route_history") or
        path.startswith("/api/v1/route_geometry") or
        path.startswith("/api/v1/route_segment") or
        path.startswith("/api/v1/current_trace") or
        path.startswith("/api/v1/trip_trace") or
        path.startswith("/api/v1/snap_route") or
        path.startswith("/api/v1/history")
    ):
        # All GPS tracking, stop data, ETA endpoints → tracking-service
        await tracking_app(scope, receive, send)

    elif path == "/api/v1/admin/refresh_route_geometry":
        # Exception to the /api/v1/admin → admin-service rule: this endpoint is
        # defined in tracking-service (it recomputes the planned-route geometry
        # the tracking/snap layer owns) and is admin-gated there. Route it to
        # tracking_app so the admin dashboard's call doesn't 404 on admin-service.
        await tracking_app(scope, receive, send)

    elif path.startswith("/api/v1/admin"):
        # Admin dashboard endpoints → admin-service
        await admin_app(scope, receive, send)

    elif path.startswith("/api/v1/notifications") or path.startswith("/api/v1/suggestion"):
        # Push notifications + user suggestions → notification-api
        await notification_app(scope, receive, send)

    elif path.startswith("/api/v1/ml"):
        # ML-based ETA predictions (internal, called by tracking-service) → eta-ml-service
        await eta_app(scope, receive, send)

    else:
        # No matching route — return 404
        if scope["type"] == "http":
            response = JSONResponse({"detail": "Not Found"}, status_code=404)
            await response(scope, receive, send)
        else:
            # Close unhandled WebSocket connections cleanly
            await send({"type": "websocket.close", "code": 1000})


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting DUK Bus Tracker backend...")
    # Default to a single worker (unchanged behavior). Per-request shared state is
    # now Redis-backed and cross-process safe: the realtime WS uses one process-wide
    # subscriber fanning to per-client queues, route/trip locks are Redis locks, the
    # planned-route cache self-heals via a Redis version key, and grace timers dedupe
    # via a SET NX Redis key. The remaining reason NOT to blindly raise this: the
    # in-process background loops (GPS polling, realtime bridge, trip-lifecycle
    # consumer) are singletons — running N copies would duplicate their DB work. Only
    # raise WEB_CONCURRENCY once those loops are pinned to a single worker (or moved to
    # the dedicated background-workers container).
    _workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    uvicorn.run(
        "mega_gateway:app",
        host="0.0.0.0",
        port=8000,
        reload=False,       # Never use reload=True in production
        workers=_workers,
        loop="uvloop",      # Faster async event loop
        access_log=True,
        ws_ping_interval=None,
        ws_ping_timeout=None
    )
