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
import re
import logging
from typing import Sequence
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds production security headers to HTTP responses."""
    def __init__(
        self,
        app,
        environment: str = "development",
        hsts_preload: bool = False,
    ):
        super().__init__(app)
        self.environment = environment.lower().strip()
        self.hsts_preload = hsts_preload

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        # HSTS only when the service is actually running in production.
        if self.environment == "production":
            hsts = "max-age=63072000; includeSubDomains"
            if self.hsts_preload:
                hsts += "; preload"
            response.headers["Strict-Transport-Security"] = hsts
            
        # API services normally don't need a CSP on Swagger/OpenAPI pages.
        # Swagger UI loads its own scripts/styles, so applying
        # default-src 'none' there would break the documentation UI.
        path = request.url.path
        if not (
            path.startswith("/docs")
            or path.startswith("/redoc")
            or path == "/openapi.json"
        ):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "script-src 'none'; "
                "style-src 'none'; "
                "img-src 'none'; "
                "connect-src 'none'; "
                "frame-ancestors 'none'; "
                "base-uri 'none'; "
                "form-action 'none'"
            )
            
        # Remove server fingerprinting headers if present.
        if "server" in response.headers:
            del response.headers["server"]
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Adds a validated X-Request-ID to every request and response.
    Client-provided IDs are accepted only when they are short and contain
    safe tracing characters. Invalid IDs are replaced with a UUID.
    """
    MAX_REQUEST_ID_LENGTH = 128
    REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")

    async def dispatch(self, request: Request, call_next):
        incoming_id = request.headers.get("X-Request-ID")
        if (
            incoming_id
            and len(incoming_id) <= self.MAX_REQUEST_ID_LENGTH
            and self.REQUEST_ID_PATTERN.fullmatch(incoming_id)
        ):
            request_id = incoming_id
        else:
            request_id = str(uuid.uuid4())
            
        request.state.request_id = request_id
        
        try:
            response = await call_next(request)
        except Exception:
            # Exception handling remains delegated to FastAPI/Starlette.
            # Do not convert application exceptions into middleware errors.
            raise
            
        response.headers["X-Request-ID"] = request_id
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Rejects HTTP requests whose body exceeds the configured limit.
    Both Content-Length and actual streamed bytes are checked so clients
    cannot bypass the limit by omitting or spoofing Content-Length.
    """
    def __init__(self, app, max_body_size: int = 1024 * 1024):
        if not isinstance(max_body_size, int) or isinstance(max_body_size, bool):
            raise ValueError("max_body_size must be a positive integer")
        if max_body_size <= 0:
            raise ValueError("max_body_size must be greater than 0")
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"detail": "Invalid Content-Length"},
                    status_code=400,
                )
            if declared_length < 0:
                return JSONResponse(
                    {"detail": "Invalid Content-Length"},
                    status_code=400,
                )
            if declared_length > self.max_body_size:
                return JSONResponse(
                    {"detail": "Request body too large"},
                    status_code=413,
                )
                
        received = 0
        receive = request.receive

        async def wrapped_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                received += len(body)
                if received > self.max_body_size:
                    raise RuntimeError("Payload Too Large")
            return message
            
        request._receive = wrapped_receive
        
        try:
            return await call_next(request)
        except RuntimeError as exc:
            if str(exc) == "Payload Too Large":
                return JSONResponse(
                    {"detail": "Request body too large"},
                    status_code=413,
                )
            raise


def configure_app(
    app: FastAPI,
    allowed_origins: Sequence[str] = (),
    max_body_size: int = 1024 * 1024,
    environment: str = "development",
    hsts_preload: bool = False,
) -> FastAPI:
    """
    Applies production HTTP security middleware to a FastAPI application.
    Args:
        app:
            FastAPI application instance.
        allowed_origins:
            Explicit CORS origins. Wildcard '*' is prohibited because
            credentials are enabled.
        max_body_size:
            Maximum accepted HTTP request body size in bytes.
        environment:
            Runtime environment. HSTS is enabled only for "production".
        hsts_preload:
            Whether to add the HSTS preload directive. Keep False unless
            the domain is intentionally configured for HSTS preload.
    """
    if not isinstance(environment, str) or not environment.strip():
        raise ValueError("environment must be a non-empty string")
    environment = environment.strip().lower()
    
    if max_body_size <= 0:
        raise ValueError("max_body_size must be greater than 0")
        
    origins = [
        origin.strip()
        for origin in allowed_origins
        if origin and origin.strip()
    ]
    
    if not origins:
        raise RuntimeError(
            "ALLOWED_ORIGINS is not configured. "
            "Set it to the frontend URL(s). "
            "Refusing to start with CORS open to '*'."
        )
        
    if "*" in origins:
        raise RuntimeError(
            "Wildcard CORS origin '*' is not allowed when "
            "allow_credentials=True."
        )
        
    for origin in origins:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"}:
            raise RuntimeError(
                f"Invalid CORS origin '{origin}': "
                "only http:// and https:// origins are allowed."
            )
        if not parsed.netloc:
            raise RuntimeError(
                f"Invalid CORS origin '{origin}': missing hostname."
            )
        # Origins should not contain paths, query strings or fragments.
        if parsed.path not in ("", "/") or parsed.params:
            raise RuntimeError(
                f"Invalid CORS origin '{origin}': "
                "paths are not allowed."
            )
        if parsed.query or parsed.fragment:
            raise RuntimeError(
                f"Invalid CORS origin '{origin}': "
                "query strings and fragments are not allowed."
            )
            
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-API-Key",
            "X-Admin-Token",
        ],
        expose_headers=["X-Request-ID"],
        max_age=86400,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        environment=environment,
        hsts_preload=hsts_preload,
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_body_size=max_body_size,
    )
    return app
