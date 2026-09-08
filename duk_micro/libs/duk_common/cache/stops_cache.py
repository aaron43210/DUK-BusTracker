# libs/duk_common/cache/stops_cache.py
"""
Redis-backed stops cache with pub/sub invalidation.
Fixed: removed module-level mutable globals (unsafe with multiple event loops).
Uses per-request Redis fetch with L2 Redis caching.
"""
import json
import asyncio
import logging
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CACHE_KEY = "cache:stops:v2"
ROUTES_CACHE_KEY = "cache:routes:v2"
INVALIDATE_CHANNEL = "cache:invalidate:v2"
CACHE_TTL_SECONDS = 300  # 5 minutes


async def get_all_stops(db, redis: Redis, BusStop=None) -> list[dict]:
    """
    Fetch all stops from Redis L2 cache, falling back to DB.
    No process-local L1 cache — safe for multi-worker deployments.
    """
    cached = await redis.get(CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            logger.warning("[CACHE] Corrupt stops cache, refreshing from DB")

    stops = await _query_stops_from_db(db, BusStop)
    if stops:
        await redis.set(CACHE_KEY, json.dumps(stops), ex=CACHE_TTL_SECONDS)

    return stops


async def invalidate_stops_cache(redis: Redis) -> None:
    await redis.delete(CACHE_KEY, ROUTES_CACHE_KEY)
    await redis.publish(INVALIDATE_CHANNEL, "stops")
    logger.info("[CACHE] Stops cache invalidated")


async def subscribe_cache_invalidation(redis: Redis) -> None:
    """Background task — receives invalidation signals."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(INVALIDATE_CHANNEL)
    async for message in pubsub.listen():
        if message["type"] == "message":
            logger.info("[CACHE] Invalidation signal: %s", message["data"])
            # Nothing to clear — no L1 cache in this version


async def _query_stops_from_db(db, BusStop=None) -> list[dict]:
    from sqlalchemy import select

    if BusStop is None:
        try:
            from models_local import BusStop as _BS
            BusStop = _BS
        except ImportError:
            logger.error("[CACHE] Cannot import BusStop")
            return []

    try:
        result = await db.execute(
            select(BusStop).order_by(BusStop.route_id, BusStop.order_index)
        )
        stops = result.scalars().all()
        return [
            {
                "id": s.id,
                "route_id": s.route_id,
                "name": s.name,
                "lat": float(s.lat),
                "lon": float(s.lon),
                "order_index": s.order_index,
                "morning_time": getattr(s, "morning_time", None),
                "evening_time": getattr(s, "evening_time", None),
                "is_morning_origin": bool(getattr(s, "is_morning_origin", False)),
                "is_morning_destination": bool(getattr(s, "is_morning_destination", False)),
                "is_evening_origin": bool(getattr(s, "is_evening_origin", False)),
                "is_evening_destination": bool(getattr(s, "is_evening_destination", False)),
            }
            for s in stops
        ]
    except Exception as e:
        logger.error("[CACHE] DB query failed: %s", e)
        return []
