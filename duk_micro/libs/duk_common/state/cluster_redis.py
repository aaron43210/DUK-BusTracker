# libs/duk_common/state/cluster_redis.py
import json
import math
from redis.asyncio import Redis

class RedisClusterState:
    def __init__(self, redis: Redis, device_id: str, ttl_s: int = 3600):
        self.redis = redis
        self.key = f"cluster:{device_id}"
        self.ttl_s = ttl_s

    async def load(self) -> dict:
        raw = await self.redis.get(self.key)
        return json.loads(raw) if raw else {}

    async def save(self, state: dict):
        await self.redis.set(self.key, json.dumps(state), ex=self.ttl_s)


def apply_live_cluster_stateless(
    lat: float, lon: float, state: dict, radius_m: float = 15.0
) -> tuple[float, float, bool, dict]:
    """Pure function live clustering. Returns (lat, lon, should_broadcast, new_state)."""
    def _hav_m(la1, lo1, la2, lo2):
        R = 6_371_000.0
        dlat = math.radians(la2 - la1)
        dlon = math.radians(lo2 - lo1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    new_state = dict(state)
    anchor_lat = state.get("anchor_lat")
    anchor_lon = state.get("anchor_lon")
    sample_count = state.get("sample_count", 0)

    if anchor_lat is None:
        new_state["anchor_lat"] = lat
        new_state["anchor_lon"] = lon
        new_state["sample_count"] = 1
        return lat, lon, True, new_state

    dist = _hav_m(anchor_lat, anchor_lon, lat, lon)
    if dist < radius_m:
        sample_count += 1
        new_state["sample_count"] = sample_count
        alpha = 1.0 / sample_count
        new_lat = (1 - alpha) * anchor_lat + alpha * lat
        new_lon = (1 - alpha) * anchor_lon + alpha * lon
        moved = _hav_m(anchor_lat, anchor_lon, new_lat, new_lon)
        new_state["anchor_lat"] = new_lat
        new_state["anchor_lon"] = new_lon
        return new_lat, new_lon, moved > 3.0, new_state
    else:
        new_state["anchor_lat"] = lat
        new_state["anchor_lon"] = lon
        new_state["sample_count"] = 1
        return lat, lon, True, new_state
