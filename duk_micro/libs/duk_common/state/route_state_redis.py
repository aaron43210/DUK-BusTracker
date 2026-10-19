# libs/duk_common/state/route_state_redis.py
"""
Redis-backed persistence for route-constrained snapping progress.

Keeps the pure linear-referencing logic in ``route_snap.py`` free of any Redis
dependency (so it stays unit-testable without a running Redis), mirroring the
split used by ``snap_redis.py`` / ``gps_filter_redis.py``.
"""
import json
from redis.asyncio import Redis


class RedisRouteState:
    """Per-device route-progress state (``s`` along the planned route)."""

    def __init__(self, redis: Redis, device_id: str, ttl_s: int = 3600):
        self.redis = redis
        self.key = f"routestate:{device_id}"
        self.ttl_s = ttl_s

    async def load(self) -> dict:
        raw = await self.redis.get(self.key)
        return json.loads(raw) if raw else {}

    async def save(self, state: dict):
        await self.redis.set(self.key, json.dumps(state), ex=self.ttl_s)

    async def reset(self):
        await self.redis.delete(self.key)
