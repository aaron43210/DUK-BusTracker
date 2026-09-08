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
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
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

from models_local import Trip, BusStop, User, GpsLog, UserNotificationPreference
from database_local import AsyncSessionLocal

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
IST_OFFSET = timedelta(hours=5, minutes=30)

#  Constants (mirrors monolith constants.py) 
DESTINATION_RADIUS_M = 300
GRACE_PERIOD_S       = 1200     # 20 minutes
MOVEMENT_THRESHOLD_M = 50
STOP_ALERT_RADIUS_M  = 250      # OSRM distance to mark stop as reached
MORNING_END_MINS     = 660      # 11:00 AM IST in minutes
EVENING_END_MINS     = 1230     # 20:30 IST in minutes

# In-process grace timers (one per trip_id).
COMPLETION_TIMERS: dict = {}


#  Time helpers 

def to_ist(utc_dt: datetime) -> datetime:
    return utc_dt + IST_OFFSET

def parse_time_to_minutes(time_str: str) -> int:
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

async def get_start_coords(db: AsyncSession, direction: str) -> Tuple[float, float]:
    col = BusStop.is_morning_origin if direction == "forward" else BusStop.is_evening_origin
    result = await db.execute(select(BusStop).where(col.is_(True)).limit(1))
    stop = result.scalar_one_or_none()
    if stop:
        return float(stop.lat), float(stop.lon)
    order = BusStop.order_index.asc() if direction == "forward" else BusStop.order_index.desc()
    res = await db.execute(select(BusStop).order_by(order).limit(1))
    fb = res.scalar_one_or_none()
    if fb:
        return float(fb.lat), float(fb.lon)
    return (8.5350, 76.9908) if direction == "forward" else (8.6158, 76.8527)


async def get_destination_coords(db: AsyncSession, direction: str) -> Tuple[float, float]:
    col = BusStop.is_morning_destination if direction == "forward" else BusStop.is_evening_destination
    result = await db.execute(select(BusStop).where(col.is_(True)).limit(1))
    stop = result.scalar_one_or_none()
    if stop:
        return float(stop.lat), float(stop.lon)
    order = BusStop.order_index.desc() if direction == "forward" else BusStop.order_index.asc()
    res = await db.execute(select(BusStop).order_by(order).limit(1))
    fb = res.scalar_one_or_none()
    if fb:
        return float(fb.lat), float(fb.lon)
    return (8.6158, 76.8527) if direction == "forward" else (8.5350, 76.9908)


#  Trip lookup 

async def get_active_trip(db: AsyncSession, now_ist: datetime) -> Optional[Trip]:
    """Return today's IST-date trip that is eligible for GPS-driven transitions."""
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


#  Trip-started fan-out 

async def _notify_trip_started_event(bus: EventBus, trip: Trip) -> None:
    """Publish TripStartedEvent so notification-service fans out FCM + in-app."""
    ev = TripStartedEvent(trip_id=trip.id, direction=trip.direction)
    await bus.publish("notifications.trip_started", ev)
    logger.info("[LIFECYCLE] Published TripStartedEvent for trip #%d", trip.id)


#  Grace timer 

async def _complete_trip_after_grace(trip_id: int, bus: EventBus) -> None:
    """Wait GRACE_PERIOD_S then mark trip completed if still active."""
    await asyncio.sleep(GRACE_PERIOD_S)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip   = result.scalar_one_or_none()
        if trip and trip.status in ("on_trip", "late"):
            trip.status   = "completed"
            trip.ended_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("[LIFECYCLE] Trip #%d auto-completed after %ds grace", trip_id, GRACE_PERIOD_S)
            ev = TripStatusEvent(trip_id=trip_id, status="completed", direction=trip.direction)
            await bus.publish("trip.status", ev)
    COMPLETION_TIMERS.pop(trip_id, None)


def _cancel_grace_timer(trip_id: int) -> None:
    task = COMPLETION_TIMERS.pop(trip_id, None)
    if task and not task.done():
        task.cancel()
        logger.info("[LIFECYCLE] Grace timer cancelled for trip #%d", trip_id)


#  Proximity alerts (3-type system, mirrors proximity_alerts.py) 

async def _get_boarding_tokens(db: AsyncSession, route_id: int, stop_id: int, direction: str) -> List[str]:
    """Tokens from user_notification_preferences and User table (boarding alert)."""
    tokens = set()
    try:
        res1 = await db.execute(
            select(UserNotificationPreference.fcm_token).where(
                UserNotificationPreference.route_id == route_id,
                UserNotificationPreference.direction == direction,
                UserNotificationPreference.boarding_stop_id == stop_id,
                UserNotificationPreference.is_active == True,
            )
        )
        for r in res1.all():
            if r[0]:
                tokens.add(r[0])
    except Exception:
        pass

    try:
        res2 = await db.execute(
            select(User.device_token).where(
                User.verified == True,
                User.notifications_on != False,
                User.proximity_alert_enabled == True,
                User.boarding_alert_stop_id == stop_id,
                User.device_token.isnot(None),
            )
        )
        for r in res2.all():
            if r[0]:
                tokens.add(r[0])
    except Exception:
        pass

    return list(tokens)


