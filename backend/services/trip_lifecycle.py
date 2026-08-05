"""
services/trip_lifecycle.py — GPS-driven automatic trip state machine.

State transitions:
  scheduled → on_trip   : movement detected (> 50m from route start) in correct time window
  on_trip   → completed : bus within 300m of destination + POWER_OFF + 5-min grace period

Time windows (IST):
  Morning (forward) : actions visible from 06:00, on_trip only after movement detected
  Evening (reverse) : actions visible from 10:00, on_trip only after 17:00 + movement
"""
import asyncio
import logging
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from services.osrm_client import get_osrm_distance_m, get_osrm_distance_matrix_m
from services.geofence import haversine_km
from services.proximity_alerts import check_eta_late_notification, run_proximity_alerts
from constants import IST_OFFSET, MORNING_END_MINS, EVENING_END_MINS
from models.trip import Trip
from models.gps import GpsLog
from models.route import BusStop

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
DESTINATION_RADIUS_M  = 300   # metres — bus "arrived" when within this of destination
GRACE_PERIOD_S        = 1200  # seconds — wait after POWER_OFF before marking completed (20 min)
MOVEMENT_THRESHOLD_M  = 50    # metres — movement from start point to trigger on_trip

# ── Fallback coordinates (used if admin hasn't configured terminal stops yet) ──
_FALLBACK_MORNING_ORIGIN      = (8.5350, 76.9908)  # Central Polytechnic
_FALLBACK_MORNING_DESTINATION = (8.6158, 76.8527)  # Digital University Kerala
_FALLBACK_EVENING_ORIGIN      = (8.6158, 76.8527)  # Digital University Kerala
_FALLBACK_EVENING_DESTINATION = (8.5350, 76.9908)  # Central Polytechnic

# Pending completion grace timers: trip_id → asyncio.Task
COMPLETION_TIMERS: dict[int, asyncio.Task] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────
def to_ist(utc_dt: datetime) -> datetime:
    return utc_dt + IST_OFFSET


async def get_start_coords(db: AsyncSession, direction: str) -> tuple[float, float]:
    """
    Return the trip origin coordinates for a given direction.
    Reads admin-configured role from DB; falls back to hardcoded coords if none set.
    """
    col = BusStop.is_morning_origin if direction == "forward" else BusStop.is_evening_origin
    result = await db.execute(select(BusStop).where(col == True).limit(1))
    stop = result.scalar_one_or_none()
    if stop:
        return (float(stop.lat), float(stop.lon))
    return _FALLBACK_MORNING_ORIGIN if direction == "forward" else _FALLBACK_EVENING_ORIGIN


async def get_destination_coords(db: AsyncSession, direction: str) -> tuple[float, float]:
    """
    Return the trip destination coordinates for a given direction.
    Reads admin-configured role from DB; falls back to hardcoded coords if none set.
    """
    col = BusStop.is_morning_destination if direction == "forward" else BusStop.is_evening_destination
    result = await db.execute(select(BusStop).where(col == True).limit(1))
    stop = result.scalar_one_or_none()
    if stop:
        return (float(stop.lat), float(stop.lon))
    return _FALLBACK_MORNING_DESTINATION if direction == "forward" else _FALLBACK_EVENING_DESTINATION


# ── Trip lookup ────────────────────────────────────────────────────────────────
async def get_active_trip(db: AsyncSession, now_ist: datetime):
    """
    Return today's trip that is eligible for GPS-driven transitions based on the time.
    Morning (forward)  eligible from 06:00 IST.
    Evening (reverse)  eligible from 17:00 IST.
    """
    # Uses Trip

    today = date.today()
    hour  = now_ist.hour

    if 6 <= hour < 11:
        direction = 'forward'
    elif hour >= 17:
        direction = 'reverse'
    else:
        return None

    result = await db.execute(
        select(Trip).where(
            and_(
                Trip.date      == today,
                Trip.direction == direction,
                Trip.status.in_(['scheduled', 'on_trip', 'late']),
            )
        ).limit(1)
    )
    return result.scalar_one_or_none()


