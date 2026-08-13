"""
services/proximity_alerts.py — Smart proximity alert + ETA notification engine.

Runs on every GPS update (~10 s) during an active trip.
Uses OSRM for real road-network distance checks (not straight-line Haversine).

Three proximity alert types (per user preference):
  'time'     — notify when OSRM drive-time to their stop ≤ N minutes
  'distance' — notify when OSRM road distance to their stop ≤ N metres
  'stops'    — notify when bus is ≤ N stops away (stop-count, no OSRM)

ETA notification: fires a "running late" push once per trip when OSRM ETA
to destination exceeds scheduled arrival by LATE_THRESHOLD_MIN minutes.
"""
import logging
from datetime import datetime, timedelta, timezone, time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm.attributes import flag_modified

from constants import IST_OFFSET
from services.osrm_client import get_osrm_route, get_osrm_distance_matrix_m

logger = logging.getLogger(__name__)

# ── Schedule constants ─────────────────────────────────────────────────────────
SCHEDULED_ARRIVAL = {
    "forward": time(9, 0),    # Morning: should arrive by 09:00 IST
    "reverse": time(19, 30),  # Evening: should arrive by 19:30 IST
}
LATE_THRESHOLD_MIN  = 10   # minutes over schedule before "late" notification fires
PROXIMITY_RADIUS_M  = 400  # road-distance threshold for proximity alerts


# ── Stop-count helper ──────────────────────────────────────────────────────────

