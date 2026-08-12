"""
services/proximity_alerts.py — Smart proximity alert + ETA notification engine.

Runs on every GPS update (every ~10 seconds) during an active trip.
Uses OSRM (running on a local port) for exact road-network distance and ETA.

Three proximity alert types (per user preference):
  - 'time'     : notify when OSRM drive-time to their stop ≤ N minutes
  - 'distance' : notify when OSRM road distance to their stop ≤ N metres
  - 'stops'    : notify when bus is ≤ N stops away (simple stop-count, no OSRM needed)

ETA prediction: automatically fires a "running late" notification once per trip
  when the OSRM-calculated ETA to destination exceeds the scheduled arrival.

All distance / duration checks fall back to Haversine + speed heuristic automatically
inside get_osrm_route() if OSRM is temporarily unreachable.
"""
import logging
from datetime import datetime, timedelta, timezone, time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from constants import IST_OFFSET
from services.osrm_client import get_osrm_route

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
# Scheduled arrival times (IST) — bus is considered "late" if ETA > these + LATE_THRESHOLD_MIN
SCHEDULED_ARRIVAL = {
    "forward": time(9, 30),   # Morning: should arrive DUK by 09:30 IST
    "reverse": time(19, 30),  # Evening: should arrive Central Poly by 19:30 IST
}

# How many minutes over-scheduled before we fire the "running late" auto-notification
LATE_THRESHOLD_MIN = 10


# ── Stop-count helper (no OSRM needed) ────────────────────────────────────────

async def _stops_between(
    db: AsyncSession, bus_stop_order: int, user_stop_order: int, route_id: int
) -> int:
    """Count how many stops lie between the bus's current stop and the user's stop."""
    from models.route import BusStop
    if user_stop_order <= bus_stop_order:
        return 0
    result = await db.execute(
        select(BusStop).where(
            and_(
                BusStop.route_id == route_id,
                BusStop.order_index > bus_stop_order,
                BusStop.order_index <= user_stop_order,
            )
        )
    )
    return len(result.scalars().all())


# ── ETA-based "Running Late" auto-notification ─────────────────────────────────

async def check_eta_late_notification(
    db: AsyncSession,
    trip,
    bus_lat: float, bus_lon: float,
    dest_lat: float, dest_lon: float,
) -> bool:
    """
    Automatically fire a "Running Late" push notification if OSRM ETA
    shows the bus will arrive more than LATE_THRESHOLD_MIN minutes after schedule.
    Only fires ONCE per trip (tracked by trip.eta_notif_sent flag).

    Returns True if a notification was sent.
    """
    # Already sent for this trip — don't spam
    if trip.eta_notif_sent:
        return False

    route = await get_osrm_route(bus_lat, bus_lon, dest_lat, dest_lon)
    eta_min = route["duration_s"] / 60.0

    now_ist = datetime.now(timezone.utc) + IST_OFFSET
    predicted_arrival = now_ist + timedelta(minutes=eta_min)

    # Compare against scheduled arrival
    sched = SCHEDULED_ARRIVAL.get(trip.direction)
    if not sched:
        return False

    sched_dt = datetime.combine(now_ist.date(), sched).replace(tzinfo=now_ist.tzinfo)
    delay_min = (predicted_arrival - sched_dt).total_seconds() / 60

    if delay_min < LATE_THRESHOLD_MIN:
        return False

    # Fire the notification
    arrival_str   = predicted_arrival.strftime("%I:%M %p")
    delay_rounded = round(delay_min / 5) * 5   # round to nearest 5 min
    title = "Bus Running Late 🕒"
    body  = (
        f"The bus is running approximately {delay_rounded} minutes behind schedule. "
        f"Expected arrival: {arrival_str} IST."
    )

    from services.firebase import broadcast_to_all_users
    await broadcast_to_all_users(db, title, body, {
        "type":      "eta_late",
        "trip_id":   str(trip.id),
        "eta_min":   str(round(eta_min)),
        "delay_min": str(round(delay_min)),
    })

    # Mark as sent so it doesn't repeat
    trip.eta_notif_sent = True
    await db.commit()
    logger.info(
        "[ETA] Auto-late notification sent for trip #%d: %.0f min delay, ETA %s",
        trip.id, delay_min, arrival_str,
    )
    return True


# ── Per-user proximity alert engine ───────────────────────────────────────────

