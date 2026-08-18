import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from duk_common.geo import haversine_m

logger = logging.getLogger(__name__)

STATIONARY_SPEED_KMH = 3.0
LOCK_DRIFT_M = 30.0
MIN_LOCK_SAMPLES = 2
MAX_REALISTIC_SPEED_KMH = 120.0
EMA_ALPHA = 0.7

def apply_gps_filter_stateless(
    raw_lat: float,
    raw_lon: float,
    speed_kmh: Optional[float],
    now: datetime,
    state: Dict[str, Any],
) -> Tuple[float, float, Dict[str, Any], str]:
    """
    Stateless version of the GPS filter.
    Takes a dictionary representing the state (loaded from Redis) and returns the updated state.
    """
    lat, lon = raw_lat, raw_lon
    reason_parts = []
    
    # Initialize defaults if empty
    speed_history = state.get("speed_history", [])
    if speed_kmh is not None:
        speed_history.append(speed_kmh)
        if len(speed_history) > 5:
            speed_history.pop(0)
    state["speed_history"] = speed_history
    
    last_lat = state.get("last_accepted_lat")
    last_lon = state.get("last_accepted_lon")
    last_time = state.get("last_accepted_time") # Should be isoformat string in dict
    
    if last_time:
        if isinstance(last_time, str):
            last_time_dt = datetime.fromisoformat(last_time)
        else:
            last_time_dt = last_time
            
        elapsed_s = (now - last_time_dt).total_seconds()
        if elapsed_s > 0 and last_lat is not None and last_lon is not None:
            dist_m = haversine_m(last_lat, last_lon, lat, lon)
            implied_speed_kmh = (dist_m / elapsed_s) * 3.6
            
            if implied_speed_kmh > MAX_REALISTIC_SPEED_KMH and elapsed_s < 30:
                logger.warning(f"[GPS_FILTER] Outlier rejected: implied {implied_speed_kmh:.0f} km/h")
                state["last_accepted_time"] = now.isoformat()
                return last_lat, last_lon, state, f"OUTLIER_REJECTED implied={implied_speed_kmh:.0f}km/h"
    
    is_stationary = (speed_kmh is not None and speed_kmh < STATIONARY_SPEED_KMH)
    was_locked = False
    
    locked_lat = state.get("locked_lat")
    locked_lon = state.get("locked_lon")
    lock_sample_count = state.get("lock_sample_count", 0)
    
    if is_stationary:
        if locked_lat is None:
            locked_lat = lat
            locked_lon = lon
            lock_sample_count = 1
            reason_parts.append("LOCK_INIT")
        else:
            drift_m = haversine_m(locked_lat, locked_lon, lat, lon)
            if drift_m < LOCK_DRIFT_M:
                lock_sample_count += 1
                if lock_sample_count >= MIN_LOCK_SAMPLES:
                    alpha = 1.0 / lock_sample_count
                    locked_lat = (1 - alpha) * locked_lat + alpha * lat
                    locked_lon = (1 - alpha) * locked_lon + alpha * lon
                    lat = locked_lat
                    lon = locked_lon
                    was_locked = True
                    reason_parts.append(f"LOCK_APPLIED drift={drift_m:.1f}m n={lock_sample_count}")
            else:
                if lock_sample_count >= MIN_LOCK_SAMPLES:
                    lock_sample_count = max(0, lock_sample_count - 2)
                    lat = locked_lat
                    lon = locked_lon
                    was_locked = True
                    reason_parts.append(f"LOCK_HELD_ESCAPE drift={drift_m:.1f}m")
                else:
                    locked_lat = lat
                    locked_lon = lon
                    lock_sample_count = 1
                    reason_parts.append(f"LOCK_RESET drift={drift_m:.1f}m")
    else:
        if locked_lat is not None:
            reason_parts.append("LOCK_RELEASED moving")
        locked_lat = None
        locked_lon = None
        lock_sample_count = 0

    state["locked_lat"] = locked_lat
    state["locked_lon"] = locked_lon
    state["lock_sample_count"] = lock_sample_count

    smoothed_lat = state.get("smoothed_lat")
    smoothed_lon = state.get("smoothed_lon")

    if not was_locked and not is_stationary:
        if smoothed_lat is None:
            smoothed_lat = lat
            smoothed_lon = lon
        else:
            smoothed_lat = EMA_ALPHA * lat + (1 - EMA_ALPHA) * smoothed_lat
            smoothed_lon = EMA_ALPHA * lon + (1 - EMA_ALPHA) * smoothed_lon
            lat = smoothed_lat
            lon = smoothed_lon
            reason_parts.append(f"EMA_SMOOTHED alpha={EMA_ALPHA}")
    else:
        smoothed_lat = lat
        smoothed_lon = lon

    state["smoothed_lat"] = smoothed_lat
    state["smoothed_lon"] = smoothed_lon
    state["last_accepted_lat"] = lat
    state["last_accepted_lon"] = lon
    state["last_accepted_time"] = now.isoformat()

    reason = " | ".join(reason_parts) if reason_parts else "PASSTHROUGH"
    return lat, lon, state, reason