# ── State transitions ──────────────────────────────────────────────────────────
async def _mark_on_trip(db: AsyncSession, trip, manager, now_utc: datetime):
    """scheduled / late → on_trip."""
    trip.status     = 'on_trip'
    trip.started_at = now_utc
    await db.commit()
    logger.info("[LIFECYCLE] Trip #%d → on_trip", trip.id)
    await manager.broadcast({
        "type":      "trip_status",
        "trip_id":   trip.id,
        "status":    "on_trip",
        "direction": trip.direction,
    })


async def _complete_trip_after_grace(trip_id: int, manager):
    """
    Background task: waits GRACE_PERIOD_S seconds then marks the trip completed.
    Cancelled automatically if POWER_ON arrives in the meantime.
    """
    await asyncio.sleep(GRACE_PERIOD_S)
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip   = result.scalar_one_or_none()
        if trip and trip.status in ('on_trip', 'late'):
            trip.status   = 'completed'
            trip.ended_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("[LIFECYCLE] Trip #%d auto-completed after %ds grace", trip_id, GRACE_PERIOD_S)
            await manager.broadcast({
                "type":    "trip_status",
                "trip_id": trip_id,
                "status":  "completed",
            })

    COMPLETION_TIMERS.pop(trip_id, None)


def _cancel_grace_timer(trip_id: int):
    task = COMPLETION_TIMERS.pop(trip_id, None)
    if task and not task.done():
        task.cancel()
        logger.info("[LIFECYCLE] Grace timer cancelled for trip #%d (POWER_ON)", trip_id)


# ── Public event handlers (called from gps.py) ─────────────────────────────────
async def handle_power_on(db: AsyncSession, manager, now_utc: datetime):
    """Called when POWER_ON / POWER_RESTORE received from hardware."""
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)
    if trip:
        _cancel_grace_timer(trip.id)

    await manager.broadcast({
        "type":        "power",
        "event":       "POWER_RESTORE",
        "server_time": now_utc.isoformat(),
    })


async def handle_power_off(db: AsyncSession, manager, now_utc: datetime):
    """
    Called when POWER_OFF / POWER_LOST received from hardware.
    If bus is on_trip AND near destination → start 5-min grace timer.
    """
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)

    near_destination = False
    if trip and trip.status in ('on_trip', 'late'):
        # Get last known GPS position for this trip
        # Uses GpsLog
        result = await db.execute(
            select(GpsLog)
            .where(and_(GpsLog.trip_id == trip.id, GpsLog.lat.is_not(None)))
            .order_by(GpsLog.id.desc())
            .limit(1)
        )
        last_log = result.scalar_one_or_none()
        if last_log:
            dest_lat, dest_lon = await get_destination_coords(db, trip.direction)
            dist = await get_osrm_distance_m(last_log.lat, last_log.lon, dest_lat, dest_lon)
            near_destination = dist <= DESTINATION_RADIUS_M
            logger.info("[LIFECYCLE] POWER_OFF: trip #%d, %.0fm from destination", trip.id, dist)

        if near_destination:
            logger.info("[LIFECYCLE] Trip #%d — starting %ds grace timer", trip.id, GRACE_PERIOD_S)
            task = asyncio.create_task(_complete_trip_after_grace(trip.id, manager))
            COMPLETION_TIMERS[trip.id] = task

    await manager.broadcast({
        "type":             "power",
        "event":            "POWER_LOST",
        "near_destination": near_destination,
        "server_time":      now_utc.isoformat(),
    })