async def _get_destination_tokens(db: AsyncSession, route_id: int, stop_id: int, direction: str) -> List[str]:
    """Tokens for users whose destination is this stop."""
    tokens = set()
    try:
        res1 = await db.execute(
            select(UserNotificationPreference.fcm_token).where(
                UserNotificationPreference.route_id == route_id,
                UserNotificationPreference.direction == direction,
                UserNotificationPreference.destination_stop_id == stop_id,
                UserNotificationPreference.is_active == True,
            )
        )
        for r in res1.all():
            if r[0]:
                tokens.add(r[0])
    except Exception:
        pass

    try:
        res2 = await db.execute(
            select(User.device_token).where(
                User.verified == True,
                User.notifications_on != False,
                User.proximity_alert_enabled == True,
                User.destination_alert_stop_id == stop_id,
                User.device_token.isnot(None),
            )
        )
        for r in res2.all():
            if r[0]:
                tokens.add(r[0])
    except Exception:
        pass

    return list(tokens)


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
    Uses Haversine pre-filter then OSRM for final check (mirrors monolith).
    """
    for stop in stops:
        if stop["route_id"] != trip.route_id:
            continue
        if haversine_m(lat, lon, stop["lat"], stop["lon"]) > 400:
            continue  # pre-filter

        stop_id   = stop["id"]
        state_key = f"trip:{trip.id}:stop:{stop_id}:notified"
        if await redis.get(state_key):
            continue

        dist = await get_osrm_distance_m(lat, lon, stop["lat"], stop["lon"])
        if dist > STOP_ALERT_RADIUS_M:
            continue

        stop_name = stop.get("name", "your stop")

        boarding_tokens  = await _get_boarding_tokens(db, trip.route_id, stop_id, trip.direction)
        dest_tokens      = await _get_destination_tokens(db, trip.route_id, stop_id, trip.direction)

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
    trip:     Trip,
    lat:      float,
    lon:      float,
    dest_lat: float,
    dest_lon: float,
) -> None:
    """Fire once per trip when bus is > 10 min late vs scheduled end."""
    if trip.eta_notif_sent:
        return

    duration_s   = await get_osrm_duration_s(lat, lon, dest_lat, dest_lon)
    eta_min      = (duration_s / 60.0) * BUS_FACTOR
    now_ist      = to_ist(datetime.now(timezone.utc))
    time_mins    = now_ist.hour * 60 + now_ist.minute
    sched_end    = MORNING_END_MINS if trip.direction == "forward" else EVENING_END_MINS
    expected_end = sched_end - 30  # expected arrival is 30 min before window close
    eta_arrival  = time_mins + eta_min
    delay_min    = eta_arrival - expected_end

    if delay_min < 10:
        return

    arrival_dt  = now_ist + timedelta(minutes=eta_min)
    arrival_str = arrival_dt.strftime("%-I:%M %p")

    # Get all users with notifications on
    result = await db.execute(
        select(UserNotificationPreference.fcm_token).where(
            UserNotificationPreference.route_id  == trip.route_id,
            UserNotificationPreference.is_active == True,
        )
    )
    tokens = [r[0] for r in result.all() if r[0]]

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

    trip.eta_notif_sent = True
    await db.commit()
    logger.info("[LIFECYCLE] ETA late notification fired for trip #%d (%.0f min late)", trip.id, delay_min)


#  Visited stops + auto-catch-up 

async def update_visited_stops(db: AsyncSession, bus: EventBus, trip: Trip, lat: float, lon: float, stops: List[dict]) -> None:
    """OSRM distance matrix to unvisited stops; mark within STOP_ALERT_RADIUS_M visited."""
    visited     = list(trip.visited_stops or [])
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

    # Auto catch-up: find the furthest reached stop and mark all preceding stops visited
    furthest = max(reached, key=lambda x: x["order_index"] if is_fwd else -x["order_index"])
    for s in route_stops:
        if s["id"] in visited_set or s["id"] in new_ids:
            continue
        if is_fwd and s["order_index"] < furthest["order_index"]:
            new_ids.append(s["id"])
        elif not is_fwd and s["order_index"] > furthest["order_index"]:
            new_ids.append(s["id"])

    trip.visited_stops = visited + new_ids
    flag_modified(trip, "visited_stops")
    
    # Dynamic Delay Recalculation
    now_ist = to_ist(datetime.now(timezone.utc))
    time_mins = now_ist.hour * 60 + now_ist.minute
    sched_str = furthest.get("morning_time") if is_fwd else furthest.get("evening_time")
    sched_mins = parse_time_to_minutes(sched_str)
    
    if sched_mins > 0:
        trip.late_by_minutes = time_mins - sched_mins
        
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
            is_morning = t.direction in ("forward", "morning", "Morning")
            if is_morning and time_mins > MORNING_END_MINS:
                expired = True
            elif not is_morning and time_mins > EVENING_END_MINS:
                expired = True

        if expired:
            t.status   = "completed"
            modified   = True
            logger.info("[LIFECYCLE] Trip #%d expired (%s %s) -> completed", t.id, t.date, t.direction)

    if modified:
        await db.commit()
    return modified


#  POWER event handlers 

async def handle_power_on(db: AsyncSession, bus: EventBus, now_utc: datetime) -> None:
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)
    if trip:
        _cancel_grace_timer(trip.id)
    logger.info("[LIFECYCLE] POWER_RESTORE received")


async def handle_power_off(db: AsyncSession, bus: EventBus, now_utc: datetime) -> None:
    now_ist = to_ist(now_utc)
    trip    = await get_active_trip(db, now_ist)

    near_destination = False
    if trip and trip.status in ("on_trip", "late"):
        last_res = await db.execute(
            select(GpsLog).where(GpsLog.lat.isnot(None)).order_by(desc(GpsLog.id)).limit(1)
        )
        last_log = last_res.scalar_one_or_none()
        if last_log:
            dest_lat, dest_lon = await get_destination_coords(db, trip.direction)
            dist = await get_osrm_distance_m(last_log.lat, last_log.lon, dest_lat, dest_lon)
            near_destination = dist <= DESTINATION_RADIUS_M
            logger.info("[LIFECYCLE] POWER_OFF: trip #%d %.0fm from destination", trip.id, dist)

        if near_destination and trip.id not in COMPLETION_TIMERS:
            task = asyncio.create_task(_complete_trip_after_grace(trip.id, bus))
            COMPLETION_TIMERS[trip.id] = task
            logger.info("[LIFECYCLE] Trip #%d — grace timer started (%ds)", trip.id, GRACE_PERIOD_S)

    logger.info("[LIFECYCLE] POWER_LOST near_destination=%s", near_destination)


#  Main GPS event processor 

async def process_gps_event(event_data: dict, db: AsyncSession, redis: Redis) -> None:
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
    now_ist = to_ist(server_time)
    trip    = await get_active_trip(db, now_ist)
    if not trip:
        return

    #  scheduled -> on_trip 
    if trip.status == "scheduled":
        if trip.direction == "reverse" and now_ist.hour < 17:
            return
        start_lat, start_lon = await get_start_coords(db, trip.direction)
        dist_from_start      = await get_osrm_distance_m(start_lat, start_lon, lat, lon)
        logger.debug("[LIFECYCLE] Trip #%d %.0fm from start", trip.id, dist_from_start)
        if dist_from_start >= MOVEMENT_THRESHOLD_M:
            trip.status     = "on_trip"
            trip.started_at = server_time
            now_ist2        = to_ist(server_time)
            time_mins       = now_ist2.hour * 60 + now_ist2.minute
            depart_mins     = 450 if trip.direction in ("forward", "morning") else 1060
            delay           = time_mins - depart_mins
            trip.late_by_minutes = delay if delay > 2 else 0
            await db.commit()
            logger.info("[LIFECYCLE] Trip #%d -> on_trip (%.0fm from start)", trip.id, dist_from_start)
            await _notify_trip_started_event(bus, trip)
            ev = TripStatusEvent(trip_id=trip.id, status="on_trip", direction=trip.direction)
            await bus.publish("trip.status", ev)

    #  on_trip / late -> completed + proximity alerts 
    elif trip.status in ("on_trip", "late"):
        dest_lat, dest_lon = await get_destination_coords(db, trip.direction)

        dist_to_dest, stops = await asyncio.gather(
            get_osrm_distance_m(lat, lon, dest_lat, dest_lon),
            get_all_stops(db, redis, BusStop),
        )

        logger.debug("[LIFECYCLE] Trip #%d %.0fm from destination", trip.id, dist_to_dest)

        # Completion check
        completion_radius = 100 if trip.direction == "forward" else 50
        if dist_to_dest <= completion_radius:
            trip.status   = "completed"
            trip.ended_at = server_time
            await db.commit()
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
            await check_eta_late_notification(db, bus, trip, lat, lon, dest_lat, dest_lon)
        except Exception as exc:
            logger.warning("[LIFECYCLE] check_eta_late_notification failed: %s", exc)

        try:
            await run_proximity_alerts(db, bus, redis, trip, lat, lon, stops)
        except Exception as exc:
            logger.warning("[LIFECYCLE] run_proximity_alerts failed: %s", exc)
