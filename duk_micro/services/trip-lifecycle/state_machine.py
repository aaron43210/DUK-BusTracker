# services/trip-lifecycle/state_machine.py
"""
Trip lifecycle state machine — full port of backend/services/trip_lifecycle.py.

State transitions:
  scheduled -> on_trip   : movement detected (>= MOVEMENT_THRESHOLD_M from route start, via OSRM)
  on_trip   -> completed : bus within completion radius of destination (OSRM)
                           OR POWER_OFF near destination + GRACE_PERIOD_S grace timer
  on_trip / late -> proximity alerts published to Redis streams

POWER events are handled via Redis pub/sub (separate channel, see consumer.py).
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, update, or_, case
from sqlalchemy.orm.attributes import flag_modified
from redis.asyncio import Redis

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.." ))
from libs.duk_common.events import (
    EventBus, ProximityAlertEvent, TripStatusEvent, TripStartedEvent, EtaLateEvent
)
from libs.duk_common.osrm_client import (
    get_osrm_distance_m, get_osrm_distance_matrix_m,
    get_osrm_duration_s, haversine_m, BUS_FACTOR
)
from libs.duk_common.cache.stops_cache import get_all_stops

from libs.duk_common.models import Trip, BusStop, User, GpsLog, UserNotificationPreference
from database_local import AsyncSessionLocal

logger = logging.getLogger(__name__)

from libs.duk_common.constants import (
    IST_OFFSET, MORNING_END_MINS, EVENING_END_MINS,
    normalize_direction,
)

IST = ZoneInfo("Asia/Kolkata")

# Trip-lifecycle specific thresholds
DESTINATION_RADIUS_M = 300
GRACE_PERIOD_S       = 1200   # 20 minutes grace before auto-completing after power-off
MOVEMENT_THRESHOLD_M = 50     # Min movement from route start to detect trip has begun
STOP_ALERT_RADIUS_M  = 250    # OSRM distance to mark stop as reached

# In-process grace timers (one per trip_id).
COMPLETION_TIMERS: dict = {}



#  Time helpers 

def to_ist(utc_dt: datetime) -> datetime:
    """Convert a datetime object from UTC to IST."""
    return utc_dt + IST_OFFSET

def parse_time_to_minutes(time_str: str) -> int:
    """Parse a time string in the format "HH:MM AM/PM" to minutes since midnight."""
    if not time_str:
        return 0
    parts = time_str.split(" ")
    if len(parts) != 2:
        return 0
    time_part, period = parts
    hh_mm = time_part.split(":")
    if len(hh_mm) != 2:
        return 0
    try:
        h, m = int(hh_mm[0]), int(hh_mm[1])
        if period.upper() == "PM" and h != 12:
            h += 12
        if period.upper() == "AM" and h == 12:
            h = 0
        return h * 60 + m
    except ValueError:
        return 0



#  Stop coordinate helpers 

async def get_start_stop(db: AsyncSession, direction: str, route_id: int) -> BusStop:
    """Returns the origin BusStop for the route and direction, or raises ValueError."""
    col = BusStop.is_morning_origin if direction == "forward" else BusStop.is_evening_origin
    result = await db.execute(
        select(BusStop).where(BusStop.route_id == route_id, col.is_(True)).limit(1)
    )
    stop = result.scalar_one_or_none()
    if stop:
        return stop
        
    # Fallback to route order
    if direction == "forward":
        order = BusStop.morning_order_index.asc().nullslast()
    else:
        order = BusStop.evening_order_index.asc().nullslast()
    res = await db.execute(
        select(BusStop).where(BusStop.route_id == route_id).order_by(order).limit(1)
    )
    fb = res.scalar_one_or_none()
    if fb:
        return fb
        
    raise ValueError(f"No origin stop configured for route_id={route_id}, direction={direction}")


async def get_destination_stop(db: AsyncSession, direction: str, route_id: int) -> BusStop:
    """Returns the destination BusStop for the route and direction, or raises ValueError."""
    col = BusStop.is_morning_destination if direction == "forward" else BusStop.is_evening_destination
    result = await db.execute(
        select(BusStop).where(BusStop.route_id == route_id, col.is_(True)).limit(1)
    )
    stop = result.scalar_one_or_none()
    if stop:
        return stop
        
    # Fallback to route order
    if direction == "forward":
        order = BusStop.morning_order_index.desc().nullslast()
    else:
        order = BusStop.evening_order_index.desc().nullslast()
    res = await db.execute(
        select(BusStop).where(BusStop.route_id == route_id).order_by(order).limit(1)
    )
    fb = res.scalar_one_or_none()
    if fb:
        return fb
        
    raise ValueError(f"No destination stop configured for route_id={route_id}, direction={direction}")


#  Trip lookup 

async def get_active_trip(db: AsyncSession, now_ist: datetime, bus_id: int) -> Optional[Trip]:
    """Return today's IST-date trip that is eligible for GPS-driven transitions."""
    today = now_ist.date()
    hour  = now_ist.hour
    if 6 <= hour < 11:
        direction = "forward"
    elif hour >= 17:
        direction = "reverse"
    else:
        return None
        
    query = (
        select(Trip)
        .where(
            and_(
                Trip.date      == today,
                Trip.direction == direction,
                Trip.status.in_(["scheduled", "on_trip", "active", "late"]),
                # Trips created by the admin dashboard (ensure/create/cancel)
                # don't set bus_id, so a strict equality never matched and the
                # lifecycle silently ignored every scheduled trip. Unassigned
                # trips belong to the (single) default bus.
                or_(Trip.bus_id == bus_id, Trip.bus_id.is_(None)),
            )
        )
        # Prefer a trip that is already running over a still-scheduled one (an
        # admin "start trip" creates a second, active row for the same slot).
        .order_by(case((Trip.status == "scheduled", 1), else_=0), Trip.id.asc())
        .limit(1)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


#  Trip-started fan-out 

async def _notify_trip_started_event(bus: EventBus, trip: Trip) -> None:
    """Publish TripStartedEvent so notification-service fans out FCM + in-app."""
    ev = TripStartedEvent(trip_id=trip.id, direction=trip.direction)
    await bus.publish("notifications.trip_started", ev)
    logger.info("[LIFECYCLE] Published TripStartedEvent for trip #%d", trip.id)


#  Grace timer 

from libs.duk_common.redis_client import get_redis

async def _complete_trip_after_grace(trip_id: int, bus: EventBus, delay: int = GRACE_PERIOD_S) -> None:
    """Wait delay then mark trip completed if still active."""
    await asyncio.sleep(delay)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Trip)
            .where(Trip.id == trip_id, Trip.status.in_(["on_trip", "active", "late"]))
            .values(status="completed", ended_at=datetime.now(timezone.utc))
        )
        await db.commit()
        if result.rowcount == 1:
            logger.info("[LIFECYCLE] Trip #%d auto-completed after %ds grace", trip_id, delay)
            # Need direction for the event, so query it since update didn't return it
            trip_res = await db.execute(select(Trip.direction).where(Trip.id == trip_id))
            direction = trip_res.scalar_one_or_none()
            if direction:
                ev = TripStatusEvent(trip_id=trip_id, status="completed", direction=direction)
                await bus.publish("trip.status", ev)
    
    COMPLETION_TIMERS.pop(trip_id, None)
    try:
        redis = await get_redis()
        await redis.delete(f"grace_timer:{trip_id}")
    except Exception:
        pass