async def run_proximity_alerts(
    db: AsyncSession,
    trip,
    bus_lat: float, bus_lon: float,
    current_stop_order: Optional[int] = None,
) -> int:
    """
    Main proximity alert engine. Called on every GPS update during an active trip.
    Checks all opted-in users to see if the bus has crossed their alert threshold.

    Groups users by their boarding stop to avoid redundant OSRM queries
    (one OSRM call per unique stop, not one per user).

    Returns the number of notifications sent.
    """
    from models.user import User
    from models.route import BusStop
    from services.firebase import send_push_notification

    # Fetch all opted-in, verified users with a device token who haven't been
    # alerted for this trip yet
    result = await db.execute(
        select(User).where(
            and_(
                User.proximity_alert_enabled == True,
                User.verified == True,
                User.device_token.isnot(None),
                User.boarding_stop_id.isnot(None),
                # Don't alert again for the same trip
                (User.last_alerted_trip_id != trip.id) | User.last_alerted_trip_id.is_(None),
            )
        )
    )
    users = result.scalars().all()
    if not users:
        return 0

    # Group users by stop_id to avoid duplicate OSRM calls
    stops_to_check: dict[int, list] = {}
    for u in users:
        stops_to_check.setdefault(u.boarding_stop_id, []).append(u)

    # Fetch all relevant stop metadata in one query
    stop_ids  = list(stops_to_check.keys())
    stops_res = await db.execute(
        select(BusStop).where(BusStop.id.in_(stop_ids))
    )
    stop_map = {s.id: s for s in stops_res.scalars().all()}

    sent_count = 0

    for stop_id, stop_users in stops_to_check.items():
        stop = stop_map.get(stop_id)
        if not stop:
            continue

        # Use the first user's alert config — all users at the same stop share
        # the same physical distance so the trigger condition is the same for all.
        sample_user = stop_users[0]
        alert_type  = sample_user.proximity_alert_type
        alert_value = sample_user.proximity_alert_value

        if not alert_type or not alert_value:
            continue

        triggered    = False
        debug_metric = None

        if alert_type == "distance":
            # Real road distance via OSRM
            route_info = await get_osrm_route(bus_lat, bus_lon, stop.lat, stop.lon)
            dist_m     = route_info["distance_m"]
            if dist_m <= alert_value:
                triggered    = True
                debug_metric = f"{dist_m:.0f}m road distance (OSRM={'yes' if route_info['from_osrm'] else 'fallback'})"

        elif alert_type == "time":
            # Drive-time via OSRM (seconds → minutes)
            route_info = await get_osrm_route(bus_lat, bus_lon, stop.lat, stop.lon)
            eta_min    = route_info["duration_s"] / 60.0
            if eta_min <= alert_value:
                triggered    = True
                debug_metric = f"{eta_min:.1f} min ETA (OSRM={'yes' if route_info['from_osrm'] else 'fallback'})"

        elif alert_type == "stops" and current_stop_order is not None:
            # Simple stop-count — no OSRM needed
            stops_away = await _stops_between(
                db, current_stop_order, stop.order_index, stop.route_id
            )
            if stops_away <= alert_value:
                triggered    = True
                debug_metric = f"{stops_away} stops away"

        if not triggered:
            continue

        # Build the notification message
        if alert_type == "distance":
            dist_km = alert_value / 1000
            body = f"🚌 The bus is about {dist_km:.1f} km away from {stop.name} by road!"
        elif alert_type == "time":
            body = f"🚌 The bus is about {alert_value} minutes away from {stop.name}!"
        else:
            body = f"🚌 The bus is {alert_value} stop(s) away from {stop.name}!"

        title = "Bus Approaching! 🚌"

        # Fire FCM push notification to all opted-in users at this stop
        tokens = [u.device_token for u in stop_users if u.device_token]
        if tokens:
            await send_push_notification(tokens, title, body, {
                "type":    "proximity",
                "stop_id": str(stop_id),
                "stop_name": stop.name,
                "trip_id": str(trip.id),
            })

            # Mark each user as alerted for this trip so we don't spam them
            for u in stop_users:
                u.last_alerted_trip_id = trip.id

            await db.commit()
            sent_count += len(tokens)
            logger.info(
                "[PROXIMITY] Alert sent to %d user(s) at '%s' (%s). Metric: %s",
                len(tokens), stop.name, alert_type, debug_metric,
            )

    return sent_count
