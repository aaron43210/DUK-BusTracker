# libs/duk_common/redis_client.py
import os
from redis.asyncio import Redis
from functools import lru_cache

_redis_instance: Redis | None = None

async def get_redis() -> Redis:
    global _redis_instance
    if _redis_instance is None:
        _redis_instance = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
    return _redis_instance

async def close_redis():
    global _redis_instance
    if _redis_instance:
        await _redis_instance.aclose()
        _redis_instance = None