async def _cancel_grace_timer(trip_id: int) -> None:
    """Cancel the grace timer for a given trip_id."""
    task = COMPLETION_TIMERS.pop(trip_id, None)
    if task and not task.done():
        task.cancel()
        logger.info("[LIFECYCLE] Grace timer cancelled for trip #%d", trip_id)
    try:
        redis = await get_redis()
        await redis.delete(f"grace_timer:{trip_id}")
    except Exception:
        pass


#  Proximity alerts (3-type system, mirrors proximity_alerts.py) 

async def _get_all_alert_tokens_bulk(db: AsyncSession, route_id: int, direction: str) -> tuple[dict, dict]:
    """Bulk load all boarding and destination tokens grouped by stop_id."""
    boarding_map, dest_map = {}, {}
    try:
        prefs_res = await db.execute(
            select(UserNotificationPreference, User.device_token).join(
                User, User.id == UserNotificationPreference.user_id
            ).where(
                UserNotificationPreference.route_id == route_id,
                UserNotificationPreference.direction == direction,
                UserNotificationPreference.is_active == True,
                User.device_token.isnot(None)
            )
        )
        for p, device_token in prefs_res.all():
            if device_token:
                if p.boarding_stop_id:
                    boarding_map.setdefault(p.boarding_stop_id, set()).add(device_token)
                if p.destination_stop_id:
                    dest_map.setdefault(p.destination_stop_id, set()).add(device_token)

        users_res = await db.execute(
            select(User).where(
                User.verified == True,
                User.notifications_on != False,
                User.device_token.isnot(None),
            )
        )
        for u in users_res.scalars().all():
            if u.device_token:
                if u.boarding_alert_stop_id:
                    boarding_map.setdefault(u.boarding_alert_stop_id, set()).add(u.device_token)
                if u.destination_alert_stop_id:
                    dest_map.setdefault(u.destination_alert_stop_id, set()).add(u.device_token)
    except Exception as e:
        logger.error("[LIFECYCLE] Failed to bulk fetch proximity tokens: %s", e)
    
    return boarding_map, dest_map


