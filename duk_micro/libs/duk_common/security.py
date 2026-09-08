# libs/duk_common/security.py
"""
Security utilities used across all services.

Provides:
  1. constant_time_compare  — prevents timing attacks on API key checks
  2. RateLimiter            — atomic sliding-window rate limiting via Redis Lua script
  3. TokenBlocklist         — JWT revocation via Redis (for logout)
"""
import hmac
import logging
from typing import Optional

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Atomic sliding-window Lua script.
#
# All four operations (remove expired, count, add, expire) execute as a
# single atomic unit inside Redis. This eliminates the TOCTOU race where two
# concurrent requests both observe count < max before either records itself.
#
# Keys:   KEYS[1] = the rate-limit sorted-set key
# Args:   ARGV[1] = now (float seconds as string)
#         ARGV[2] = window_start (float seconds as string)
#         ARGV[3] = max_requests (int as string)
#         ARGV[4] = window_seconds + 1 (TTL, int as string)
#         ARGV[5] = unique member = now + random suffix (avoids score collision)
#
# Returns: {current_count_AFTER_add, 1-if-allowed-else-0}
# ---------------------------------------------------------------------------
_RATE_LIMIT_LUA = """
local key          = KEYS[1]
local now          = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local max_req      = tonumber(ARGV[3])
local ttl          = tonumber(ARGV[4])
local member       = ARGV[5]

redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = redis.call('ZCARD', key)

if count >= max_req then
    return {count, 0}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return {count + 1, 1}
"""


def constant_time_compare(val1: str, val2: str) -> bool:
    """
    Compares two strings in constant time to prevent timing attacks.

    Input:
        val1 - First string (e.g., the key provided in the request)
        val2 - Second string (e.g., the expected secret key)

    Output:
        True if strings match, False otherwise

    Why this matters:
        A normal `val1 == val2` check returns faster when strings differ early.
        An attacker can measure these tiny timing differences to guess secrets
        character by character. hmac.compare_digest always takes the same time.
    """
    return hmac.compare_digest(
        val1.encode("utf-8"),
        val2.encode("utf-8"),
    )


class RateLimiter:
    """
    Atomic sliding-window rate limiter backed by a Redis Lua script.

    The entire check-and-record operation is atomic, eliminating the race
    condition present in a multi-step pipeline approach.

    Example usage:
        limiter = RateLimiter(redis, "auth:otp", max_requests=3, window_seconds=300)
        allowed, remaining = await limiter.check(identifier="192.168.1.1")
        if not allowed:
            raise HTTPException(429, "Too many requests")
    """

    def __init__(
        self,
        redis: Redis,
        key_prefix: str,
        max_requests: int,
        window_seconds: int,
    ):
        """
        Input:
            redis          - Active Redis connection
            key_prefix     - Namespace for Redis keys (e.g., "auth:otp", "gps:http")
            max_requests   - Maximum allowed requests in the time window
            window_seconds - Length of the sliding window in seconds
        """
        self.redis = redis
        self.key_prefix = key_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # Pre-register the script so Redis can cache it by SHA
        self._script = redis.register_script(_RATE_LIMIT_LUA)

    async def check(self, identifier: str) -> tuple[bool, int]:
        """
        Atomically checks and records a request for the given identifier.

        Input:
            identifier - Usually the client's IP address (or IP+email composite)

        Output:
            (allowed: bool, remaining: int)
              - allowed   = True if the request should proceed
              - remaining = How many more requests are allowed this window

        The Lua script runs atomically inside Redis, preventing the TOCTOU
        race that existed in the previous pipeline-based implementation.
        """
        import time
        import secrets as _secrets

        key = f"ratelimit:{self.key_prefix}:{identifier}"
        now = time.time()
        window_start = now - self.window_seconds
        # Unique member prevents score collisions between simultaneous requests
        member = f"{now}:{_secrets.token_hex(4)}"

        result = await self._script(
            keys=[key],
            args=[
                str(now),
                str(window_start),
                str(self.max_requests),
                str(self.window_seconds + 1),
                member,
            ],
        )
        count_after, allowed_int = int(result[0]), int(result[1])
        allowed = bool(allowed_int)
        remaining = max(0, self.max_requests - count_after)
        return allowed, remaining

    async def reset(self, identifier: str) -> None:
        """
        Clears the rate limit counter for an identifier (e.g., after successful OTP verify).

        Input:  identifier - The IP or key to reset
        Output: nothing
        """
        key = f"ratelimit:{self.key_prefix}:{identifier}"
        await self.redis.delete(key)


class TokenBlocklist:
    """
    JWT revocation list stored in Redis.

    When a user logs out, their token's JTI (unique token ID) is added here.
    Every authenticated request checks this list before processing.

    The blocklist entry auto-expires when the token would have expired anyway,
    so the Redis key is self-cleaning.
    """

    def __init__(self, redis: Redis):
        """
        Input:
            redis - Active Redis connection
        """
        self.redis = redis

    async def revoke(self, jti: str, expires_in_seconds: int) -> None:
        """
        Adds a token JTI to the blocklist.

        Input:
            jti                 - The JWT ID from the token's payload ("jti" claim)
            expires_in_seconds  - How long to keep this entry (match the token's TTL)

        Output: nothing
        """
        await self.redis.set(f"blocklist:jti:{jti}", "1", ex=expires_in_seconds)

    async def is_revoked(self, jti: str) -> bool:
        """
        Checks if a token has been revoked.

        Input:
            jti - The JWT ID to check

        Output:
            True if the token was revoked (reject the request)
            False if the token is still valid
        """
        return await self.redis.exists(f"blocklist:jti:{jti}") > 0
