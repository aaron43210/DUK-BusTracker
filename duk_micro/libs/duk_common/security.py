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
#         ARGV[2] = window (float seconds as string)
#         ARGV[3] = max_requests (int as string)
#         ARGV[4] = window_seconds + 1 (TTL, int as string)
#         ARGV[5] = unique member = now + random suffix (avoids score collision)
#
# Returns: {current_count_AFTER_add, 1-if-allowed-else-0, retry_after_seconds}
# ---------------------------------------------------------------------------
_RATE_LIMIT_LUA = """
local key          = KEYS[1]
local now          = tonumber(ARGV[1])
local window       = tonumber(ARGV[2])
local max_req      = tonumber(ARGV[3])
local ttl          = tonumber(ARGV[4])
local member       = ARGV[5]

local window_start = now - window

-- Remove elements exactly at or before window_start
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = redis.call('ZCARD', key)

if count >= max_req then
    -- Find the oldest score in the window to calculate when it expires
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = window
    if #oldest > 0 then
        local oldest_score = tonumber(oldest[2])
        retry_after = math.max(1, math.ceil(oldest_score + window - now))
    end
    return {count, 0, retry_after}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return {count + 1, 1, 0}
"""


def constant_time_compare(val1: str, val2: str) -> bool:
    """
    Compares two strings in constant time to prevent timing attacks.
    """
    if not isinstance(val1, str) or not isinstance(val2, str):
        return False
    return hmac.compare_digest(
        val1.encode("utf-8"),
        val2.encode("utf-8"),
    )


class RateLimiter:
    """
    Atomic sliding-window rate limiter backed by a Redis Lua script.
    """

    def __init__(
        self,
        redis: Redis,
        key_prefix: str,
        max_requests: int,
        window_seconds: int,
    ):
        if not key_prefix or not key_prefix.strip():
            raise ValueError("key_prefix must not be empty")
        if max_requests <= 0:
            raise ValueError("max_requests must be greater than zero")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
            
        self.redis = redis
        self.key_prefix = key_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # Pre-register the script so Redis can cache it by SHA
        self._script = redis.register_script(_RATE_LIMIT_LUA)

    async def check(self, identifier: str) -> tuple[bool, int, int]:
        """
        Atomically checks and records a request for the given identifier.

        Returns:
            (allowed: bool, remaining: int, retry_after: int)
        """
        if not identifier:
            raise ValueError("identifier must not be empty")
        if len(identifier) > 512:
            raise ValueError("identifier too long")
            
        import time
        import secrets as _secrets
        import hashlib

        # Hash the identifier so raw emails/IPs are obfuscated and bounded
        hashed_id = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        key = f"ratelimit:{self.key_prefix}:{hashed_id}"
        
        now = time.time()
        # Unique member prevents score collisions between simultaneous requests
        member = f"{now}:{_secrets.token_hex(4)}"

        result = await self._script(
            keys=[key],
            args=[
                str(now),
                str(self.window_seconds),
                str(self.max_requests),
                str(self.window_seconds + 1),
                member,
            ],
        )
        count_after = int(result[0])
        allowed_int = int(result[1])
        retry_after = int(result[2])
        
        allowed = bool(allowed_int)
        remaining = max(0, self.max_requests - count_after)
        return allowed, remaining, retry_after

    async def reset(self, identifier: str) -> None:
        """
        Clears the rate limit counter for an identifier.
        """
        import hashlib
        if not identifier:
            return
        hashed_id = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        key = f"ratelimit:{self.key_prefix}:{hashed_id}"
        await self.redis.delete(key)


class TokenBlocklist:
    """
    JWT revocation list stored in Redis.
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    async def revoke(self, jti: str, expires_in_seconds: int) -> None:
        """
        Adds a token JTI to the blocklist.
        """
        if not jti or len(jti) > 256:
            raise ValueError("Invalid JWT ID")
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be greater than zero")
            
        await self.redis.set(f"blocklist:jti:{jti}", "1", ex=expires_in_seconds)

    async def is_revoked(self, jti: str) -> bool:
        """
        Checks if a token has been revoked.
        """
        if not jti:
            return False
        return bool(await self.redis.exists(f"blocklist:jti:{jti}"))
