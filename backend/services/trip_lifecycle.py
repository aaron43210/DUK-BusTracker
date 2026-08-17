"""
services/trip_lifecycle.py — GPS-driven automatic trip state machine.

State transitions:
  scheduled → on_trip   : movement detected (> 50 m from route start)
  on_trip   → completed : bus within completion radius of destination
                          OR POWER_OFF near destination + grace period

Time windows (IST):
  Morning (forward): eligible from 06:00 IST
  Evening (reverse): eligible from 17:00 IST
"""
import asyncio
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm.attributes import flag_modified

from services.osrm_client import get_osrm_distance_m, get_osrm_distance_matrix_m, reset_snap_state
from services.geofence import haversine_km
from services.proximity_alerts import check_eta_late_notification, run_proximity_alerts
from constants import IST_OFFSET, MORNING_END_MINS, EVENING_END_MINS
from models.trip import Trip
from models.gps import GpsLog
from models.route import BusStop

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
DESTINATION_RADIUS_M  = 300
GRACE_PERIOD_S        = 1200   # 20 minutes
MOVEMENT_THRESHOLD_M  = 50

COMPLETION_TIMERS: dict[int, asyncio.Task] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def to_ist(utc_dt: datetime) -> datetime:
    return utc_dt + IST_OFFSET


async def get_start_coords(db: AsyncSession, direction: str) -> tuple[float, float]:
    col = BusStop.is_morning_origin if direction == "forward" else BusStop.is_evening_origin
    result = await db.execute(select(BusStop).where(col.is_(True)).limit(1))
    stop = result.scalar_one_or_none()
    if stop:
        return float(stop.lat), float(stop.lon)

    order = BusStop.order_index.asc() if direction == "forward" else BusStop.order_index.desc()
    res   = await db.execute(select(BusStop).order_by(order).limit(1))
    fallback = res.scalar_one_or_none()
    if fallback:
        return float(fallback.lat), float(fallback.lon)

    return (8.5350, 76.9908) if direction == "forward" else (8.6158, 76.8527)


async def get_destination_coords(db: AsyncSession, direction: str) -> tuple[float, float]:
    col = (
        BusStop.is_morning_destination
        if direction == "forward"
        else BusStop.is_evening_destination
    )
    result = await db.execute(select(BusStop).where(col.is_(True)).limit(1))
    stop = result.scalar_one_or_none()
    if stop:
        return float(stop.lat), float(stop.lon)

    order = BusStop.order_index.desc() if direction == "forward" else BusStop.order_index.asc()
    res   = await db.execute(select(BusStop).order_by(order).limit(1))
    fallback = res.scalar_one_or_none()
    if fallback:
        return float(fallback.lat), float(fallback.lon)

    return (8.6158, 76.8527) if direction == "forward" else (8.5350, 76.9908)


# ── Trip lookup ────────────────────────────────────────────────────────────────

async def get_active_trip(db: AsyncSession, now_ist: datetime) -> Trip | None:
    """
    Return today's trip eligible for GPS-driven state transitions.
    Uses IST date (not server-local date) to avoid midnight boundary bugs.
    """
    # FIX: use now_ist.date() not date.today() — server may be UTC,
    # causing wrong date from 00:00–05:30 IST (still "yesterday" in UTC).
    today = now_ist.date()
    hour  = now_ist.hour

    if 6 <= hour < 11:
        direction = "forward"
    elif hour >= 17:
        direction = "reverse"
    else:
        return None

    result = await db.execute(
        select(Trip).where(
            and_(
                Trip.date      == today,
                Trip.direction == direction,
                Trip.status.in_(["scheduled", "on_trip", "late"]),
            )
        ).limit(1)
    )
    return result.scalar_one_or_none()


# ── State transitions ──────────────────────────────────────────────────────────

async def _notify_trip_started(trip_id: int, direction: str) -> None:
    from database import AsyncSessionLocal
    from models.user import User
    from models.notification import InAppNotification
    from services.firebase import send_push_notification

    trip_name = "Morning" if direction in ("forward", "morning", "Morning") else "Evening"
    title = f"{trip_name} Trip Started"
    body = f"The {trip_name.lower()} trip bus has departed from the starting point."

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.verified.is_(True)))
            users = result.scalars().all()

            in_app_notifs = []
            tokens = []
            for u in users:
                in_app_notifs.append(
                    InAppNotification(
                        user_id=u.id,
                        title=title,
                        body=body,
                        type="trip_start"
                    )
                )
                if u.notifications_on and u.device_token:
                    tokens.append(u.device_token)

            if in_app_notifs:
                db.add_all(in_app_notifs)
                await db.commit()

            if tokens:
                await send_push_notification(
                    device_tokens=tokens,
                    title=title,
                    body=body,
                    urgent=True
                )
                logger.info("[LIFECYCLE] Sent trip start push notification to %d devices", len(tokens))
    except Exception as e:
        logger.error("[LIFECYCLE] Failed to send trip start notifications: %s", e)


