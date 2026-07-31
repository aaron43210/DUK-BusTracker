"""
services/proximity_alerts.py \u2014 Smart proximity alert + ETA notification engine.

Runs on every GPS update (every ~10 seconds) during an active trip.
Uses PostGIS + pgRouting for real road-network distance and ETA.

Three proximity alert types (per user preference):
  - 'time'     : notify when pgRouting ETA to their stop \u2264 N minutes
  - 'distance' : notify when pgRouting road distance \u2264 N metres
  - 'stops'    : notify when bus is \u2264 N stops away from their stop

ETA prediction: automatically fires a "running late" notification once per trip
  when the pgRouting-calculated ETA to destination exceeds the scheduled arrival.

Graceful fallback: if pgRouting tables (ways / ways_vertices_pgr) don't exist yet
  (i.e., the one-time OSM import hasn't been run), all functions return None and
  the system silently skips without crashing.
"""
import logging
from datetime import datetime, timedelta, timezone, time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
# Average bus speed in Trivandrum city traffic (km/h)
BUS_AVG_SPEED_KMH = 20.0

# Scheduled arrival times (IST) — bus is considered "late" if ETA > these + LATE_THRESHOLD_MIN
# These match real-world DUK schedule; can be moved to DB config later.
SCHEDULED_ARRIVAL = {
    "forward": time(9, 30),   # Morning: should arrive DUK by 09:30 IST
    "reverse": time(19, 30),  # Evening: should arrive Central Poly by 19:30 IST
}

# How many minutes over-scheduled before we fire the "running late" auto-notification
LATE_THRESHOLD_MIN = 10

# IST offset
IST = timedelta(hours=5, minutes=30)


# ── pgRouting helpers ──────────────────────────────────────────────────────────

async def _pgr_available(db: AsyncSession) -> bool:
    """Check if the ways table (OSM road network) exists in the database."""
    try:
        result = await db.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='ways')"
        ))
        return result.scalar()
    except Exception:
        return False


async def _nearest_node(db: AsyncSession, lat: float, lon: float) -> Optional[int]:
    """Return the nearest pgRouting node ID in ways_vertices_pgr for a given coordinate."""
    try:
        result = await db.execute(text("""
            SELECT id FROM ways_vertices_pgr
            ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
            LIMIT 1
        """), {"lat": lat, "lon": lon})
        row = result.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.debug("[PROXIMITY] nearest_node error: %s", e)
        return None


async def _road_distance_m(db: AsyncSession, from_lat: float, from_lon: float,
                            to_lat: float, to_lon: float) -> Optional[float]:
    """
    Calculate the exact driving distance in metres between two coordinates
    using pgRouting Dijkstra on the OSM road network.
    Returns None if pgRouting is not available or no path found.
    """
    from_node = await _nearest_node(db, from_lat, from_lon)
    to_node   = await _nearest_node(db, to_lat, to_lon)
    if not from_node or not to_node or from_node == to_node:
        return None
    try:
        result = await db.execute(text("""
            SELECT SUM(w.length_m) AS road_distance_m
            FROM pgr_dijkstra(
                'SELECT id, source, target, cost, reverse_cost FROM ways',
                :from_node,
                :to_node,
                directed := false
            ) path
            JOIN ways w ON path.edge = w.id
        """), {"from_node": from_node, "to_node": to_node})
        row = result.fetchone()
        return float(row[0]) if row and row[0] else None
    except Exception as e:
        logger.debug("[PROXIMITY] road_distance error: %s", e)
        return None


