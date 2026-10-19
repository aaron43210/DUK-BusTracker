# libs/duk_common/state/snap_redis.py
import json
import math
from redis.asyncio import Redis

class RedisSnapState:
    def __init__(self, redis: Redis, device_id: str, ttl_s: int = 3600):
        ''' Initialize RedisSnapState. 
        Args:
            redis (Redis): Redis client.
            device_id (str): Device ID.
            ttl_s (int): Time to live in seconds.
        '''# Creates the Redis state manager for a particular bus.
        self.redis = redis
        self.key = f"snapstate:{device_id}"
        self.ttl_s = ttl_s

    async def load(self) -> dict:
        ''' Loads the last saved snapped state for this bus from Redis.
        Args:
            state (dict): Snap state.
        Returns:
            dict: Snap state.
        '''# Loads the current road-snapping state for the bus.
        raw = await self.redis.get(self.key)
        return json.loads(raw) if raw else {}

    async def save(self, state: dict):
        ''' Saves the new snapped state for this bus to Redis. 
        Args:
            state (dict): Snap state.
        '''#Stores the current snapping state in Redis.
        await self.redis.set(self.key, json.dumps(state), ex=self.ttl_s)

    async def reset(self):
        ''' Reset snap state in Redis. '''
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
        ''' Calculate the bearing between two points. 
        Args:
            la1 (float): Latitude of the first point.
            lo1 (float): Longitude of the first point.
            la2 (float): Latitude of the second point.
            lo2 (float): Longitude of the second point.
        Returns:
            float: Bearing between the two points.
        '''# Determines the direction of movement.
        la1_r, la2_r = math.radians(la1), math.radians(la2)
        dlon_r = math.radians(lo2 - lo1)
        x = math.sin(dlon_r) * math.cos(la2_r)
        y = math.cos(la1_r) * math.sin(la2_r) - math.sin(la1_r) * math.cos(la2_r) * math.cos(dlon_r)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    def _angle_diff(a1, a2):
        ''' Calculate the angle difference between two angles. 
        Args:
            a1 (float): First angle.
            a2 (float): Second angle.
        Returns:
            float: Angle difference between the two angles.
        '''
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
                # Hysteresis stops GPS jitter from flipping the locked bearing.
                # But a near-reversal (>135°) while the bus is clearly moving is a
                # real turn-around at the terminus, not noise — adapt after 2 fixes
                # instead of 3 so the OSRM bearing cone stops snapping the bus to
                # the wrong-direction carriageway through the turn (audit S3).
                flip_threshold = 2 if diff > 135 else 3
                if disagreements >= flip_threshold:
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
