# libs/duk_common/rate_limit_deps.py
"""
FastAPI dependency factories for Redis-backed rate limiting.
X-Forwarded-For is trusted only when the direct TCP peer is a configured
trusted reverse proxy. Client-supplied forwarding headers are otherwise
ignored to prevent IP spoofing and rate-limit bypass.
"""
import ipaddress
import os
from typing import Callable, Optional
from fastapi import HTTPException, Request
from libs.duk_common.redis_client import get_redis
from libs.duk_common.security import RateLimiter


def _load_trusted_proxy_ips() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """
    Load and validate trusted reverse-proxy addresses.
    Accepts single IPs ("127.0.0.1") and CIDR ranges ("172.16.0.0/12"). CIDR
    matters for Docker: Traefik reaches the app from a container IP that is not
    known in advance, so an exact-IP list made every client look like the proxy
    and share ONE rate-limit bucket.
    Invalid configuration fails during application startup.
    """
    raw = os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1")
    trusted: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            trusted.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid IP address or CIDR in TRUSTED_PROXY_IPS: {value!r}"
            ) from exc
    return trusted

TRUSTED_PROXY_IPS = _load_trusted_proxy_ips()


def is_trusted_proxy(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `ip` is inside any configured TRUSTED_PROXY_IPS address/range."""
    return any(ip.version == net.version and ip in net for net in TRUSTED_PROXY_IPS)


def _parse_ip(value: str) -> Optional[str]:
    """
    Validate and normalize an IP address.
    Returns:
        Canonical string representation, or None if invalid.
    """
    try:
        return str(ipaddress.ip_address(value.strip()))
    except (ValueError, AttributeError):
        return None


def _get_real_ip(request: Request) -> str:
    """
    Determine the client IP used for rate limiting.
    X-Forwarded-For is trusted only when the immediate TCP peer is a
    configured trusted reverse proxy.
    Example:
        Client -> Proxy -> FastAPI
        Proxy sends:
            X-Forwarded-For: 1.2.3.4
        FastAPI trusts 1.2.3.4 only because the direct peer is trusted.
    """
    direct_peer = request.client.host if request.client else None
    if not direct_peer:
        return "unknown"
        
    normalized_peer = _parse_ip(direct_peer)
    if normalized_peer is None:
        return "unknown"
        
    peer_ip = ipaddress.ip_address(normalized_peer)
    
    # Direct connection is not from a trusted proxy.
    if not is_trusted_proxy(peer_ip):
        return normalized_peer
        
    forwarded_for = request.headers.get("X-Forwarded-For")
    if not forwarded_for:
        return normalized_peer

    # Parse X-Forwarded-For right-to-left. Proxies APPEND the peer they received
    # from, so the right-most entries are the ones added by our own trusted
    # infrastructure and the left-most entries are attacker-controllable. Walking
    # from the right and skipping trusted-proxy hops yields the first address we
    # did not vouch for — the real client — and prevents a spoofed left-most entry
    # from minting a fresh rate-limit bucket per request.
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for raw_hop in reversed(hops):
        hop_ip = _parse_ip(raw_hop)
        if hop_ip is None:
            # Malformed hop: stop trusting anything further left.
            return normalized_peer
        if is_trusted_proxy(ipaddress.ip_address(hop_ip)):
            continue
        return hop_ip

    # Every hop was a trusted proxy (or none were usable): fall back to the peer.
    return normalized_peer


def make_rate_limit_dep(
    key_prefix: str,
    max_requests: int,
    window_seconds: int,
    identifier_fn: Optional[Callable[[Request], str]] = None,
):
    """
    Create a FastAPI dependency for Redis-backed rate limiting.
    Args:
        key_prefix:
            Redis namespace for this rate limiter.
        max_requests:
            Maximum number of requests permitted within the window.
        window_seconds:
            Sliding-window duration in seconds.
        identifier_fn:
            Optional custom identifier function.
            Defaults to the validated client IP.
    """
    if not key_prefix or not key_prefix.strip():
        raise ValueError("key_prefix must not be empty")
    if max_requests <= 0:
        raise ValueError("max_requests must be greater than zero")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be greater than zero")
        
    async def _rate_limit(request: Request) -> None:
        redis = await get_redis()
        limiter = RateLimiter(
            redis,
            key_prefix,
            max_requests,
            window_seconds,
        )
        identifier = (
            identifier_fn(request)
            if identifier_fn
            else _get_real_ip(request)
        )
        if not identifier:
            identifier = "unknown"
            
        allowed, remaining, retry_after = await limiter.check(identifier)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )
        request.state.rate_limit_remaining = remaining
        
    return _rate_limit
