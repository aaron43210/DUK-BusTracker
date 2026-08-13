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
from sqlalchemy import select, and_, or_

from constants import IST_OFFSET
from services.osrm_client import get_osrm_route

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
# Scheduled arrival times (IST) — bus is considered "late" if ETA > these + LATE_THRESHOLD_MIN
SCHEDULED_ARRIVAL = {
    "forward": time(9, 00),   # Morning: should arrive DUK by 09:30 IST
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
    Checks opted-in users to see if the bus has reached their specified alert stops
    (boarding_alert_stop_id and/or destination_alert_stop_id).
    
    Returns the number of notifications sent.
    """
    from models.user import User
    from models.route import BusStop
    from services.firebase import send_push_notification
    from services.geofence import haversine_km

    # 1) Fetch all opted-in, verified users who have at least one alert stop configured
    result = await db.execute(
        select(User).where(
            and_(
                User.proximity_alert_enabled == True,
                User.verified == True,
                User.device_token.isnot(None),
                or_(
                    User.boarding_alert_stop_id.isnot(None),
                    User.destination_alert_stop_id.isnot(None)
                )
            )
        )
    )
    users = result.scalars().all()
    if not users:
        return 0

    # 2) Gather unique alert stop IDs across all users
    unique_stop_ids = set()
    for u in users:
        if u.boarding_alert_stop_id and (u.last_alerted_trip_id != trip.id):
            unique_stop_ids.add(u.boarding_alert_stop_id)
        if u.destination_alert_stop_id and (u.last_dest_alerted_trip_id != trip.id):
            unique_stop_ids.add(u.destination_alert_stop_id)

    if not unique_stop_ids:
        return 0

    # 3) Fetch stop metadata for the required stops
    stops_res = await db.execute(
        select(BusStop).where(BusStop.id.in_(unique_stop_ids))
    )
    stop_map = {s.id: s for s in stops_res.scalars().all()}

    # 4) Determine which stops the bus has reached (within 400m haversine)
    reached_stop_ids = set()
    for stop_id, stop in stop_map.items():
        # Check direct distance
        dist_km = haversine_km(bus_lat, bus_lon, stop.lat, stop.lon)
        if dist_km <= 0.4:  # 400 meters
            reached_stop_ids.add(stop_id)

    if not reached_stop_ids:
        return 0

    sent_count = 0
    # Group users by the reached stops so we can send push notifications in bulk
    boarding_alerts = {}     # stop_id -> list of users
    destination_alerts = {}  # stop_id -> list of users

    for u in users:
        # Check boarding alert
        if u.boarding_alert_stop_id in reached_stop_ids and u.last_alerted_trip_id != trip.id:
            boarding_alerts.setdefault(u.boarding_alert_stop_id, []).append(u)
        
        # Check destination alert
        if u.destination_alert_stop_id in reached_stop_ids and u.last_dest_alerted_trip_id != trip.id:
            destination_alerts.setdefault(u.destination_alert_stop_id, []).append(u)

    # 5) Send notifications and update DB
    for stop_id, stop_users in boarding_alerts.items():
        stop = stop_map[stop_id]
        tokens = [u.device_token for u in stop_users if u.device_token]
        if tokens:
            await send_push_notification(tokens, "Bus Approaching! 🚌", f"The bus has reached {stop.name}, your selected boarding alert stop!", {
                "type": "proximity", "stop_id": str(stop_id), "trip_id": str(trip.id)
            })
            for u in stop_users:
                u.last_alerted_trip_id = trip.id
            sent_count += len(tokens)
            logger.info(f"[PROXIMITY] Sent boarding alert to {len(tokens)} users for stop {stop.name}")

    for stop_id, stop_users in destination_alerts.items():
        stop = stop_map[stop_id]
        tokens = [u.device_token for u in stop_users if u.device_token]
        if tokens:
            await send_push_notification(tokens, "Destination Approaching! 🚌", f"The bus has reached {stop.name}, your selected destination alert stop!", {
                "type": "proximity", "stop_id": str(stop_id), "trip_id": str(trip.id)
            })
            for u in stop_users:
                u.last_dest_alerted_trip_id = trip.id
            sent_count += len(tokens)
            logger.info(f"[PROXIMITY] Sent destination alert to {len(tokens)} users for stop {stop.name}")

    if sent_count > 0:
        await db.commit()

    return sent_count