async def run_proximity_alerts(
    db:     AsyncSession,
    bus:    EventBus,
    redis:  Redis,
    trip:   Trip,
    lat:    float,
    lon:    float,
    stops:  List[dict],
) -> None:
    """
    Check each unnotified stop and emit ProximityAlertEvent when bus is within 0.25 km.
    Uses Haversine pre-filter then OSRM for final check.
    Uses a single bulk database query (V8, V21) to fetch tokens to avoid N+1 queries.
    """
    boarding_map, dest_map = await _get_all_alert_tokens_bulk(db, trip.route_id, trip.direction)

    candidate_stops = []
    for stop in stops:
        if stop["route_id"] != trip.route_id:
            continue
        if haversine_m(lat, lon, stop["lat"], stop["lon"]) > 400:
            continue  # pre-filter

        stop_id   = stop["id"]
        state_key = f"trip:{trip.id}:stop:{stop_id}:notified"
        if await redis.get(state_key):
            continue
        
        candidate_stops.append(stop)
        
    if not candidate_stops:
        return

    dests = [(s["lat"], s["lon"]) for s in candidate_stops]
    distances = await get_osrm_distance_matrix_m(lat, lon, dests)

    for stop, dist in zip(candidate_stops, distances):
        if dist > STOP_ALERT_RADIUS_M:
            continue

        stop_id   = stop["id"]
        stop_name = stop.get("name", "your stop")
        state_key = f"trip:{trip.id}:stop:{stop_id}:notified"

        boarding_tokens = list(boarding_map.get(stop_id, []))
        dest_tokens     = list(dest_map.get(stop_id, []))

        if boarding_tokens:
            await bus.publish(
                "notifications.proximity",
                ProximityAlertEvent(
                    trip_id=trip.id, stop_id=stop_id, stop_name=stop_name,
                    alert_type="boarding", user_tokens=boarding_tokens,
                ),
            )
        if dest_tokens:
            await bus.publish(
                "notifications.proximity",
                ProximityAlertEvent(
                    trip_id=trip.id, stop_id=stop_id, stop_name=stop_name,
                    alert_type="destination", user_tokens=dest_tokens,
                ),
            )

        if boarding_tokens or dest_tokens:
            await redis.set(state_key, "1", ex=3600)
            logger.info("[LIFECYCLE] Proximity alert fired for stop %s (trip #%d)", stop_name, trip.id)


