"""
Shared Redis connection for all services.
Provides one Redis client per process with lazy initialization and graceful
shutdown. Redis values are decoded to Python strings.
"""
import asyncio
import contextlib
import os
import uuid
from typing import Optional
from redis.asyncio import Redis

_redis_instance: Optional[Redis] = None

def _get_redis_url() -> str:
    """Return the configured Redis URL, rejecting an empty configuration."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0").strip()
    if not url:
        raise RuntimeError("REDIS_URL must not be empty.")
    if not url.startswith(("redis://", "rediss://")):
        raise RuntimeError(
            "REDIS_URL must use redis:// or rediss:// scheme."
        )
    return url

async def get_redis() -> Redis:
    """
    Return the shared Redis client for this process.
    The client is created lazily on the first call and reused afterward.
    Returns:
        Redis: Async Redis client with decoded string responses.
    """
    global _redis_instance
    if _redis_instance is None or _redis_instance.connection_pool is None:
        _redis_instance = Redis.from_url(
            _get_redis_url(),
            decode_responses=True,
            health_check_interval=30,
        )
    return _redis_instance

async def close_redis() -> None:
    """
    Gracefully close the shared Redis client.
    Safe to call multiple times.
    """
    global _redis_instance
    client = _redis_instance
    _redis_instance = None
    if client is not None:
        await client.aclose()


# Lua: delete the lock only if we still own it (compare token, then del).
_UNLOCK_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


@contextlib.asynccontextmanager
async def redis_lock(
    redis: Redis,
    key: str,
    ttl_s: float = 5.0,
    wait_s: float = 2.0,
):
    """
    Cross-process advisory lock backed by a single Redis key. Serializes a
    critical section across OS processes / workers so shared-resource work isn't
    duplicated when more than one worker is running.

    Best-effort: on lock-acquire timeout we proceed anyway (availability over
    strict mutual exclusion) so a stuck holder can never wedge the caller. The
    TTL auto-expires the lock, and release is a token-checked compare-and-del so
    we never delete a lock a later holder has taken. Yields True if we hold the
    lock, False if we timed out waiting and proceeded regardless.
    """
    token = uuid.uuid4().hex
    deadline = asyncio.get_event_loop().time() + wait_s
    acquired = False
    px = int(ttl_s * 1000)
    while True:
        acquired = bool(await redis.set(key, token, nx=True, px=px))
        if acquired or asyncio.get_event_loop().time() >= deadline:
            break
        await asyncio.sleep(0.02)
    try:
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                await redis.eval(_UNLOCK_LUA, 1, key, token)


def device_state_lock(redis: Redis, device_id: str, ttl_s: float = 5.0, wait_s: float = 2.0):
    """
    Per-device advisory lock serializing the load-modify-save of a device's
    filter/snap/cluster state across the ingestion paths (device push, polling
    loop, realtime bridge). Prevents lost updates when two paths touch the same
    device concurrently, without merging the paths themselves. Thin wrapper over
    redis_lock with a per-device key.
    """
    return redis_lock(redis, f"devlock:{device_id}", ttl_s=ttl_s, wait_s=wait_s)
