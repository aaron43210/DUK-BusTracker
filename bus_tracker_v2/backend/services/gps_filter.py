"""
Single source of truth for GPS coordinate cleaning.
All paths (WebSocket ingestion, HTTP POST, direct Supabase reads) go through here.
"""
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


@dataclass
class FilteredPosition:
    lat: float
    lon: float
    raw_lat: float
    raw_lon: float
    was_locked: bool        # True = stationary lock applied
    was_snapped: bool       # True = Valhalla road snap applied
    snap_delta_m: float     # How far Valhalla moved the point
    filter_reason: str      # Human-readable explanation for debugging


@dataclass 
class GPSFilterState:
    """
    Persistent filter state that must survive across requests.
    Store this in Redis or a DB row for multi-process deployments.
    For single-process: keep as module singleton.
    """
    # Stationary lock
    locked_lat: Optional[float] = None
    locked_lon: Optional[float] = None
    lock_sample_count: int = 0          # How many consecutive stationary pings
    
    # Kalman-lite smoothing (exponential moving average)
    smoothed_lat: Optional[float] = None
    smoothed_lon: Optional[float] = None
    
    # Outlier rejection
    last_accepted_lat: Optional[float] = None
    last_accepted_lon: Optional[float] = None
    last_accepted_time: Optional[datetime] = None
    
    # Speed history for adaptive thresholds
    speed_history: list = field(default_factory=list)
    
    def record_speed(self, speed_kmh: float):
        self.speed_history.append(speed_kmh)
        if len(self.speed_history) > 5:
            self.speed_history.pop(0)
    
    @property
    def avg_speed(self) -> float:
        if not self.speed_history:
            return 0.0
        return sum(self.speed_history) / len(self.speed_history)


# Module-level singleton — replace with Redis-backed state for multi-worker setups
_GPS_STATE = GPSFilterState()


# ── Tuning Constants ───────────────────────────────────────────────────────────

# Stationary detection: bus must be below this speed AND displacement < LOCK_DRIFT_M
STATIONARY_SPEED_KMH   = 3.0     # km/h — raised from 5.0 to catch slow crawl
LOCK_DRIFT_M           = 30.0    # metres — tighter than old 50m; locks faster
MIN_LOCK_SAMPLES       = 2       # consecutive stationary pings before we trust the lock

# Outlier rejection: if the bus appears to teleport faster than physically possible,
# reject the point and keep the previous position.
MAX_REALISTIC_SPEED_KMH = 120.0  # km/h — anything above this is a GPS glitch

# Exponential smoothing factor: 0.0 = no smoothing, 1.0 = no update
# 0.3 means new reading contributes 70%, history 30%
EMA_ALPHA = 0.7   # Aggressive smoothing when stationary; lighter when moving


