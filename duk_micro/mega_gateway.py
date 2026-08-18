import sys
import os
import importlib.util
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

# Add duk_micro to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

def load_app(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return getattr(module, "app")

# Load all microservice FastAPI apps
print("Loading Microservices...")
base = os.path.dirname(__file__)

gps_app = load_app(os.path.join(base, "services/gps-ingestion/main.py"), "gps_ingestion")
realtime_app = load_app(os.path.join(base, "services/realtime-gateway/main.py"), "realtime_gateway")
auth_app = load_app(os.path.join(base, "services/auth-service/main.py"), "auth_service")
tracking_app = load_app(os.path.join(base, "services/tracking-service/main.py"), "tracking_service")
admin_app = load_app(os.path.join(base, "services/admin-service/main.py"), "admin_service")
notification_app = load_app(os.path.join(base, "services/notification-api/main.py"), "notification_api")
trip_app = load_app(os.path.join(base, "services/trip-lifecycle/main.py"), "trip_lifecycle")
eta_app = load_app(os.path.join(base, "services/eta-ml-service/main.py"), "eta_ml_service")

# Create the ASGI routing proxy
async def app(scope, receive, send):
    assert scope["type"] in ("http", "websocket")
    path = scope["path"]

    # Traefik routing rules translated to Python:
    if path.startswith("/api/v1/gps") or path.startswith("/ws/device"):
        await gps_app(scope, receive, send)
    elif path.startswith("/ws/bus"):
        await realtime_app(scope, receive, send)
    elif path.startswith("/api/v1/auth"):
        await auth_app(scope, receive, send)
    elif path.startswith("/api/v1/tracking") or path.startswith("/api/v1/stops") or path.startswith("/api/v1/routes") or path.startswith("/api/v1/latest") or path.startswith("/api/v1/trip_state") or path.startswith("/api/v1/eta") or path.startswith("/api/v1/route_history") or path.startswith("/api/v1/route_geometry") or path.startswith("/api/v1/route_segment") or path.startswith("/api/v1/current_trace") or path.startswith("/api/v1/trip_trace") or path.startswith("/api/v1/snap_route") or path.startswith("/api/v1/history"):
        await tracking_app(scope, receive, send)
    elif path.startswith("/api/v1/admin"):
        await admin_app(scope, receive, send)
    elif path.startswith("/api/v1/notifications") or path.startswith("/api/v1/suggestion"):
        await notification_app(scope, receive, send)
    elif path.startswith("/api/v1/trip"): # Internal trip lifecycle endpoints usually don't have external routes, but adding for completeness
        await trip_app(scope, receive, send)
    elif path.startswith("/api/v1/ml"): # Internal ML endpoints
        await eta_app(scope, receive, send)
    else:
        if scope["type"] == "http":
            response = JSONResponse({"detail": "Not Found. Mega-Gateway active."}, status_code=404)
            await response(scope, receive, send)
        else:
            # For unhandled websockets, just close
            await send({"type": "websocket.close", "code": 1000})

if __name__ == "__main__":
    import uvicorn
    print("Starting Mega-Gateway ASGI Proxy...")
    uvicorn.run("mega_gateway:app", host="0.0.0.0", port=8000, reload=False)