async def _mark_on_trip(
    db: AsyncSession, trip: Trip, manager, now_utc: datetime
) -> None:
    trip.status     = "on_trip"
    trip.started_at = now_utc
    reset_snap_state()
    await db.commit()
    logger.info("[LIFECYCLE] Trip #%d → on_trip", trip.id)
    
    asyncio.create_task(_notify_trip_started(trip.id, trip.direction))
    
    await manager.broadcast({
        "type":      "trip_status",
        "trip_id":   trip.id,
        "status":    "on_trip",
        "direction": trip.direction,
    })


async def _complete_trip_after_grace(trip_id: int, manager) -> None:
    await asyncio.sleep(GRACE_PERIOD_S)
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip   = result.scalar_one_or_none()
        if trip and trip.status in ("on_trip", "late"):
            trip.status   = "completed"
            trip.ended_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(
                "[LIFECYCLE] Trip #%d auto-completed after %ds grace",
                trip_id, GRACE_PERIOD_S,
            )
            await manager.broadcast({
                "type":    "trip_status",
                "trip_id": trip_id,
                "status":  "completed",
            })

    COMPLETION_TIMERS.pop(trip_id, None)


def _cancel_grace_timer(trip_id: int) -> None:
    task = COMPLETION_TIMERS.pop(trip_id, None)
    if task and not task.done():
        task.cancel()
        logger.info("[LIFECYCLE] Grace timer cancelled for trip #%d", trip_id)


# ── Public event handlers ──────────────────────────────────────────────────────

async def handle_power_on(db: AsyncSession, manager, now_utc: datetime) -> None:
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)
    if trip:
        _cancel_grace_timer(trip.id)
    reset_snap_state()
    await manager.broadcast({
        "type":        "power",
        "event":       "POWER_RESTORE",
        "server_time": now_utc.isoformat(),
    })


async def handle_power_off(db: AsyncSession, manager, now_utc: datetime) -> None:
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)

    near_destination = False
    if trip and trip.status in ("on_trip", "late"):
        last_res = await db.execute(
            select(GpsLog)
            .where(GpsLog.lat.is_not(None))
            .order_by(GpsLog.id.desc())
            .limit(1)
        )
        last_log = last_res.scalar_one_or_none()
        if last_log:
            dest_lat, dest_lon = await get_destination_coords(db, trip.direction)
            dist = await get_osrm_distance_m(
                last_log.lat, last_log.lon, dest_lat, dest_lon
            )
            near_destination = dist <= DESTINATION_RADIUS_M
            logger.info(
                "[LIFECYCLE] POWER_OFF: trip #%d %.0fm from destination",
                trip.id, dist,
            )

        if near_destination:
            task = asyncio.create_task(_complete_trip_after_grace(trip.id, manager))
            COMPLETION_TIMERS[trip.id] = task
            logger.info(
                "[LIFECYCLE] Trip #%d — grace timer started (%ds)",
                trip.id, GRACE_PERIOD_S,
            )

    await manager.broadcast({
        "type":             "power",
        "event":            "POWER_LOST",
        "near_destination": near_destination,
        "server_time":      now_utc.isoformat(),
    })


