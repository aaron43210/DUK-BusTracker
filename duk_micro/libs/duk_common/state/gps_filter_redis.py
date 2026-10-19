# libs/duk_common/state/gps_filter_redis.py
import json
import logging
import math
from redis.asyncio import Redis
from typing import Optional

logger = logging.getLogger(__name__)

class RedisGpsFilterState:
    """GPS filter state backed by Redis — safe across N replicas."""
    
    def __init__(self, redis: Redis, device_id: str, ttl_s: int = 3600):
        '''Creates the Redis state manager for a particular bus.
        Args:
            redis (Redis): Redis client.
            device_id (str): Device ID.
            ttl_s (int): Time to live in seconds.
        '''
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
    timestamp_s: Optional[float] = None,
    stationary_speed_kmh: float = 3.0,
    lock_drift_m: float = 30.0,
    min_lock_samples: int = 2,
    max_realistic_speed_kmh: float = 110.0,  # raised from 80: cascade-rejection test showed 80 freezes dot at 40 km/h + 30m GPS noise
    ema_alpha: float = 0.7,
    outlier_distance_m: float = 2000.0
) -> tuple[float, float, dict]:
    """
    Pure function GPS filter. No module globals.
    Returns (filtered_lat, filtered_lon, new_state_dict)
    """
    new_state = dict(state)  # copy

    # 1. Coordinate validation
    if not (math.isfinite(raw_lat) and -90 <= raw_lat <= 90):
        raise ValueError(f"Invalid latitude: {raw_lat}")
    if not (math.isfinite(raw_lon) and -180 <= raw_lon <= 180):
        raise ValueError(f"Invalid longitude: {raw_lon}")

    # 2. Speed validation
    if speed_kmh is not None:
        if not math.isfinite(speed_kmh) or speed_kmh < 0 or speed_kmh > max_realistic_speed_kmh:
            # Reject the point — but still count the rejection and auto-reset after
            # 20 in a row, exactly like the distance/implied-speed branches below.
            # Without this, a stuck speed sensor reporting an out-of-range value on
            # every ping freezes the live dot forever with no self-recovery.
            new_state["consecutive_rejections"] = new_state.get("consecutive_rejections", 0) + 1
            if new_state["consecutive_rejections"] >= 20:
                logger.critical(
                    "[GPS-FILTER] 20+ consecutive rejections (speed sensor) — auto-resetting. "
                    "Incoming=(%.5f,%.5f) speed_kmh=%s",
                    raw_lat, raw_lon, speed_kmh
                )
                new_state.pop("last_accepted_lat", None)
                new_state.pop("last_accepted_lon", None)
                new_state.pop("last_accepted_timestamp", None)
                new_state["consecutive_rejections"] = 0
                return raw_lat, raw_lon, new_state
            return new_state.get("last_accepted_lat", raw_lat), new_state.get("last_accepted_lon", raw_lon), new_state

    # 3. Timestamp validation
    if timestamp_s is not None and not math.isfinite(timestamp_s):
        timestamp_s = None

    lat, lon = raw_lat, raw_lon

    def _haversine_m(la1, lo1, la2, lo2):
        R = 6_371_000.0
        dlat = math.radians(la2 - la1)
        dlon = math.radians(lo2 - lo1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlon/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    last_lat = new_state.get("last_accepted_lat")
    last_lon = new_state.get("last_accepted_lon")
    last_ts = new_state.get("last_accepted_timestamp")

    # Outlier rejection (time-aware + hard limit)
    if last_lat is not None and last_lon is not None:
        dist_m = _haversine_m(last_lat, last_lon, lat, lon)
        
        # Time-aware speed limit & Outlier rejection
        if timestamp_s is not None and last_ts is not None:
            time_diff_s = timestamp_s - last_ts
            if time_diff_s > 0:
                time_diff_hours = time_diff_s / 3600.0
                implied_speed = (dist_m / 1000.0) / time_diff_hours
                
                # Allow >2km jump if bus was offline/idle for >5 minutes
                if dist_m > outlier_distance_m and time_diff_s < 300:
                    new_state["consecutive_rejections"] = new_state.get("consecutive_rejections", 0) + 1
                    if new_state["consecutive_rejections"] >= 20:
                        logger.critical(
                            "[GPS-FILTER] 20+ consecutive rejections (outlier+time) — auto-resetting. "
                            "Last=(%.5f,%.5f) Incoming=(%.5f,%.5f)",
                            last_lat or 0, last_lon or 0, raw_lat, raw_lon
                        )
                        new_state.pop("last_accepted_lat", None)
                        new_state.pop("last_accepted_lon", None)
                        new_state.pop("last_accepted_timestamp", None)
                        new_state["consecutive_rejections"] = 0
                        return raw_lat, raw_lon, new_state
                    return last_lat, last_lon, new_state
                    
                if implied_speed > max_realistic_speed_kmh:
                    new_state["consecutive_rejections"] = new_state.get("consecutive_rejections", 0) + 1
                    if new_state["consecutive_rejections"] >= 20:
                        logger.critical(
                            "[GPS-FILTER] 20+ consecutive rejections (speed) — auto-resetting. "
                            "Last=(%.5f,%.5f) Incoming=(%.5f,%.5f)",
                            last_lat or 0, last_lon or 0, raw_lat, raw_lon
                        )
                        new_state.pop("last_accepted_lat", None)
                        new_state.pop("last_accepted_lon", None)
                        new_state.pop("last_accepted_timestamp", None)
                        new_state["consecutive_rejections"] = 0
                        return raw_lat, raw_lon, new_state
                    # Cascade-prevention: advance last_accepted toward the new point at capped
                    # speed so the NEXT point isn't even farther away.  We linearly interpolate
                    # between last_accepted and raw_point so the implied speed equals exactly
                    # max_realistic_speed_kmh.  This prevents N consecutive GPS-noise spikes
                    # from stacking distance and causing an 80-second live-dot freeze.
                    capped_dist_m = max_realistic_speed_kmh * time_diff_hours * 1000.0
                    alpha_cap = min(capped_dist_m / dist_m, 1.0)
                    capped_lat = last_lat + (lat - last_lat) * alpha_cap
                    capped_lon = last_lon + (lon - last_lon) * alpha_cap
                    new_state["last_accepted_lat"] = capped_lat
                    new_state["last_accepted_lon"] = capped_lon
                    if timestamp_s is not None:
                        new_state["last_accepted_timestamp"] = timestamp_s
                    return capped_lat, capped_lon, new_state
        else:
            # Fallback if no timestamps
            if dist_m > outlier_distance_m:
                new_state["consecutive_rejections"] = new_state.get("consecutive_rejections", 0) + 1
                if new_state["consecutive_rejections"] >= 20:
                    logger.critical(
                        "[GPS-FILTER] 20+ consecutive rejections (no-ts outlier) — auto-resetting. "
                        "Last=(%.5f,%.5f) Incoming=(%.5f,%.5f)",
                        last_lat or 0, last_lon or 0, raw_lat, raw_lon
                    )
                    new_state.pop("last_accepted_lat", None)
                    new_state.pop("last_accepted_lon", None)
                    new_state.pop("last_accepted_timestamp", None)
                    new_state["consecutive_rejections"] = 0
                    return raw_lat, raw_lon, new_state
                return last_lat, last_lon, new_state

    # Stationary lock
    is_stationary = (speed_kmh is not None and speed_kmh < stationary_speed_kmh)
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
            if drift_m < lock_drift_m:
                lock_count += 1
                new_state["lock_sample_count"] = lock_count
                if lock_count >= min_lock_samples:
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
            new_state["smoothed_lat"] = ema_alpha * lat + (1 - ema_alpha) * s_lat
            new_state["smoothed_lon"] = ema_alpha * lon + (1 - ema_alpha) * s_lon
            lat = new_state["smoothed_lat"]
            lon = new_state["smoothed_lon"]

    # Only updated if accepted — reset consecutive rejection counter
    new_state["consecutive_rejections"] = 0
    new_state["last_accepted_lat"] = lat
    new_state["last_accepted_lon"] = lon
    if timestamp_s is not None:
        new_state["last_accepted_timestamp"] = timestamp_s

    return lat, lon, new_state