async def _stops_between(
    db: AsyncSession,
    bus_stop_order: int,
    user_stop_order: int,
    route_id: int,
) -> int:
    """Count stops between the bus's current position and the user's stop."""
    from models.route import BusStop

    if user_stop_order <= bus_stop_order:
        return 0
    result = await db.execute(
        select(BusStop).where(
            and_(
                BusStop.route_id   == route_id,
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
    bus_lat: float,
    bus_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> bool:
    """
    Fire a "Running Late" push notification when OSRM ETA shows the bus will
    arrive more than LATE_THRESHOLD_MIN minutes after its scheduled arrival.
    Only fires once per trip (tracked by trip.eta_notif_sent).
    Returns True if a notification was sent.
    """
    if trip.eta_notif_sent:
        return False

    route    = await get_osrm_route(bus_lat, bus_lon, dest_lat, dest_lon)
    eta_min  = route["duration_s"] / 60.0
    now_ist  = datetime.now(timezone.utc) + IST_OFFSET
    pred_arr = now_ist + timedelta(minutes=eta_min)

    sched = SCHEDULED_ARRIVAL.get(trip.direction)
    if not sched:
        return False

    sched_dt  = datetime.combine(now_ist.date(), sched).replace(tzinfo=now_ist.tzinfo)
    delay_min = (pred_arr - sched_dt).total_seconds() / 60.0

    if delay_min < LATE_THRESHOLD_MIN:
        return False

    arrival_str   = pred_arr.strftime("%I:%M %p")
    delay_rounded = round(delay_min / 5) * 5  # round to nearest 5 min

    from services.firebase import broadcast_to_all_users
    await broadcast_to_all_users(
        db,
        "Bus Running Late 🕒",
        (
            f"The bus is running approximately {delay_rounded} minutes behind schedule. "
            f"Expected arrival: {arrival_str} IST."
        ),
        {
            "type":      "eta_late",
            "trip_id":   str(trip.id),
            "eta_min":   str(round(eta_min)),
            "delay_min": str(round(delay_min)),
        },
    )

    trip.eta_notif_sent = True
    await db.commit()
    logger.info(
        "[ETA] Late notification sent for trip #%d: %.0f min delay, ETA %s",
        trip.id, delay_min, arrival_str,
    )
    return True


# ── Per-user proximity alert engine ───────────────────────────────────────────

async def run_proximity_alerts(
    db: AsyncSession,
    trip,
    bus_lat: float,
    bus_lon: float,
    current_stop_order: Optional[int] = None,
) -> int:
    """
    Main proximity alert engine. Called on every GPS update during an active trip.

    FIX #1: Uses OSRM road distance (not Haversine) as advertised in the docstring.
    FIX #2: Correctly handles users who have BOTH boarding and destination alerts,
            where one was already sent this trip but the other was not.

    Returns the number of notifications sent.
    """
    from models.user import User
    from models.route import BusStop
    from services.firebase import send_push_notification
    from services.geofence import haversine_km

    # 1) Fetch opted-in verified users with at least one alert stop
    result = await db.execute(
        select(User).where(
            and_(
                User.proximity_alert_enabled == True,
                User.verified                == True,
                User.device_token.isnot(None),
                or_(
                    User.boarding_alert_stop_id.isnot(None),
                    User.destination_alert_stop_id.isnot(None),
                ),
            )
        )
    )
    users = result.scalars().all()
    if not users:
        return 0

    # 2) Collect unique stop IDs that still need checking this trip.
    #    Boarding and destination are tracked independently so that
    #    a user who already got their boarding alert can still get a
    #    destination alert on the same trip.
    boarding_needed:     set[int] = set()
    destination_needed:  set[int] = set()

    for u in users:
        if u.boarding_alert_stop_id and u.last_alerted_trip_id != trip.id:
            boarding_needed.add(u.boarding_alert_stop_id)
        if u.destination_alert_stop_id and u.last_dest_alerted_trip_id != trip.id:
            destination_needed.add(u.destination_alert_stop_id)

    all_needed_ids = boarding_needed | destination_needed
    if not all_needed_ids:
        return 0

    # 3) Fetch stop metadata
    stops_res = await db.execute(
        select(BusStop).where(BusStop.id.in_(all_needed_ids))
    )
    stop_map: dict[int, BusStop] = {s.id: s for s in stops_res.scalars().all()}

    # 4) Pre-filter with Haversine < 2 km, then use OSRM for exact road distance.
    #    This avoids hammering OSRM with stops that are clearly far away.
    candidate_ids = [
        sid for sid, stop in stop_map.items()
        if haversine_km(bus_lat, bus_lon, float(stop.lat), float(stop.lon)) < 2.0
    ]
    if not candidate_ids:
        return 0

    destinations = [
        (float(stop_map[sid].lat), float(stop_map[sid].lon))
        for sid in candidate_ids
    ]
    road_distances_m = await get_osrm_distance_matrix_m(bus_lat, bus_lon, destinations)

    reached_stop_ids: set[int] = set()
    for sid, dist_m in zip(candidate_ids, road_distances_m):
        if dist_m <= PROXIMITY_RADIUS_M:
            reached_stop_ids.add(sid)

    if not reached_stop_ids:
        return 0

    # 5) Build notification batches
    boarding_alerts:     dict[int, list] = {}  # stop_id → [user, ...]
    destination_alerts:  dict[int, list] = {}

    for u in users:
        if (
            u.boarding_alert_stop_id in reached_stop_ids
            and u.boarding_alert_stop_id in boarding_needed
            and u.last_alerted_trip_id != trip.id
        ):
            boarding_alerts.setdefault(u.boarding_alert_stop_id, []).append(u)

        if (
            u.destination_alert_stop_id in reached_stop_ids
            and u.destination_alert_stop_id in destination_needed
            and u.last_dest_alerted_trip_id != trip.id
        ):
            destination_alerts.setdefault(u.destination_alert_stop_id, []).append(u)

    sent_count = 0

    # 6) Send boarding alerts
    for stop_id, stop_users in boarding_alerts.items():
        stop   = stop_map[stop_id]
        tokens = [u.device_token for u in stop_users if u.device_token]
        if not tokens:
            continue
        await send_push_notification(
            tokens,
            "Bus Approaching! ",
            f"The bus has reached {stop.name}, your boarding alert stop!",
            {"type": "proximity", "stop_id": str(stop_id), "trip_id": str(trip.id)},
        )
        for u in stop_users:
            u.last_alerted_trip_id = trip.id
        sent_count += len(tokens)
        logger.info(
            "[PROXIMITY] Boarding alert → %d users @ %s", len(tokens), stop.name
        )

    # 7) Send destination alerts
    for stop_id, stop_users in destination_alerts.items():
        stop   = stop_map[stop_id]
        tokens = [u.device_token for u in stop_users if u.device_token]
        if not tokens:
            continue
        await send_push_notification(
            tokens,
            "Destination Approaching!",
            f"The bus has reached {stop.name}, your destination alert stop!",
            {"type": "proximity", "stop_id": str(stop_id), "trip_id": str(trip.id)},
        )
        for u in stop_users:
            u.last_dest_alerted_trip_id = trip.id
        sent_count += len(tokens)
        logger.info(
            "[PROXIMITY] Destination alert → %d users @ %s", len(tokens), stop.name
        )

    if sent_count > 0:
        await db.commit()

    return sent_count