async def handle_gps_update(
    db: AsyncSession,
    manager,
    lat: float,
    lon: float,
    now_utc: datetime,
    gps_log,
) -> None:
    """
    Called on every valid GPS coordinate.

    FIX: Uses flag_modified() after mutating trip.visited_stops to guarantee
    SQLAlchemy detects the JSON column change even when the ORM identity map
    holds a reference to the same list object.
    """
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)
    if not trip:
        return

    if hasattr(gps_log, "trip_id"):
        gps_log.trip_id = trip.id

    # ── scheduled → on_trip ───────────────────────────────────────────────────
    if trip.status == "scheduled":
        if trip.direction == "reverse" and now_ist.hour < 17:
            return
        start_lat, start_lon = await get_start_coords(db, trip.direction)
        dist_from_start = await get_osrm_distance_m(start_lat, start_lon, lat, lon)
        logger.debug("[LIFECYCLE] Trip #%d %.0fm from start", trip.id, dist_from_start)
        if dist_from_start >= MOVEMENT_THRESHOLD_M:
            await _mark_on_trip(db, trip, manager, now_utc)

    # ── on_trip / late → proximity checks ────────────────────────────────────
    elif trip.status in ("on_trip", "late"):
        dest_lat, dest_lon = await get_destination_coords(db, trip.direction)

        # Fetch destination distance and stops in parallel
        dist_to_dest_task = asyncio.create_task(
            get_osrm_distance_m(lat, lon, dest_lat, dest_lon)
        )
        stops_res_task = asyncio.create_task(
            db.execute(select(BusStop).where(BusStop.route_id == trip.route_id))
        )
        dist_to_dest, stops_res = await asyncio.gather(dist_to_dest_task, stops_res_task)

        logger.debug("[LIFECYCLE] Trip #%d %.0fm from destination", trip.id, dist_to_dest)

        # Early completion
        completion_radius = 100 if trip.direction == "forward" else 50
        if dist_to_dest <= completion_radius:
            trip.status   = "completed"
            trip.ended_at = now_utc
            await db.commit()
            logger.info("[LIFECYCLE] Trip #%d completed", trip.id)
            return

        # Visited stops detection
        try:
            stops   = stops_res.scalars().all()
            visited = list(trip.visited_stops or [])
            visited_set = set(visited)

            # Pre-filter with haversine < 2km before OSRM call
            unvisited  = [s for s in stops if s.id not in visited_set]
            candidates = [
                s for s in unvisited
                if haversine_km(lat, lon, float(s.lat), float(s.lon)) < 2.0
            ]
            if candidates:
                dests     = [(float(s.lat), float(s.lon)) for s in candidates]
                distances = await get_osrm_distance_matrix_m(lat, lon, dests)
                
                # The stops we physically reached in this tick
                reached_stops = [s for s, d in zip(candidates, distances) if d <= 250]
                
                if reached_stops:
                    new_ids = [s.id for s in reached_stops]
                    
                    # --- AUTO CATCH-UP LOGIC ---
                    is_fwd = (trip.direction == "forward")
                    # Find the "furthest" reached stop in this tick based on order
                    furthest_reached = max(
                        reached_stops, 
                        key=lambda x: x.order_index if is_fwd else -x.order_index
                    )
                    
                    # Find all preceding stops that were skipped
                    skipped_ids = []
                    for s in stops:
                        if s.id in visited_set or s.id in new_ids:
                            continue
                        
                        # Forward: missed if order < furthest
                        if is_fwd and s.order_index < furthest_reached.order_index:
                            skipped_ids.append(s.id)
                        # Reverse: missed if order > furthest
                        elif not is_fwd and s.order_index > furthest_reached.order_index:
                            skipped_ids.append(s.id)
                            
                    if skipped_ids:
                        new_ids = skipped_ids + new_ids
                        logger.info("[LIFECYCLE] Trip #%d auto-caught up %d skipped stops", trip.id, len(skipped_ids))
                    
                    trip.visited_stops = visited + new_ids
                    flag_modified(trip, "visited_stops")
                    await db.commit()
                    logger.info("[LIFECYCLE] Trip #%d reached stops: %s", trip.id, new_ids)
        except Exception as exc:
            logger.warning("[LIFECYCLE] Progression check failed: %s", exc)

        # ETA + proximity alerts run concurrently
        try:
            await asyncio.gather(
                check_eta_late_notification(db, trip, lat, lon, dest_lat, dest_lon),
                run_proximity_alerts(db, trip, lat, lon),
                return_exceptions=True,
            )
        except Exception as exc:
            logger.warning("[LIFECYCLE] Alert checks failed: %s", exc)


async def auto_complete_expired_trips(db: AsyncSession) -> bool:
    """
    Mark trips as completed if their time window has expired.

    FIX: Uses IST date throughout instead of mixing date.today() (UTC/local)
    with IST time-of-day arithmetic, which caused wrong-date bugs between
    00:00 and 05:30 IST when the server clock is UTC.
    """
    now_ist   = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    time_mins = now_ist.hour * 60 + now_ist.minute
    today_ist = now_ist.date()   # FIX: was date.today() which is UTC-based

    result = await db.execute(
        select(Trip).where(
            Trip.status.in_(["scheduled", "on_trip", "active", "late"])
        )
    )
    trips    = result.scalars().all()
    modified = False

    for t in trips:
        is_expired = False
        if t.date < today_ist:
            is_expired = True
        elif t.date == today_ist:
            is_morning = t.direction in ("forward", "morning", "Morning")
            if is_morning and time_mins > MORNING_END_MINS:
                is_expired = True
            elif not is_morning and time_mins > EVENING_END_MINS:
                is_expired = True

        if is_expired:
            t.status  = "completed"
            modified  = True
            logger.info(
                "[LIFECYCLE] Trip #%d expired (%s %s) → completed",
                t.id, t.date, t.direction,
            )

    if modified:
        await db.commit()
    return modified
