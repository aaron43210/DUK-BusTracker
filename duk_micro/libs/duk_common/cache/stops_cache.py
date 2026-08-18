# libs/duk_common/cache/stops_cache.py
"""Redis-backed stops cache with pub/sub invalidation."""
import json
import asyncio
import logging
from redis.asyncio import Redis

logger = logging.getLogger(__name__)
CACHE_KEY = "cache:stops:v1"
ROUTES_CACHE_KEY = "cache:routes:v1"
INVALIDATE_CHANNEL = "cache:invalidate"

# Optional process-local L1 cache (zeroed on invalidation signal)
_L1_STOPS: list | None = None
_L1_ROUTES: list | None = None


async def get_all_stops(db, redis: Redis, BusStop=None) -> list[dict]:
    """
    Fetch all stops, using a two-tier cache: process-local L1 + Redis L2.

    BusStop: Pass the service-local BusStop SQLAlchemy model class so this
             shared function does not need to import from a fixed path.
    """
    global _L1_STOPS
    if _L1_STOPS is not None:
        return _L1_STOPS

    cached = await redis.get(CACHE_KEY)
    if cached:
        _L1_STOPS = json.loads(cached)
        return _L1_STOPS

    stops = await _query_stops_from_db(db, BusStop)
    await redis.set(CACHE_KEY, json.dumps(stops), ex=300)
    _L1_STOPS = stops
    return stops


async def invalidate_stops_cache(redis: Redis):
    global _L1_STOPS, _L1_ROUTES
    _L1_STOPS = None
    _L1_ROUTES = None
    await redis.delete(CACHE_KEY, ROUTES_CACHE_KEY)
    await redis.publish(INVALIDATE_CHANNEL, "stops")
    logger.info("[CACHE] Stops cache invalidated and broadcast to all replicas")


async def subscribe_cache_invalidation(redis: Redis):
    """Background task — clears L1 when any replica invalidates."""
    global _L1_STOPS, _L1_ROUTES
    pubsub = redis.pubsub()
    await pubsub.subscribe(INVALIDATE_CHANNEL)
    async for message in pubsub.listen():
        if message["type"] == "message":
            logger.info("[CACHE] Received invalidation signal: %s", message["data"])
            _L1_STOPS = None
            _L1_ROUTES = None


async def _query_stops_from_db(db, BusStop=None) -> list[dict]:
    """
    Query stops from DB.

    BusStop: Service-local SQLAlchemy BusStop model class.
             If None, falls back to a dynamic import (avoid in new code — always pass explicitly).
    """
    from sqlalchemy import select

    if BusStop is None:
        # Fallback: try the service's own models_local, then monolith path.
        # Each service should pass its own BusStop class explicitly to avoid this.
        try:
            from models_local import BusStop as _BS
            BusStop = _BS
        except ImportError:
            try:
                from models.route import BusStop as _BS  # monolith fallback
                BusStop = _BS
            except ImportError:
                logger.error(
                    "[CACHE] Cannot import BusStop — pass model class explicitly to get_all_stops()"
                )
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
                "is_morning_origin":      bool(getattr(s, "is_morning_origin", False) or False),
                "is_morning_destination": bool(getattr(s, "is_morning_destination", False) or False),
                "is_evening_origin":      bool(getattr(s, "is_evening_origin", False) or False),
                "is_evening_destination": bool(getattr(s, "is_evening_destination", False) or False),
            }
            for s in stops
        ]
    except Exception as e:
        logger.error("Failed to query stops from DB: %s", e)
        return []
