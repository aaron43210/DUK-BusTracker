# libs/duk_common/constants.py
"""
Shared constants used across all DUK Bus Tracker microservices.

Previously each service defined its own copy of IST_OFFSET and schedule
window constants, which risked them drifting out of sync. Import from here.
"""
from datetime import timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Timezone
IST_OFFSET = timedelta(hours=5, minutes=30)
IST_TZ = ZoneInfo("Asia/Kolkata")

# Bus Schedule Windows (minutes since midnight IST)
MORNING_START_MINS  = 360   # 06:00 IST
MORNING_END_MINS    = 660   # 11:00 IST - latest time to start a morning trip
EVENING_START_MINS  = 1020  # 17:00 IST
EVENING_END_MINS    = 1230  # 20:30 IST - latest time to start an evening trip

# Pre-wait windows (bus departs soon - show waiting state to PWA)
MORNING_WAIT_START  = 330   # 05:30 IST
EVENING_WAIT_START  = 990   # 16:30 IST

def normalize_direction(direction: str | None) -> str:
    """Normalizes direction strings to 'forward' or 'reverse'."""
    if not direction:
        return "unknown"
    d = direction.lower().strip()
    if d in ("forward", "morning", "m"):
        return "forward"
    if d in ("reverse", "evening", "e"):
        return "reverse"
    if d == "unscheduled":
        return "unscheduled"
    return "unknown"
