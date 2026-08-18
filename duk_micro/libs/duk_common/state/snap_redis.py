# libs/duk_common/state/snap_redis.py
import json
import math
from redis.asyncio import Redis
from typing import Optional

class RedisSnapState:
    def __init__(self, redis: Redis, device_id: str, ttl_s: int = 3600):
        self.redis = redis
        self.key = f"snapstate:{device_id}"
        self.ttl_s = ttl_s

    async def load(self) -> dict:
        raw = await self.redis.get(self.key)
        return json.loads(raw) if raw else {}

    async def save(self, state: dict):
        await self.redis.set(self.key, json.dumps(state), ex=self.ttl_s)

    async def reset(self):
        await self.redis.delete(self.key)


def apply_snap_stateless(
    lat: float,
    lon: float,
    snapped_lat: float,
    snapped_lon: float,
    state: dict,
) -> dict:
    """Update bearing lock state after a snap. Pure function."""
    def _bearing(la1, lo1, la2, lo2):
        la1_r, la2_r = math.radians(la1), math.radians(la2)
        dlon_r = math.radians(lo2 - lo1)
        x = math.sin(dlon_r) * math.cos(la2_r)
        y = math.cos(la1_r) * math.sin(la2_r) - math.sin(la1_r) * math.cos(la2_r) * math.cos(dlon_r)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    def _angle_diff(a1, a2):
        d = abs(a1 - a2)
        return min(d, 360 - d)

    new_state = dict(state)
    prev_lat = state.get("lat")
    prev_lon = state.get("lon")
    prev_bearing = state.get("bearing")
    disagreements = state.get("disagreements", 0)

    if prev_lat is not None and prev_lon is not None:
        def _hav_m(la1, lo1, la2, lo2):
            R = 6_371_000.0
            dlat = math.radians(la2 - la1)
            dlon = math.radians(lo2 - lo1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlon/2)**2
            return R * 2 * math.asin(math.sqrt(a))

        dist = _hav_m(prev_lat, prev_lon, lat, lon)
        if dist > 8.0:
            current_bearing = _bearing(prev_lat, prev_lon, lat, lon)
            if prev_bearing is not None:
                diff = _angle_diff(current_bearing, prev_bearing)
                if diff > 45:
                    disagreements += 1
                else:
                    disagreements = 0
                if disagreements >= 3:
                    new_state["bearing"] = current_bearing
                    new_state["disagreements"] = 0
                else:
                    new_state["disagreements"] = disagreements
            else:
                new_state["bearing"] = current_bearing
                new_state["disagreements"] = 0

    new_state["lat"] = snapped_lat
    new_state["lon"] = snapped_lon
    return new_state
