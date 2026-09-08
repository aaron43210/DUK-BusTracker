# libs/duk_common/middleware.py
"""
Production HTTP middleware applied to every service's FastAPI app.

Applied via configure_app() in each service's main.py startup.

Middleware stack (applied in reverse order, so last added runs first):
  1. RequestSizeLimitMiddleware — rejects giant payloads (prevents DoS)
  2. RequestIDMiddleware        — adds X-Request-ID to every request for tracing
  3. SecurityHeadersMiddleware  — adds browser security headers to every response
  4. CORSMiddleware             — controls which domains can call the API
"""
import uuid
import logging
from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to every HTTP response.

    These headers tell the browser to be extra careful with the content:
      - X-Content-Type-Options: Don't guess the content type (prevents MIME sniffing attacks)
      - X-Frame-Options: Don't allow this page to be embedded in iframes (prevents clickjacking)
      - X-XSS-Protection: Enable browser's built-in XSS filter
      - Referrer-Policy: Don't leak the full URL to third parties
      - Strict-Transport-Security: Always use HTTPS for the next 2 years
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
        # Remove the "Server: uvicorn" header so attackers can't fingerprint our stack
        if "server" in response.headers:
            del response.headers["server"]
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Adds a unique X-Request-ID header to every request and response.

    This makes it easy to trace a specific request through all log files.
    The client can also send their own X-Request-ID and we'll echo it back.

    Input:  Any HTTP request (optionally with X-Request-ID header)
    Output: The same response, with X-Request-ID header added
    """

    async def dispatch(self, request: Request, call_next):
        # Use the client's ID if provided, otherwise generate a new UUID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Rejects HTTP requests with bodies larger than the configured limit.

    Prevents DoS attacks where someone sends a huge payload to crash the server.

    Default limit: 1 MB (1,048,576 bytes)
    GPS payloads are ~200 bytes, so 1 MB is very generous.
    """

    def __init__(self, app, max_body_size: int = 1024 * 1024):
        """
        Input:
            app           - The FastAPI app
            max_body_size - Max allowed request body in bytes (default: 1 MB)
        """
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_body_size:
                    return JSONResponse(
                        {"detail": "Request body too large"},
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)

        received = 0
        receive = request.receive

        async def wrapped_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    raise RuntimeError("Payload Too Large")
            return message

        request._receive = wrapped_receive

        try:
            return await call_next(request)
        except RuntimeError as exc:
            if str(exc) == "Payload Too Large":
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
            raise


def configure_app(
    app: FastAPI,
    allowed_origins: Sequence[str] = (),
    max_body_size: int = 1024 * 1024,
) -> FastAPI:
    """
    Applies all production middleware to a FastAPI app.

    Call this once in each service's main.py, right after creating the app:
        app = FastAPI(...)
        configure_app(app, allowed_origins=settings.allowed_origins_list)

    Input:
        app             - The FastAPI app instance
        allowed_origins - List of domains allowed to call the API.
                          MUST be non-empty. Set ALLOWED_ORIGINS in .env.
                          Defaulting to ['*'] is a security vulnerability —
                          the app will refuse to start if this is not configured.
        max_body_size   - Maximum allowed request body size in bytes (default: 1 MB)

    Output:
        The same app with middleware applied (also modifies in-place)

    CORS (Cross-Origin Resource Sharing):
        This controls which websites can make API calls to our backend.
        In production, set ALLOWED_ORIGINS=https://bus.duk.ac.in so only our
        PWA can access the API, not random websites.
    """
    origins = list(allowed_origins)

    if not origins:
        # Fail-closed: a missing/empty ALLOWED_ORIGINS is a misconfiguration,
        # not a reason to open the API to the entire internet.
        # For local development, set ALLOWED_ORIGINS=http://localhost:5173
        raise RuntimeError(
            "ALLOWED_ORIGINS is not configured. "
            "Set it to your frontend URL(s) in .env (e.g. http://localhost:5173). "
            "Refusing to start with CORS open to '*'."
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-API-Key", "X-Admin-Token"],
        expose_headers=["X-Request-ID"],
        max_age=86400,   # Cache preflight responses for 24 hours
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_body_size=max_body_size)
    return app