#  ETA late notification 

async def check_eta_late_notification(
    db:       AsyncSession,
    bus:      EventBus,
    redis:    Redis,
    trip:     Trip,
    lat:      float,
    lon:      float,
    dest_lat: float,
    dest_lon: float,
) -> None:
    """Fire tiered notifications when bus is > 10 min late vs scheduled end."""
    duration_s   = await get_osrm_duration_s(lat, lon, dest_lat, dest_lon)
    eta_min      = (duration_s / 60.0) * BUS_FACTOR
    now_ist      = datetime.now(timezone.utc).astimezone(IST)
    time_mins    = now_ist.hour * 60 + now_ist.minute
    sched_end    = MORNING_END_MINS if trip.direction == "forward" else EVENING_END_MINS
    expected_end = sched_end - 30  # expected arrival is 30 min before window close
    eta_arrival  = time_mins + eta_min
    delay_min    = eta_arrival - expected_end

    if delay_min < 10:
        return

    current_tier = int(delay_min // 10) * 10
    
    tier_key = f"trip_{trip.id}_delay_tier"
    last_tier_str = await redis.get(tier_key)
    last_tier = int(last_tier_str) if last_tier_str else 0

    if current_tier <= last_tier:
        return

    arrival_dt  = now_ist + timedelta(minutes=eta_min)
    arrival_str = arrival_dt.strftime("%-I:%M %p")

    # Send to all eligible users who have delay notifications enabled.
    #
    # Previously this joined UserNotificationPreference, but no shipped client
    # populates that table (the PWA writes preferences onto the User row via
    # /api/v1/auth/preferences), so the query always returned zero tokens and the
    # "Bus Delayed" push effectively never fired. get_notification_tokens reads
    # the User table and honours the fine-grained notify_delays flag.
    from libs.duk_common.notification_helpers import get_notification_tokens
    tokens = await get_notification_tokens(db, pref_column=User.notify_delays)

    if tokens:
        await bus.publish(
            "notifications.late_eta",
            EtaLateEvent(
                trip_id=trip.id,
                delay_min=round(delay_min, 1),
                eta_min=round(eta_min, 1),
                arrival_str=arrival_str,
                direction=trip.direction,
                user_tokens=tokens,
            ),
        )

    # Set new tier in redis with 24h expiration
    await redis.set(tier_key, str(current_tier), ex=86400)
    logger.info("[LIFECYCLE] ETA late notification fired for trip #%d (%.0f min late, tier %d)", trip.id, delay_min, current_tier)


#  Visited stops + auto-catch-up 

async def update_visited_stops(db: AsyncSession, bus: EventBus, trip: Trip, lat: float, lon: float, stops: List[dict]) -> None:
    """OSRM distance matrix to unvisited stops; mark within STOP_ALERT_RADIUS_M visited."""

    # Lock the trip row for the duration of this transaction so concurrent GPS
    # workers can't lose each other's visited_stops appends. visited_stops is a
    # JSON array updated read-modify-write; without the row lock two workers could
    # each read the old list and the second commit would clobber the first.
    locked = await db.execute(
        select(Trip.visited_stops).where(Trip.id == trip.id).with_for_update()
    )
    visited     = list(locked.scalar_one_or_none() or [])
    visited_set = set(visited)
    route_stops = [s for s in stops if s["route_id"] == trip.route_id]
    unvisited   = [s for s in route_stops if s["id"] not in visited_set]

    # Pre-filter with haversine
    candidates = [s for s in unvisited if haversine_m(lat, lon, s["lat"], s["lon"]) < 2000]
    if not candidates:
        return

    dests     = [(s["lat"], s["lon"]) for s in candidates]
    distances = await get_osrm_distance_matrix_m(lat, lon, dests)

    is_fwd = (trip.direction == "forward")
    reached = [s for s, d in zip(candidates, distances) if d <= STOP_ALERT_RADIUS_M]
    if not reached:
        return

    new_ids = [s["id"] for s in reached]

    # Auto catch-up: find the furthest reached stop and mark all preceding stops visited.
    # Explicit None check, not `a or b` — a real order index of 0 is falsy and `or`
    # would drop the origin stop, scrambling the catch-up order (audit D3).
    def key_fn(x):
        primary = x.get("morning_order_index") if is_fwd else x.get("evening_order_index")
        return primary if primary is not None else x.get("order_index", 0)
    furthest = max(reached, key=key_fn)
    for s in route_stops:
        if s["id"] in visited_set or s["id"] in new_ids:
            continue
        if is_fwd and key_fn(s) < key_fn(furthest):
            new_ids.append(s["id"])
        elif not is_fwd and key_fn(s) > key_fn(furthest):
            new_ids.append(s["id"])

    trip.visited_stops = visited + new_ids
    flag_modified(trip, "visited_stops")
    
    # Dynamic Delay Recalculation
    now_ist = to_ist(datetime.now(timezone.utc))
    time_mins = now_ist.hour * 60 + now_ist.minute
    sched_str = furthest.get("morning_time") if is_fwd else furthest.get("evening_time")
    sched_mins = parse_time_to_minutes(sched_str)
    
    if sched_mins > 0:
        delay = time_mins - sched_mins
        trip.late_by_minutes = delay if delay > -5 else 0
        
    await db.commit()
    logger.info("[LIFECYCLE] Trip #%d — marked %d stops visited, updated delay: %s", trip.id, len(new_ids), trip.late_by_minutes)

    # Instantly broadcast the delay update
    ev = TripStatusEvent(trip_id=trip.id, status=trip.status, direction=trip.direction, late_by_minutes=trip.late_by_minutes)
    await bus.publish("trip.status", ev)


#  Auto-complete expired trips 

async def auto_complete_expired_trips(db: AsyncSession) -> bool:
    """
    Mark trips as completed if their IST time window has already passed.
    Must use IST date (not UTC date.today()) to avoid midnight boundary bugs.
    """
    now_ist   = datetime.now(timezone.utc).astimezone(IST)
    time_mins = now_ist.hour * 60 + now_ist.minute
    today_ist = now_ist.date()

    result = await db.execute(
        select(Trip).where(
            Trip.status.in_(["scheduled", "on_trip", "active", "late"])
        )
    )
    trips    = result.scalars().all()
    modified = False

    for t in trips:
        expired = False
        if t.date and t.date < today_ist:
            expired = True
        elif t.date and t.date == today_ist:
            is_morning = normalize_direction(t.direction) == "forward"
            if is_morning and time_mins > MORNING_END_MINS:
                expired = True
            elif not is_morning and time_mins > EVENING_END_MINS:
                expired = True

        if expired:
            t.status   = "completed"
            modified   = True
            await _cancel_grace_timer(t.id)
            logger.info("[LIFECYCLE] Trip #%d expired (%s %s) -> completed", t.id, t.date, t.direction)

    if modified:
        await db.commit()
    return modified


#  POWER event handlers 

async def handle_power_on(db: AsyncSession, bus: EventBus, now_utc: datetime, bus_id: int) -> None:
    """Handle POWER_RESTORE events by cancelling any active grace timers."""
    now_ist = now_utc.astimezone(IST)
    trip    = await get_active_trip(db, now_ist, bus_id)
    if trip:
        await _cancel_grace_timer(trip.id)
    logger.info("[LIFECYCLE] POWER_RESTORE received for bus %d", bus_id)


async def handle_power_off(db: AsyncSession, bus: EventBus, now_utc: datetime, bus_id: int) -> None:
    """Handle POWER_LOST events by starting a grace timer if the bus is near its destination."""
    now_ist = now_utc.astimezone(IST)
    trip    = await get_active_trip(db, now_ist, bus_id)

    near_destination = False
    if trip and trip.status in ("on_trip", "active", "late"):
        last_res = await db.execute(
            select(GpsLog).where(GpsLog.trip_id == trip.id, GpsLog.lat.isnot(None)).order_by(desc(GpsLog.server_time)).limit(1)
        )
        last_log = last_res.scalar_one_or_none()
        if last_log:
            try:
                dest_stop = await get_destination_stop(db, trip.direction, trip.route_id)
                dist = await get_osrm_distance_m(last_log.lat, last_log.lon, float(dest_stop.lat), float(dest_stop.lon))
                near_destination = dist <= DESTINATION_RADIUS_M
                logger.info("[LIFECYCLE] POWER_OFF: trip #%d %.0fm from destination", trip.id, dist)
            except ValueError as e:
                logger.warning("[LIFECYCLE] Could not get destination coords for POWER_OFF check: %s", e)

        if near_destination and trip.id not in COMPLETION_TIMERS:
            # The grace_timer:{trip_id} Redis key (TTL = GRACE_PERIOD_S) is the
            # durable, cross-process source of truth: recover_grace_timers rebuilds
            # the in-process task from it on startup. Arm it with SET NX so that if
            # any instance already started this trip's grace period we don't stack a
            # second timer; COMPLETION_TIMERS still dedupes within this process.
            armed = False
            try:
                redis_cli = await get_redis()
                armed = bool(await redis_cli.set(f"grace_timer:{trip.id}", "1", ex=GRACE_PERIOD_S, nx=True))
            except Exception:
                # If Redis is unreachable, fall back to a local-only timer so the
                # single-worker path keeps behaving exactly as before.
                armed = True
            if armed:
                task = asyncio.create_task(_complete_trip_after_grace(trip.id, bus, GRACE_PERIOD_S))
                COMPLETION_TIMERS[trip.id] = task
                logger.info("[LIFECYCLE] Trip #%d — grace timer started (%ds)", trip.id, GRACE_PERIOD_S)

    logger.info("[LIFECYCLE] POWER_LOST near_destination=%s", near_destination)


#  Main GPS event processor 

async def process_gps_event(event_data: dict, db: AsyncSession, redis: Redis, bus_id: int) -> None:
    """
    Called for every GpsSnappedEvent consumed from the 'gps.snapped' Redis stream.
    Drives all state transitions and emits events to downstream services.
    """
    lat       = float(event_data["lat"])
    lon       = float(event_data["lon"])
    speed_kmh = event_data.get("speed_kmh")
    try:
        server_time = datetime.fromisoformat(event_data["server_time"])
        if server_time.tzinfo is None:
            server_time = server_time.replace(tzinfo=timezone.utc)
    except Exception:
        server_time = datetime.now(timezone.utc)

    bus     = EventBus(redis)
    now_ist = server_time.astimezone(IST)
    trip    = await get_active_trip(db, now_ist, bus_id)
    if not trip:
        return

    #  scheduled -> on_trip 
    if trip.status == "scheduled":
        if trip.direction == "reverse" and now_ist.hour < 17:
            return
        try:
            start_stop = await get_start_stop(db, trip.direction, trip.route_id)
            dist_from_start = await get_osrm_distance_m(float(start_stop.lat), float(start_stop.lon), lat, lon)
        except ValueError as e:
            logger.error("[LIFECYCLE] Cannot start trip: %s", e)
            return

        logger.debug("[LIFECYCLE] Trip #%d %.0fm from start", trip.id, dist_from_start)
        if dist_from_start >= MOVEMENT_THRESHOLD_M:
            started_at      = server_time
            time_mins       = now_ist.hour * 60 + now_ist.minute

            # Dynamic departure schedule
            sched_time_str  = start_stop.morning_time if normalize_direction(trip.direction) == "forward" else start_stop.evening_time
            depart_mins     = parse_time_to_minutes(sched_time_str)
            if depart_mins == 0:
                depart_mins = 450 if normalize_direction(trip.direction) == "forward" else 1060

            delay           = time_mins - depart_mins
            late_by_minutes = delay if delay > 2 else 0

            # Conditional transition guarded by current status (mirrors the
            # _complete_trip_after_grace pattern): only ONE worker can flip
            # scheduled -> on_trip, so concurrent GPS updates can't each emit a
            # duplicate TripStarted event or clobber started_at.
            result = await db.execute(
                update(Trip)
                .where(Trip.id == trip.id, Trip.status == "scheduled")
                .values(status="on_trip", started_at=started_at, late_by_minutes=late_by_minutes)
            )
            await db.commit()
            if result.rowcount != 1:
                # Another worker already started this trip; nothing more to do.
                return
            trip.status          = "on_trip"
            trip.started_at      = started_at
            trip.late_by_minutes = late_by_minutes
            logger.info("[LIFECYCLE] Trip #%d -> on_trip (%.0fm from start)", trip.id, dist_from_start)
            await _notify_trip_started_event(bus, trip)
            ev = TripStatusEvent(trip_id=trip.id, status="on_trip", direction=trip.direction)
            await bus.publish("trip.status", ev)

    #  on_trip / late -> completed + proximity alerts 
    # "active" = started manually from the admin dashboard (start_trip); treat it
    # like on_trip so it still completes and drives stop/ETA/proximity updates.
    elif trip.status in ("on_trip", "active", "late"):
        try:
            dest_stop = await get_destination_stop(db, trip.direction, trip.route_id)
            dest_lat, dest_lon = float(dest_stop.lat), float(dest_stop.lon)
        except ValueError as e:
            logger.error("[LIFECYCLE] Cannot process on_trip events: %s", e)
            return

        dist_to_dest, stops = await asyncio.gather(
            get_osrm_distance_m(lat, lon, dest_lat, dest_lon),
            get_all_stops(db, redis, BusStop),
        )

        logger.debug("[LIFECYCLE] Trip #%d %.0fm from destination", trip.id, dist_to_dest)

        # Completion check
        completion_radius = 100 if trip.direction == "forward" else 50
        if dist_to_dest <= completion_radius:
            # Guarded transition so concurrent workers can't both emit "completed".
            result = await db.execute(
                update(Trip)
                .where(Trip.id == trip.id, Trip.status.in_(["on_trip", "active", "late"]))
                .values(status="completed", ended_at=server_time)
            )
            await db.commit()
            await _cancel_grace_timer(trip.id)
            if result.rowcount != 1:
                return
            trip.status   = "completed"
            trip.ended_at = server_time
            logger.info("[LIFECYCLE] Trip #%d completed", trip.id)
            ev = TripStatusEvent(trip_id=trip.id, status="completed", direction=trip.direction)
            await bus.publish("trip.status", ev)
            return

        # Visited stops + ETA late + proximity alerts executed sequentially for AsyncSession thread-safety
        try:
            await update_visited_stops(db, bus, trip, lat, lon, stops)
        except Exception as exc:
            logger.warning("[LIFECYCLE] update_visited_stops failed: %s", exc)

        try:
            await check_eta_late_notification(db, bus, redis, trip, lat, lon, dest_lat, dest_lon)
        except Exception as exc:
            logger.warning("[LIFECYCLE] check_eta_late_notification failed: %s", exc)

        try:
            await run_proximity_alerts(db, bus, redis, trip, lat, lon, stops)
        except Exception as exc:
            logger.warning("[LIFECYCLE] run_proximity_alerts failed: %s", exc)