async def _road_distance_from_stop_order(
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


# ── ETA calculation ────────────────────────────────────────────────────────────

async def calculate_eta_minutes(
    db: AsyncSession,
    bus_lat: float, bus_lon: float,
    dest_lat: float, dest_lon: float,
) -> Optional[float]:
    """
    Calculate ETA in minutes from bus's current position to destination using
    pgRouting road distance divided by average bus speed.
    """
    dist_m = await _road_distance_m(db, bus_lat, bus_lon, dest_lat, dest_lon)
    if dist_m is None:
        return None
    dist_km = dist_m / 1000.0
    return (dist_km / BUS_AVG_SPEED_KMH) * 60.0


# ── ETA-based "Running Late" auto-notification ─────────────────────────────────

async def check_eta_late_notification(
    db: AsyncSession,
    trip,
    bus_lat: float, bus_lon: float,
    dest_lat: float, dest_lon: float,
) -> bool:
    """
    Automatically fire a "Running Late" push notification if pgRouting ETA
    shows the bus will arrive more than LATE_THRESHOLD_MIN minutes after schedule.
    Only fires ONCE per trip (tracked by trip.eta_notif_sent flag).

    Returns True if a notification was sent.
    """
    # Already sent for this trip — don't spam
    if trip.eta_notif_sent:
        return False

    if not await _pgr_available(db):
        return False

    eta_min = await calculate_eta_minutes(db, bus_lat, bus_lon, dest_lat, dest_lon)
    if eta_min is None:
        return False

    now_ist = datetime.now(timezone.utc) + IST
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
    arrival_str = predicted_arrival.strftime("%I:%M %p")
    delay_rounded = round(delay_min / 5) * 5   # round to nearest 5 min
    title = "Bus Running Late \ud83d\udd52"
    body  = (
        f"The bus is running approximately {delay_rounded} minutes behind schedule. "
        f"Expected arrival: {arrival_str} IST."
    )

    from services.firebase import broadcast_to_all_users
    await broadcast_to_all_users(db, title, body, {
        "type":       "eta_late",
        "trip_id":    str(trip.id),
        "eta_min":    str(round(eta_min)),
        "delay_min":  str(round(delay_min)),
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

    Groups users by their boarding stop to avoid redundant pgRouting queries
    (one pgr_dijkstra call per unique stop, not one per user).

    Returns the number of notifications sent.
    """
    if not await _pgr_available(db):
        return 0

    from models.user import User
    from models.route import BusStop
    from services.firebase import send_push_notification

    # Fetch all opted-in users who haven't been alerted for this trip yet
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

    # Group users by stop_id to avoid duplicate pgRouting calls
    stops_to_check: dict[int, list] = {}
    for u in users:
        stops_to_check.setdefault(u.boarding_stop_id, []).append(u)

    # Fetch all relevant stop metadata in one query
    stop_ids = list(stops_to_check.keys())
    stops_res = await db.execute(
        select(BusStop).where(BusStop.id.in_(stop_ids))
    )
    stop_map = {s.id: s for s in stops_res.scalars().all()}

    sent_count = 0

    for stop_id, stop_users in stops_to_check.items():
        stop = stop_map.get(stop_id)
        if not stop:
            continue

        # Use the first user's alert config (all users at same stop get same notification)
        # In practice, users at the same stop should have similar configs
        sample_user = stop_users[0]
        alert_type  = sample_user.proximity_alert_type
        alert_value = sample_user.proximity_alert_value

        if not alert_type or not alert_value:
            continue

        triggered = False
        debug_metric = None

        if alert_type == "distance":
            # Real road distance in metres
            dist_m = await _road_distance_m(db, bus_lat, bus_lon, stop.lat, stop.lon)
            if dist_m is not None and dist_m <= alert_value:
                triggered = True
                debug_metric = f"{dist_m:.0f}m road distance"

        elif alert_type == "time":
            # ETA in minutes
            eta_min = await calculate_eta_minutes(db, bus_lat, bus_lon, stop.lat, stop.lon)
            if eta_min is not None and eta_min <= alert_value:
                triggered = True
                debug_metric = f"{eta_min:.1f} min ETA"

        elif alert_type == "stops" and current_stop_order is not None:
            # Number of stops away
            stops_away = await _road_distance_from_stop_order(
                db, current_stop_order, stop.order_index, stop.route_id
            )
            if stops_away <= alert_value:
                triggered = True
                debug_metric = f"{stops_away} stops away"

        if not triggered:
            continue

        # Build the notification message
        if alert_type == "distance":
            dist_km = alert_value / 1000
            body = f"\ud83d\ude8c The bus is about {dist_km:.1f} km away from {stop.name} by road!"
        elif alert_type == "time":
            body = f"\ud83d\ude8c The bus is about {alert_value} minutes away from {stop.name}!"
        else:
            body = f"\ud83d\ude8c The bus is {alert_value} stop(s) away from {stop.name}!"

        title = "Bus Approaching! \ud83d\ude8c"

        # Fire FCM push notification to all users at this stop
        tokens = [u.device_token for u in stop_users if u.device_token]
        if tokens:
            await send_push_notification(tokens, title, body, {
                "type":    "proximity",
                "stop_id": str(stop_id),
                "trip_id": str(trip.id),
            })

            # Mark each user as alerted for this trip
            for u in stop_users:
                u.last_alerted_trip_id = trip.id

            await db.commit()
            sent_count += len(tokens)
            logger.info(
                "[PROXIMITY] Alert sent to %d users at '%s' (%s). Metric: %s",
                len(tokens), stop.name, alert_type, debug_metric,
            )

    return sent_count
