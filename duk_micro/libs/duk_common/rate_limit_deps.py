# libs/duk_common/rate_limit_deps.py
"""
FastAPI dependency factories for rate limiting.

Key security fix: X-Forwarded-For is now only trusted when it arrives on a
connection whose direct peer IP is in TRUSTED_PROXY_IPS. Otherwise the real
client IP (request.client.host) is used, preventing attackers from bypassing
rate limits by spoofing the header.
"""
import os
from typing import Optional, Callable, Set
from fastapi import Request, HTTPException
from libs.duk_common.redis_client import get_redis
from libs.duk_common.security import RateLimiter


# Comma-separated list of trusted reverse-proxy IPs, e.g.:
#   TRUSTED_PROXY_IPS=127.0.0.1,10.0.0.1
# Only requests arriving from these IPs will have X-Forwarded-For honoured.
_raw = os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1")
TRUSTED_PROXY_IPS: Set[str] = {ip.strip() for ip in _raw.split(",") if ip.strip()}


def _get_real_ip(request: Request) -> str:
    """
    Returns the real client IP, respecting X-Forwarded-For ONLY when the
    direct peer (request.client.host) is a known trusted proxy.

    This prevents IP spoofing attacks where an external client sends:
        X-Forwarded-For: 1.2.3.4
    to impersonate a different IP and bypass per-IP rate limits.

    Trusted proxies are configured via the TRUSTED_PROXY_IPS environment
    variable (default: 127.0.0.1, ::1).
    """
    direct_peer = request.client.host if request.client else "unknown"
    if direct_peer in TRUSTED_PROXY_IPS:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            # Take the left-most address — that's the original client
            return forwarded_for.split(",")[0].strip()
    return direct_peer


def make_rate_limit_dep(
    key_prefix: str,
    max_requests: int,
    window_seconds: int,
    identifier_fn: Optional[Callable[[Request], str]] = None,
):
    """
    Returns a FastAPI dependency that enforces atomic sliding-window rate limiting.

    Example:
        otp_rate_limit = make_rate_limit_dep("otp:send", 3, 300)

        @app.post("/register")
        async def register(req: RegisterRequest, _: None = Depends(otp_rate_limit)):
            ...

    Args:
        key_prefix      - Redis key namespace (e.g., "otp:send", "auth:general")
        max_requests    - Max allowed requests in window_seconds
        window_seconds  - Sliding window length in seconds
        identifier_fn   - Optional override for extracting the identifier from the
                          request. Defaults to _get_real_ip().
    """
    async def _rate_limit(request: Request):
        redis = await get_redis()
        limiter = RateLimiter(redis, key_prefix, max_requests, window_seconds)

        identifier = identifier_fn(request) if identifier_fn else _get_real_ip(request)

        allowed, remaining = await limiter.check(identifier)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(window_seconds)},
            )

    return _rate_limit
