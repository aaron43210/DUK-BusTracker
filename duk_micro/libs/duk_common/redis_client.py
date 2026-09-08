# libs/duk_common/redis_client.py
"""
Shared Redis connection for all services.

Uses a single persistent connection (singleton pattern) so we don't
open a new Redis connection for every API request.

decode_responses=True means all values come back as Python str,
not bytes — so we never need to call .decode() on anything.
"""
import os
from redis.asyncio import Redis


# Module-level singleton — shared across the whole process lifetime
_redis_instance: Redis | None = None


async def get_redis() -> Redis:
    """
    Returns the shared Redis connection.

    Input:  nothing (reads REDIS_URL from environment)
    Output: Redis — async Redis client, ready to use

    Creates the connection on first call, reuses it on all subsequent calls.
    Default URL: redis://localhost:6379/0
    """
    global _redis_instance
    if _redis_instance is None:
        _redis_instance = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,   # All Redis values are str, not bytes
        )
    return _redis_instance


async def close_redis() -> None:
    """
    Closes the Redis connection gracefully.

    Input:  nothing
    Output: nothing

    Call this during application shutdown to avoid connection leaks.
    """
    global _redis_instance
    if _redis_instance:
        await _redis_instance.aclose()
        _redis_instance = None