def apply_gps_filter(
    raw_lat: float,
    raw_lon: float,
    speed_kmh: Optional[float],
    now: datetime,
    state: GPSFilterState = None,
) -> tuple[float, float, FilteredPosition]:
    """
    Apply multi-stage GPS filter:
      1. Outlier rejection   (teleport guard)
      2. Stationary lock     (jitter suppression)
      3. EMA smoothing       (trajectory smoothing while moving)

    Returns (filtered_lat, filtered_lon, debug_info)
    
    NOTE: Valhalla snapping is NOT done here — it's async and handled separately.
    This function is synchronous and safe to call from anywhere.
    """
    if state is None:
        state = _GPS_STATE

    lat, lon = raw_lat, raw_lon
    reason_parts = []

    if speed_kmh is not None:
        state.record_speed(speed_kmh)

    # ── Stage 1: Outlier / Teleport Rejection ─────────────────────────────────
    if (
        state.last_accepted_lat is not None
        and state.last_accepted_time is not None
    ):
        elapsed_s = (now - state.last_accepted_time).total_seconds()
        if elapsed_s > 0:
            dist_m = haversine_m(
                state.last_accepted_lat, state.last_accepted_lon, lat, lon
            )
            implied_speed_kmh = (dist_m / elapsed_s) * 3.6

            if implied_speed_kmh > MAX_REALISTIC_SPEED_KMH and elapsed_s < 30:
                # Hard reject: use last accepted position
                lat = state.last_accepted_lat
                lon = state.last_accepted_lon
                logger.warning(
                    "[GPS_FILTER] Outlier rejected: implied %.0f km/h (%.0fm in %.0fs)",
                    implied_speed_kmh, dist_m, elapsed_s,
                )
                return lat, lon, FilteredPosition(
                    lat=lat, lon=lon,
                    raw_lat=raw_lat, raw_lon=raw_lon,
                    was_locked=False, was_snapped=False,
                    snap_delta_m=0.0,
                    filter_reason=f"OUTLIER_REJECTED implied={implied_speed_kmh:.0f}km/h",
                )

    # ── Stage 2: Stationary Lock ──────────────────────────────────────────────
    is_stationary = (speed_kmh is not None and speed_kmh < STATIONARY_SPEED_KMH)
    was_locked = False

    if is_stationary:
        if state.locked_lat is None:
            # First stationary ping — tentative lock
            state.locked_lat = lat
            state.locked_lon = lon
            state.lock_sample_count = 1
            reason_parts.append("LOCK_INIT")
        else:
            drift_m = haversine_m(state.locked_lat, state.locked_lon, lat, lon)

            if drift_m < LOCK_DRIFT_M:
                # Within drift tolerance — apply lock if we have enough samples
                state.lock_sample_count += 1
                if state.lock_sample_count >= MIN_LOCK_SAMPLES:
                    # Update lock centroid with weighted average (Welford-lite)
                    alpha = 1.0 / state.lock_sample_count
                    state.locked_lat = (1 - alpha) * state.locked_lat + alpha * lat
                    state.locked_lon = (1 - alpha) * state.locked_lon + alpha * lon
                    lat = state.locked_lat
                    lon = state.locked_lon
                    was_locked = True
                    reason_parts.append(f"LOCK_APPLIED drift={drift_m:.1f}m n={state.lock_sample_count}")
            else:
                # Drifted too far — bus may have genuinely moved
                # Don't immediately reset: wait for a second ping to confirm
                if state.lock_sample_count >= MIN_LOCK_SAMPLES:
                    # We had a solid lock — require 2+ consecutive escapes before releasing
                    state.lock_sample_count = max(0, state.lock_sample_count - 2)
                    # Keep old lock for this ping
                    lat = state.locked_lat
                    lon = state.locked_lon
                    was_locked = True
                    reason_parts.append(f"LOCK_HELD_ESCAPE drift={drift_m:.1f}m")
                else:
                    # Weak lock — release and update
                    state.locked_lat = lat
                    state.locked_lon = lon
                    state.lock_sample_count = 1
                    reason_parts.append(f"LOCK_RESET drift={drift_m:.1f}m")
    else:
        # Bus is moving — release stationary lock
        if state.locked_lat is not None:
            reason_parts.append("LOCK_RELEASED moving")
        state.locked_lat = None
        state.locked_lon = None
        state.lock_sample_count = 0

    # ── Stage 3: EMA Smoothing (only while moving, to avoid over-smoothing stops) ──
    if not was_locked and not is_stationary:
        if state.smoothed_lat is None:
            state.smoothed_lat = lat
            state.smoothed_lon = lon
        else:
            state.smoothed_lat = EMA_ALPHA * lat + (1 - EMA_ALPHA) * state.smoothed_lat
            state.smoothed_lon = EMA_ALPHA * lon + (1 - EMA_ALPHA) * state.smoothed_lon
            lat = state.smoothed_lat
            lon = state.smoothed_lon
            reason_parts.append(f"EMA_SMOOTHED alpha={EMA_ALPHA}")
    else:
        # Reset smoother when stationary so it picks up fresh when moving resumes
        state.smoothed_lat = lat
        state.smoothed_lon = lon

    # Update accepted position
    state.last_accepted_lat = lat
    state.last_accepted_lon = lon
    state.last_accepted_time = now

    return lat, lon, FilteredPosition(
        lat=lat, lon=lon,
        raw_lat=raw_lat, raw_lon=raw_lon,
        was_locked=was_locked,
        was_snapped=False,        # Set by caller after Valhalla snap
        snap_delta_m=0.0,
        filter_reason=" | ".join(reason_parts) if reason_parts else "PASSTHROUGH",
    )


def get_filter_state() -> GPSFilterState:
    """Return the global filter state (for inspection/debugging)."""
    return _GPS_STATE


def reset_filter_state():
    """Reset all filter state — call on server restart or trip change."""
    global _GPS_STATE
    _GPS_STATE = GPSFilterState()
    logger.info("[GPS_FILTER] State reset")