async def handle_gps_update(
    db: AsyncSession, manager,
    lat: float, lon: float,
    now_utc: datetime, gps_log,
):
    """
    Called on every valid GPS coordinate received.
    - Links the log to the active trip.
    - Detects movement from route origin → triggers scheduled → on_trip.
    - Runs pgRouting-based proximity alerts for opted-in users.
    - Runs automatic ETA-based late notification.
    """
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)
    if not trip:
        return

    # Link this GPS log to the active trip
    gps_log.trip_id = trip.id

    if trip.status == 'scheduled':
        # Evening trip: only allow on_trip after 17:00 IST
        if trip.direction == 'reverse' and now_ist.hour < 17:
            return

        start_lat, start_lon = await get_start_coords(db, trip.direction)
        dist_from_start = await get_osrm_distance_m(start_lat, start_lon, lat, lon)
        logger.debug("[LIFECYCLE] Trip #%d — %.0fm from start", trip.id, dist_from_start)

        if dist_from_start >= MOVEMENT_THRESHOLD_M:
            await _mark_on_trip(db, trip, manager, now_utc)

    elif trip.status in ('on_trip', 'late'):
        dest_lat, dest_lon = await get_destination_coords(db, trip.direction)
        dist_to_dest = await get_osrm_distance_m(lat, lon, dest_lat, dest_lon)
        logger.debug("[LIFECYCLE] Trip #%d — %.0fm from destination", trip.id, dist_to_dest)

        # ── Check for early trip completion ──────────────────
        completion_radius = 100 if trip.direction == 'forward' else 50
        if dist_to_dest <= completion_radius:
            trip.status = 'completed'
            await db.commit()
            logger.info("[LIFECYCLE] Trip #%d arrived within %dm of destination! Auto-completing.", trip.id, completion_radius)
            return

        # ── Smart Progression (Visited Stops Detection) ───────────────────
        try:
            # Distance calc
            
            # Fetch all stops for this route
            stops_res = await db.execute(
                select(BusStop).where(BusStop.route_id == trip.route_id)
            )
            stops = stops_res.scalars().all()
            
            visited = trip.visited_stops or []
            new_visited = []
            
            # Filter stops not yet visited
            unvisited = [s for s in stops if s.id not in visited]
            
            # Pre-filter using Haversine (< 2km) to avoid overloading OSRM, then check real driving distance
            candidates = [s for s in unvisited if haversine_km(lat, lon, float(s.lat), float(s.lon)) < 2.0]
            if candidates:
                dests = [(float(s.lat), float(s.lon)) for s in candidates]
                # OSRM calc
                distances = await get_osrm_distance_matrix_m(lat, lon, dests)
                for s, dist in zip(candidates, distances):
                    if dist <= 250: # 250 meters driving distance
                        new_visited.append(s.id)
            
            if new_visited:
                # Update the JSON array in the database
                trip.visited_stops = visited + new_visited
                await db.commit()
                logger.info("[LIFECYCLE] Trip #%d reached stops: %s", trip.id, new_visited)
        except Exception as e:
            logger.error("[LIFECYCLE] Progression check failed: %s", e)

        # ── Run ETA-based automatic late notification ───────────────────────────────────
        try:
            # ETA alerts
            await check_eta_late_notification(db, trip, lat, lon, dest_lat, dest_lon)
        except Exception as e:
            logger.debug("[LIFECYCLE] ETA check skipped: %s", e)

        # ── Run per-user proximity alerts ─────────────────────────────────────────
        try:
            # Proximity alerts
            alerts_sent = await run_proximity_alerts(db, trip, lat, lon)
            if alerts_sent:
                logger.info("[LIFECYCLE] Sent %d proximity alerts for trip #%d", alerts_sent, trip.id)
        except Exception as e:
            logger.debug("[LIFECYCLE] Proximity alerts skipped: %s", e)


async def auto_complete_expired_trips(db: AsyncSession) -> bool:
    """
    Checks all active/scheduled/late trips in the DB.
    If a trip's time window has expired (or date is in the past), marks it completed.
    Returns True if any trip was updated.
    """
    # Complete past trips
    # Force use of Indian Standard Time rather than server local time
    now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    time_mins = now_ist.hour * 60 + now_ist.minute
    today_dt = now_ist.date()
    
    result = await db.execute(
        select(Trip).where(Trip.status.in_(["scheduled", "on_trip", "active", "late"]))
    )
    trips = result.scalars().all()
    modified = False

    for t in trips:
        is_expired = False
        if t.date < today_dt:
            is_expired = True
        elif t.date == today_dt:
            is_morning = t.direction in ["forward", "morning", "Morning"]
            if is_morning and time_mins > MORNING_END_MINS:        # After 11:00 AM IST
                is_expired = True
            elif not is_morning and time_mins > EVENING_END_MINS: # After 8:30 PM IST (20:30)
                is_expired = True

        if is_expired:
            t.status = "completed"
            modified = True
            logger.info("[LIFECYCLE] Trip #%d auto-completed due to time window expiry (%s, %s)", t.id, t.date, t.direction)

    if modified:
        await db.commit()
    return modified
