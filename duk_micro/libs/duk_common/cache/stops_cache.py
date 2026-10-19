# libs/duk_common/cache/stops_cache.py
"""
Redis-backed stops cache with L2 Redis caching.
"""
import json
import logging
import math
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CACHE_KEY = "cache:stops:v2"
ROUTES_CACHE_KEY = "cache:routes:v2"
INVALIDATE_CHANNEL = "cache:invalidate:v2"
CACHE_TTL_SECONDS = 300  # 5 minutes


def _validate_stops_list(stops_data) -> bool:
    """Validates that the cached data is a list of correctly structured dictionaries."""
    if not isinstance(stops_data, list):
        return False
    required_keys = {"id", "route_id", "lat", "lon", "morning_order_index", "evening_order_index"}
    for stop in stops_data:
        if not isinstance(stop, dict):
            return False
        if not required_keys.issubset(stop.keys()):
            return False
    return True


async def get_all_stops(db, redis: Redis, BusStop=None) -> list[dict]:
    """
    Fetch all stops from Redis L2 cache, falling back to DB.
    """
    cached = await redis.get(CACHE_KEY)
    if cached:
        try:
            parsed = json.loads(cached)
            if _validate_stops_list(parsed):
                return parsed
            else:
                logger.warning("[CACHE] Malformed stops cache structure, refreshing from DB")
        except json.JSONDecodeError:
            logger.warning("[CACHE] Corrupt stops cache JSON, refreshing from DB")

    stops = await _query_stops_from_db(db, BusStop)
    if stops:
        await redis.set(CACHE_KEY, json.dumps(stops), ex=CACHE_TTL_SECONDS)

    return stops


async def invalidate_stops_cache(redis: Redis) -> None:
    await redis.delete(CACHE_KEY, ROUTES_CACHE_KEY)
    
    # Delete route-specific caches
    cursor = b"0"
    while cursor:
        cursor, keys = await redis.scan(cursor, match=f"{CACHE_KEY}:*", count=100)
        if keys:
            await redis.delete(*keys)
            
    await redis.publish(INVALIDATE_CHANNEL, "stops")
    logger.info("[CACHE] Stops cache invalidated")


def _format_time(time_obj):
    if hasattr(time_obj, "strftime"):
        return time_obj.strftime("%H:%M:%S")
    return time_obj


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
            select(BusStop).order_by(BusStop.route_id, BusStop.morning_order_index.nullslast())
        )
        stops = result.scalars().all()
        
        valid_stops = []
        for s in stops:
            lat, lon = None, None
            try:
                lat = float(s.lat)
                lon = float(s.lon)
            except (TypeError, ValueError):
                pass
                
            if lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                logger.warning("Invalid coordinates for bus stop %s; skipping", s.id)
                continue
                
            valid_stops.append({
                "id": s.id,
                "route_id": s.route_id,
                "name": s.name,
                "lat": lat,
                "lon": lon,
                "order_index":         s.order_index,
                "morning_order_index": s.morning_order_index if s.morning_order_index is not None else s.order_index,
                "evening_order_index": s.evening_order_index if s.evening_order_index is not None else s.order_index,
                "morning_time": _format_time(getattr(s, "morning_time", None)),
                "evening_time": _format_time(getattr(s, "evening_time", None)),
                "is_morning_origin": bool(getattr(s, "is_morning_origin", False)),
                "is_morning_destination": bool(getattr(s, "is_morning_destination", False)),
                "is_evening_origin": bool(getattr(s, "is_evening_origin", False)),
                "is_evening_destination": bool(getattr(s, "is_evening_destination", False)),
            })
            
        return valid_stops
    except Exception as e:
        logger.error("[CACHE] DB query failed: %s", e)
        raise RuntimeError(f"Database query failed: {e}") from e
