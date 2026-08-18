# libs/duk_common/state/gps_filter_redis.py
import json
from redis.asyncio import Redis
from typing import Optional
import math

class RedisGpsFilterState:
    """GPS filter state backed by Redis — safe across N replicas."""
    
    def __init__(self, redis: Redis, device_id: str, ttl_s: int = 3600):
        self.redis = redis
        self.key = f"gpsfilter:{device_id}"
        self.ttl_s = ttl_s

    async def load(self) -> dict:
        raw = await self.redis.get(self.key)
        return json.loads(raw) if raw else {}

    async def save(self, state: dict):
        await self.redis.set(self.key, json.dumps(state), ex=self.ttl_s)

    async def reset(self):
        await self.redis.delete(self.key)


def apply_gps_filter_stateless(
    raw_lat: float,
    raw_lon: float,
    speed_kmh: Optional[float],
    state: dict,
) -> tuple[float, float, dict]:
    """
    Pure function GPS filter. No module globals.
    Returns (filtered_lat, filtered_lon, new_state_dict)
    """
    STATIONARY_SPEED_KMH = 3.0
    LOCK_DRIFT_M = 30.0
    MIN_LOCK_SAMPLES = 2
    MAX_REALISTIC_SPEED_KMH = 120.0
    EMA_ALPHA = 0.7

    lat, lon = raw_lat, raw_lon
    new_state = dict(state)  # copy

    def _haversine_m(la1, lo1, la2, lo2):
        R = 6_371_000.0
        dlat = math.radians(la2 - la1)
        dlon = math.radians(lo2 - lo1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    # Record speed history
    speed_history = new_state.get("speed_history", [])
    if speed_kmh is not None:
        speed_history = (speed_history + [speed_kmh])[-5:]
    new_state["speed_history"] = speed_history

    # Outlier rejection
    last_lat = new_state.get("last_accepted_lat")
    last_lon = new_state.get("last_accepted_lon")
    
    if last_lat is not None and last_lon is not None:
        dist_m = _haversine_m(last_lat, last_lon, lat, lon)
        # Simple time-based check: if > 2km jump, likely outlier
        if dist_m > 2000:
            lat = last_lat
            lon = last_lon
            return lat, lon, new_state

    # Stationary lock
    is_stationary = (speed_kmh is not None and speed_kmh < STATIONARY_SPEED_KMH)
    locked_lat = new_state.get("locked_lat")
    locked_lon = new_state.get("locked_lon")
    lock_count = new_state.get("lock_sample_count", 0)

    if is_stationary:
        if locked_lat is None:
            new_state["locked_lat"] = lat
            new_state["locked_lon"] = lon
            new_state["lock_sample_count"] = 1
        else:
            drift_m = _haversine_m(locked_lat, locked_lon, lat, lon)
            if drift_m < LOCK_DRIFT_M:
                lock_count += 1
                new_state["lock_sample_count"] = lock_count
                if lock_count >= MIN_LOCK_SAMPLES:
                    alpha = 1.0 / lock_count
                    new_state["locked_lat"] = (1 - alpha) * locked_lat + alpha * lat
                    new_state["locked_lon"] = (1 - alpha) * locked_lon + alpha * lon
                    lat = new_state["locked_lat"]
                    lon = new_state["locked_lon"]
            else:
                new_state["locked_lat"] = lat
                new_state["locked_lon"] = lon
                new_state["lock_sample_count"] = 1
    else:
        # Moving — release lock
        new_state["locked_lat"] = None
        new_state["locked_lon"] = None
        new_state["lock_sample_count"] = 0
        # EMA smoothing
        s_lat = new_state.get("smoothed_lat")
        s_lon = new_state.get("smoothed_lon")
        if s_lat is None:
            new_state["smoothed_lat"] = lat
            new_state["smoothed_lon"] = lon
        else:
            new_state["smoothed_lat"] = EMA_ALPHA * lat + (1 - EMA_ALPHA) * s_lat
            new_state["smoothed_lon"] = EMA_ALPHA * lon + (1 - EMA_ALPHA) * s_lon
            lat = new_state["smoothed_lat"]
            lon = new_state["smoothed_lon"]

    new_state["last_accepted_lat"] = lat
    new_state["last_accepted_lon"] = lon

    return lat, lon, new_state
