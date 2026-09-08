# services/admin-service/main.py
"""
Admin Service — Internal dashboard API for managing trips, stops, and announcements.

All endpoints require either:
  - X-Admin-Token header (for programmatic access / admin panel)
  - POST /api/v1/admin/login (returns a session token)

Only the admin panel (running on the same server) should be able to reach
these endpoints. In production, Nginx blocks access to /api/v1/admin
from any IP other than the server's own local network.

Key responsibilities:
  - Trip management: create, start, end, cancel, update late status
  - Stop & route management: add/edit/reorder bus stops
  - GPS log viewing: see raw GPS data for debugging
  - Admin broadcasts: send messages to all students
  - Suggestion review: view/approve student suggestions

Endpoints:
  POST   /api/v1/admin/login          — Admin login (returns token)
  GET    /api/v1/admin/trips          — List trips
  POST   /api/v1/admin/trips          — Create trip
  PATCH  /api/v1/admin/trips/{id}     — Update trip status/late info
  GET    /api/v1/admin/stops          — List all stops
  POST   /api/v1/admin/stops          — Add a stop
  GET    /api/v1/admin/gps-logs       — View raw GPS logs
  POST   /api/v1/admin/broadcast      — Send announcement to all students
  GET    /health                      — Health check
"""
import asyncio
import html as html_mod
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Literal

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func, update

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.settings import get_settings
from libs.duk_common.redis_client import get_redis
from libs.duk_common.events import TripStatusChangedEvent, EventBus
from libs.duk_common.cache.stops_cache import invalidate_stops_cache
from libs.duk_common.audit_logger import record_audit_event

from database_local import get_db
from models_local import (
    Trip, BusStop, Route, GpsLog, User, AdminBroadcast,
    Suggestion, ScheduledNotification, InAppNotification, AdminAuditLog
)

logging.basicConfig(level=logging.INFO)
logger   = logging.getLogger(__name__)
from libs.duk_common.middleware import configure_app

settings = get_settings()

app = FastAPI(title="admin-service")
configure_app(app, allowed_origins=settings.allowed_origins_list)

IST_OFFSET      = timedelta(hours=5, minutes=30)
MORNING_END_MINS = 660    # 11:00 AM IST
EVENING_END_MINS = 1230   # 20:30 IST


#  Auth 

import hashlib
import secrets
import jwt
from libs.duk_common.security import constant_time_compare, RateLimiter
from libs.duk_common.rate_limit_deps import make_rate_limit_dep

# Rate limit: 5 login attempts per 15 minutes per IP
admin_login_limit = make_rate_limit_dep("admin:login", max_requests=5, window_seconds=900)


async def require_admin(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None),
):
    """
    Validates dynamic signed Admin JWT or static fallback ADMIN_TOKEN.
    Checks expiration and Redis revocation blacklist.
    """
    token = x_admin_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=403, detail="Admin access required")

    # 1. Allow fallback static ADMIN_TOKEN for system scripts
    if constant_time_compare(token, settings.ADMIN_TOKEN):
        return {"sub": "admin", "role": "admin"}

    # 2. Decode and validate signed JWT
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin privileges required")
        
        jti = payload.get("jti")
        if jti:
            try:
                redis = await get_redis()
                if await redis.get(f"revoked_token:{jti}"):
                    raise HTTPException(status_code=401, detail="Admin session revoked")
            except HTTPException:
                raise
            except Exception:
                pass  # allow if redis unreachable

        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Admin session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=403, detail="Invalid admin token")


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username", "password")
    @classmethod
    def no_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v


@app.post("/api/v1/admin/login")
async def admin_login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _limit: None = Depends(admin_login_limit),
):
    username_ok = constant_time_compare(req.username, settings.ADMIN_USERNAME)
    password_ok = constant_time_compare(req.password, settings.ADMIN_PASSWORD)

    if not (username_ok and password_ok):
        await record_audit_event(
            db=db,
            request=request,
            action="LOGIN_FAILED",
            admin_username=req.username[:50],
            status_code=401,
            success=False,
            changes={"attempted_username": req.username[:50], "reason": "Invalid credentials"}
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Issue dynamic signed 24h JWT session token
    session_jti = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc)
    payload = {
        "sub": req.username,
        "role": "admin",
        "jti": session_jti,
        "exp": now_utc + timedelta(hours=24),
        "iat": now_utc,
    }
    jwt_token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

    await record_audit_event(
        db=db,
        request=request,
        action="LOGIN_SUCCESS",
        admin_username=req.username,
        status_code=200,
        success=True,
        changes={"message": "Admin session authenticated successfully"}
    )
    return {"token": jwt_token}


@app.post("/api/v1/admin/logout")
async def admin_logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    jti = auth_data.get("jti") if isinstance(auth_data, dict) else None
    if jti:
        try:
            redis = await get_redis()
            await redis.set(f"revoked_token:{jti}", "1", ex=86400)
        except Exception:
            pass

    await record_audit_event(
        db=db,
        request=request,
        action="LOGOUT",
        admin_username=auth_data.get("sub", "admin") if isinstance(auth_data, dict) else "admin",
        status_code=200,
        success=True,
        changes={"message": "Admin session terminated"}
    )
    return {"success": True}



#  Helpers 

async def _broadcast_to_all_users(db: AsyncSession, title: str, body: str, data: dict = None):
    """FCM push to all verified users with notifications on. Creates in-app record."""
    try:
        from firebase_singleton import send_multicast_chunked

        result = await db.execute(
            select(User).where(
                User.verified == True,
                User.notifications_on != False,
                User.device_token.isnot(None),
            )
        )
        users  = result.scalars().all()
        tokens = [u.device_token for u in users if u.device_token]

        # In-app notification (broadcast — no user_id)
        notif = InAppNotification(title=title, body=body, type="admin_broadcast")
        db.add(notif)
        await db.commit()

        # FIX: send_multicast_chunked is async — must be awaited
        sent_count = await send_multicast_chunked(tokens, title, body, data)
        failed_count = len(tokens) - (sent_count or 0)

        db.add(AdminBroadcast(title=title, body=body, sent_count=sent_count, success=True))
        await db.commit()
        # Return field names that match what the frontend reads (data.sent, data.failed)
        return {"sent": sent_count, "failed": max(0, failed_count), "success": True}
    except Exception as e:
        logger.error("[ADMIN] FCM broadcast failed: %s", e)
        return {"sent": 0, "failed": 0, "success": False}


async def _void_trip_notifications(db: AsyncSession, trip_id: int):
    result = await db.execute(
        select(ScheduledNotification).where(
            and_(ScheduledNotification.trip_id == trip_id,
                 ScheduledNotification.sent      == False,
                 ScheduledNotification.cancelled  == False)
        )
    )
    for n in result.scalars().all():
        n.cancelled = True


async def auto_complete_expired_trips(db: AsyncSession) -> bool:
    now_ist   = datetime.now(timezone.utc) + IST_OFFSET
    time_mins = now_ist.hour * 60 + now_ist.minute
    today_ist = now_ist.date()

    result = await db.execute(
        select(Trip).where(Trip.status.in_(["scheduled", "on_trip", "active", "late"]))
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
            t.status = "completed"
            modified = True
            logger.info("[ADMIN] Trip #%d expired -> completed", t.id)
    if modified:
        await db.commit()
    return modified


#  Trip Management 

class TripStatusUpdate(BaseModel):
    trip_id:             int
    status:              Literal["active", "cancelled", "late", "stop_change", "scheduled"]
    late_by_minutes:     Optional[int] = None
    cancellation_reason: Optional[str] = None
    reason:              Optional[str] = None
    message:             Optional[str] = None


@app.post("/api/v1/admin/trip/status")
async def update_trip_status(
    req:   TripStatusUpdate,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    result = await db.execute(select(Trip).where(Trip.id == req.trip_id))
    trip   = result.scalar_one_or_none()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    trip.status = req.status
    if req.late_by_minutes is not None:
        trip.late_by_minutes = req.late_by_minutes
    if req.cancellation_reason:
        trip.cancellation_reason = req.cancellation_reason
    await db.commit()

    # Publish to Redis so realtime-gateway can broadcast to WS clients
    try:
        redis = await get_redis()
        bus   = EventBus(redis)
        ev    = TripStatusChangedEvent(
            trip_id=trip.id, status=trip.status, direction=trip.direction,
            late_by_minutes=req.late_by_minutes,
            cancellation_reason=req.cancellation_reason,
            message=req.message,
        )
        await bus.publish("trip.status", ev)
        await redis.publish("bus:broadcast", ev.model_dump_json())
    except Exception as e:
        logger.debug("[ADMIN] Redis broadcast failed: %s", e)

    # FCM push
    title, body = "", ""
    if req.status == "cancelled":
        title = "Bus Trip Cancelled"
        safe_reason = html_mod.escape(req.cancellation_reason or req.reason or "")
        body  = f"Today's trip has been cancelled. {safe_reason}".strip()
    elif req.status == "late":
        mins  = req.late_by_minutes or 0
        title = "Bus Running Late"
        body  = f"The bus will be approximately {mins} minutes late today."
        if req.reason:
            body += f" Reason: {html_mod.escape(req.reason)}"
    elif req.status == "stop_change":
        title = "Bus Stop Change"
        safe_msg = html_mod.escape(req.message) if req.message else ""
        body  = safe_msg or "There is a change to the bus stop schedule today."

    if title:
        await _broadcast_to_all_users(db, title, body, {"trip_id": str(req.trip_id), "status": req.status})

    return {"success": True, "status": req.status}


@app.get("/api/v1/admin/trips")
async def list_trips(
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    await auto_complete_expired_trips(db)
    cutoff_past   = date.today() - timedelta(days=10)
    cutoff_future = date.today() + timedelta(days=365)
    result = await db.execute(
        select(Trip)
        .where(Trip.date.between(cutoff_past, cutoff_future))
        .order_by(desc(Trip.date), desc(Trip.id))
    )
    trips = result.scalars().all()
    return [
        {
            "id":                  t.id,
            "date":                t.date.isoformat() if t.date else None,
            "direction":           t.direction,
            "status":              t.status,
            "late_by_minutes":     t.late_by_minutes,
            "cancellation_reason": t.cancellation_reason,
        }
        for t in trips
    ]


_ensure_trips_lock = asyncio.Lock()


@app.post("/api/v1/admin/trips/today")
async def ensure_today_trips(
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    await auto_complete_expired_trips(db)
    async with _ensure_trips_lock:
        today   = date.today()
        created = False
        days_ensured = 0
        i = 0
        while days_ensured < 3:
            target_date = today + timedelta(days=i)
            i += 1
            if target_date.weekday() >= 5:
                continue
            days_ensured += 1
            existing_result = await db.execute(
                select(Trip).where(and_(Trip.date == target_date, Trip.route_id == 1))
            )
            existing_directions = {t.direction for t in existing_result.scalars().all()}
            for direction in ["forward", "reverse"]:
                if direction not in existing_directions:
                    db.add(Trip(route_id=1, date=target_date, direction=direction, status="scheduled"))
                    created = True
        if created:
            await db.commit()

    all_today = await db.execute(
        select(Trip).where(and_(Trip.date == today, Trip.route_id == 1)).order_by(Trip.id)
    )
    today_trips = all_today.scalars().all()
    return {
        "created": created,
        "trips": [{"id": t.id, "direction": t.direction, "status": t.status} for t in today_trips],
    }


@app.post("/api/v1/admin/trip/start")
async def start_trip(
    req:   dict,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """Create a trip in 'scheduled' status and publish to Redis for trip-lifecycle."""
    route_id  = req.get("route_id", 1)
    direction = req.get("direction", "forward")
    trip_date = req.get("date", date.today().isoformat())
    d         = date.fromisoformat(trip_date)

    trip = Trip(route_id=route_id, bus_id=1, date=d, direction=direction, status="scheduled")
    db.add(trip)
    await db.commit()
    await db.refresh(trip)

    # Publish to Redis so realtime-gateway shows the new trip
    try:
        redis = await get_redis()
        await redis.publish("bus:trip_start", f'{{"trip_id": {trip.id}, "direction": "{direction}"}}')
    except Exception as e:
        logger.debug("[ADMIN] Redis trip_start publish failed: %s", e)

    return {"status": "ok", "trip_id": trip.id}


class TripCreateRequest(BaseModel):
    route_id:      int = 1
    direction:     str
    trip_date:     Optional[str] = None
    create_return: bool = False


@app.post("/api/v1/admin/trip/create")
async def create_trip(
    req:   TripCreateRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    d          = date.fromisoformat(req.trip_date) if req.trip_date else date.today()
    now_ist    = datetime.now(timezone.utc) + IST_OFFSET
    today_ist  = now_ist.date()
    now_mins   = now_ist.hour * 60 + now_ist.minute

    if d < today_ist:
        raise HTTPException(status_code=400, detail="Cannot create a trip for a past date.")
    if d == today_ist:
        if req.direction == "forward" and now_mins >= MORNING_END_MINS:
            raise HTTPException(status_code=400, detail="Morning window has passed.")
        if req.direction == "reverse" and now_mins >= EVENING_END_MINS:
            raise HTTPException(status_code=400, detail="Evening window has passed.")

    existing = (await db.execute(
        select(Trip).where(Trip.date == d, Trip.direction == req.direction)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Trip already exists with status '{existing.status}'.")

    db.add(Trip(route_id=req.route_id, date=d, direction=req.direction, status="scheduled"))
    if req.create_return and req.direction == "forward":
        if not (await db.execute(select(Trip).where(Trip.date == d, Trip.direction == "reverse"))).scalar_one_or_none():
            db.add(Trip(route_id=req.route_id, date=d, direction="reverse", status="scheduled"))
    await db.commit()
    return {"success": True}


class AdvanceCancelRequest(BaseModel):
    trip_date:          str
    direction:          str
    also_cancel_return: bool = False
    reason:             Optional[str] = None


@app.post("/api/v1/admin/trip/cancel-advance")
async def cancel_advance_trip(
    req:   AdvanceCancelRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    d      = date.fromisoformat(req.trip_date)
    reason = req.reason or "Pre-cancelled by admin"
    directions     = [req.direction]
    if req.also_cancel_return and req.direction == "forward":
        directions.append("reverse")

    cancelled_ids = []
    for direction in directions:
        result = await db.execute(
            select(Trip).where(and_(Trip.date == d, Trip.direction == direction, Trip.route_id == 1))
        )
        trip = result.scalar_one_or_none()
        if trip:
            if trip.status == "completed":
                raise HTTPException(status_code=400, detail="Cannot cancel a completed trip.")
            trip.status, trip.cancellation_reason = "cancelled", reason
        else:
            trip = Trip(route_id=1, date=d, direction=direction, status="cancelled", cancellation_reason=reason)
            db.add(trip)
        await db.flush()
        cancelled_ids.append(trip.id)

    await db.commit()

    dir_label  = "Morning" if req.direction == "forward" else "Evening"
    date_label = d.strftime("%d %B")
    title      = "Bus Service Cancelled"
    safe_reason = html_mod.escape(req.reason) if req.reason else ""
    body       = f"The {dir_label} bus on {date_label} has been cancelled.{(' ' + safe_reason) if safe_reason else ''}"
    await _broadcast_to_all_users(db, title, body, {"type": "advance_cancel", "date": req.trip_date})

    # Schedule deferred reminders
    now_ist = (datetime.now(timezone.utc) + IST_OFFSET).replace(tzinfo=None)
    for trip_id, direction in zip(cancelled_ids, directions):
        dir_l         = "Morning" if direction == "forward" else "Evening"
        reminder_body = f"Reminder: The {dir_l} bus on {date_label} is cancelled.{(' ' + safe_reason) if safe_reason else ''}"
        for day_offset in [-1, 0]:
            remind_date = d + timedelta(days=day_offset)
            if remind_date >= date.today():
                hour    = 6 if direction == "forward" else 15
                send_dt = datetime.combine(remind_date, datetime.min.time().replace(hour=hour))
                if send_dt > now_ist:
                    db.add(ScheduledNotification(trip_id=trip_id, title=title, body=reminder_body, send_at=send_dt))

    await db.commit()
    return {"success": True, "cancelled_ids": cancelled_ids}


class RevokeCancelRequest(BaseModel):
    trip_id: int
    reason:  Optional[str] = None


@app.post("/api/v1/admin/trip/restore")
async def restore_trip(
    req:   RevokeCancelRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    result = await db.execute(select(Trip).where(Trip.id == req.trip_id))
    trip   = result.scalar_one_or_none()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    if trip.status != "cancelled":
        raise HTTPException(status_code=400, detail="Trip is not cancelled")

    dir_label  = "Morning" if trip.direction == "forward" else "Evening"
    date_label = trip.date.strftime("%d %B") if trip.date else ""

    if trip.date and trip.date > date.today():
        await db.delete(trip)
        trip_deleted = True
    else:
        trip.status, trip.cancellation_reason = "scheduled", None
        trip_deleted = False
    await db.commit()

    await _void_trip_notifications(db, req.trip_id)
    await db.commit()

    title = "Bus Service Restored"
    safe_reason = html_mod.escape(req.reason) if req.reason else ""
    body  = f"The {dir_label} bus on {date_label} is back on schedule.{(' ' + safe_reason) if safe_reason else ''}"
    await _broadcast_to_all_users(db, title, body, {"type": "revoke_cancel", "trip_id": str(req.trip_id)})
    return {"success": True}


class RevokeCancelRangeRequest(BaseModel):
    trip_ids: List[int]
    reason:   Optional[str] = None


@app.post("/api/v1/admin/trip/restore-range")
async def restore_trip_range(
    req:   RevokeCancelRangeRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """Restore multiple cancelled trips at once and send a single consolidated push notification."""
    if not req.trip_ids:
        raise HTTPException(status_code=400, detail="trip_ids must not be empty")
    if len(req.trip_ids) > 50:
        raise HTTPException(status_code=400, detail="Cannot restore more than 50 trips at once")

    restored = []
    for trip_id in req.trip_ids:
        result = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip = result.scalar_one_or_none()
        if not trip or trip.status != "cancelled":
            continue
        if trip.date and trip.date > date.today():
            await db.delete(trip)
        else:
            trip.status, trip.cancellation_reason = "scheduled", None
        await _void_trip_notifications(db, trip_id)
        restored.append(trip_id)

    await db.commit()

    if restored:
        title = "Bus Service Restored"
        safe_reason = html_mod.escape(req.reason) if req.reason else ""
        body  = f"{len(restored)} trip(s) restored to schedule.{(' ' + safe_reason) if safe_reason else ''}"
        await _broadcast_to_all_users(db, title, body, {"type": "revoke_cancel_range"})

    return {"success": True, "restored_count": len(restored), "restored_ids": restored}


#  Stop Management 

@app.get("/api/v1/admin/stops")
async def list_stops(
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    result = await db.execute(select(BusStop).order_by(BusStop.route_id, BusStop.order_index))
    stops  = result.scalars().all()
    return [
        {
            "id": s.id, "route_id": s.route_id, "name": s.name,
            "lat": s.lat, "lon": s.lon, "order_index": s.order_index,
            "morning_time": s.morning_time, "evening_time": s.evening_time,
            "is_morning_origin": bool(s.is_morning_origin),
            "is_morning_destination": bool(s.is_morning_destination),
            "is_evening_origin": bool(s.is_evening_origin),
            "is_evening_destination": bool(s.is_evening_destination),
        }
        for s in stops
    ]


class StopCreateRequest(BaseModel):
    route_id:    int
    name:        str
    lat:         float
    lon:         float
    order_index: int
    morning_time: Optional[str] = None
    evening_time: Optional[str] = None


@app.post("/api/v1/admin/stops", status_code=201)
async def add_stop(
    req:   StopCreateRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    stop = BusStop(**req.model_dump())
    db.add(stop)
    await db.commit()
    await db.refresh(stop)
    redis = await get_redis()
    await invalidate_stops_cache(redis)
    return {"success": True, "id": stop.id}


class StopUpdateRequest(BaseModel):
    name:         Optional[str]   = None
    lat:          Optional[float] = None
    lon:          Optional[float] = None
    order_index:  Optional[int]   = None
    morning_time: Optional[str]   = None
    evening_time: Optional[str]   = None

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v):
        if v is not None and not (-90 <= v <= 90):
            raise ValueError("lat must be between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, v):
        if v is not None and not (-180 <= v <= 180):
            raise ValueError("lon must be between -180 and 180")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is not None and not v.strip():
            raise ValueError("name cannot be empty")
        return v


@app.put("/api/v1/admin/stops/{stop_id}")
async def edit_stop(
    stop_id: int,
    req:     StopUpdateRequest,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop   = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")
    # Only update the explicitly permitted fields — never allow arbitrary attribute writes
    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(stop, key, value)
    await db.commit()
    redis = await get_redis()
    await invalidate_stops_cache(redis)
    return {"success": True}


@app.delete("/api/v1/admin/stops/{stop_id}")
async def delete_stop(
    stop_id: int,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop   = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")

    # Safety: block deletion if any active or scheduled trip references this stop
    active_trip = await db.execute(
        select(Trip).where(
            Trip.status.in_(["active", "scheduled", "late", "on_trip"]),
            Trip.route_id == stop.route_id,
        ).limit(1)
    )
    if active_trip.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete stop '{stop.name}': there are active or scheduled trips on this route.",
        )

    await db.delete(stop)
    await db.commit()
    redis = await get_redis()
    await invalidate_stops_cache(redis)
    return {"success": True}


# Role field names accepted by PUT /stops/{id}/role
_VALID_ROLES = {
    "morning_origin",
    "morning_destination",
    "evening_origin",
    "evening_destination",
}


class StopRoleRequest(BaseModel):
    role: str


@app.put("/api/v1/admin/stops/{stop_id}/role")
async def set_stop_role(
    stop_id: int,
    req:     StopRoleRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    """Set the terminal role of a stop (morning/evening origin/destination)."""
    if req.role not in _VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{req.role}'. Must be one of: {', '.join(sorted(_VALID_ROLES))}.",
        )

    result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop   = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")

    # Map role name to boolean column pairs: clear the old stop with the same role first
    col = f"is_{req.role}"  # e.g. is_morning_origin
    if not hasattr(stop, col):
        raise HTTPException(status_code=400, detail=f"Stop model has no attribute '{col}'")

    # Clear any other stop currently holding this role on the same route
    await db.execute(
        update(BusStop)
        .where(BusStop.route_id == stop.route_id, BusStop.id != stop_id)
        .values({col: False})
    )
    await db.execute(
        update(BusStop)
        .where(BusStop.id == stop_id)
        .values({col: True})
    )
    await db.commit()
    redis = await get_redis()
    await invalidate_stops_cache(redis)

    await record_audit_event(
        db=db,
        request=request,
        action="STOP_ROLE_CHANGED",
        admin_username="admin",
        endpoint=f"/api/v1/admin/stops/{stop_id}/role",
        changes={"stop_id": stop_id, "stop_name": stop.name, "new_role": req.role}
    )

    return {"success": True, "role": req.role, "stop_id": stop_id}


@app.post("/api/v1/admin/stops/reorder")
async def reorder_stops(
    req:     List[dict],
    request: Request,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    for item in req:
        stop_id     = item.get("id")
        order_index = item.get("order_index")
        if stop_id and order_index is not None:
            await db.execute(
                update(BusStop).where(BusStop.id == stop_id).values(order_index=order_index)
            )
    await db.commit()
    redis = await get_redis()
    await invalidate_stops_cache(redis)

    await record_audit_event(
        db=db,
        request=request,
        action="STOPS_REORDERED",
        admin_username="admin",
        endpoint="/api/v1/admin/stops/reorder",
        changes={"reordered_count": len(req)}
    )

    return {"success": True}


#  GPS / Data 

@app.get("/api/v1/admin/gps/history")
async def gps_history(
    limit:     int = Query(5000, le=10000),
    offset:    int = 0,
    from_date: Optional[str] = None,
    to_date:   Optional[str] = None,
    db:        AsyncSession = Depends(get_db),
    _auth:     None = Depends(require_admin),
):
    from sqlalchemy import cast
    import sqlalchemy as sa

    stmt = select(GpsLog).where(GpsLog.lat.isnot(None))
    if from_date:
        try:
            d_from = date.fromisoformat(from_date)
            stmt = stmt.where(sa.cast(GpsLog.ist_time, sa.Date) >= d_from)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD")
    if to_date:
        try:
            # to_date is inclusive — include the entire day
            d_to = date.fromisoformat(to_date)
            stmt = stmt.where(sa.cast(GpsLog.ist_time, sa.Date) <= d_to)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD")
    stmt = stmt.order_by(sa.asc(GpsLog.ist_time)).limit(limit).offset(offset)

    result = await db.execute(stmt)
    logs = result.scalars().all()
    if not logs:
        return {"sessions": []}

    return {
        "sessions": [
            {
                "route_points": [
                    {
                        "id": l.id,
                        "lat": float(l.lat),
                        "lon": float(l.lon),
                        "speed": float(l.speed) if l.speed else 0.0,
                        "time": l.ist_time.strftime("%H:%M") if l.ist_time else "",
                        "ist_time": l.ist_time.isoformat() if l.ist_time else None,
                        "event": l.event
                    }
                    for l in logs
                ],
                "stop_crossings": []
            }
        ]
    }


#  Broadcasts & Suggestions 

class BroadcastRequest(BaseModel):
    title: str
    body:  str

    @field_validator("title")
    @classmethod
    def title_length(cls, v: str) -> str:
        if len(v.strip()) == 0:
            raise ValueError("Title cannot be empty")
        if len(v) > 100:
            raise ValueError("Title must be 100 characters or fewer")
        return html_mod.escape(v.strip())

    @field_validator("body")
    @classmethod
    def body_length(cls, v: str) -> str:
        if len(v.strip()) == 0:
            raise ValueError("Body cannot be empty")
        if len(v) > 500:
            raise ValueError("Body must be 500 characters or fewer")
        return html_mod.escape(v.strip())


@app.post("/api/v1/admin/broadcast")
async def send_broadcast(
    req:     BroadcastRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    result = await _broadcast_to_all_users(db, req.title, req.body, {"type": "admin_broadcast"})
    await record_audit_event(
        db=db,
        request=request,
        action="BROADCAST_SENT",
        admin_username="admin",
        endpoint="/api/v1/admin/broadcast",
        changes={
            "title": req.title,
            "body_preview": req.body[:100],
            "sent_count": result.get("sent", 0),
            "failed_count": result.get("failed", 0),
            "success": result.get("success", False)
        }
    )
    return result


@app.get("/api/v1/admin/suggestions")
async def list_suggestions(
    status: Optional[str] = None,
    db:     AsyncSession = Depends(get_db),
    _auth:  None = Depends(require_admin),
):
    stmt = select(Suggestion, User.email).outerjoin(User, Suggestion.user_id == User.id).order_by(desc(Suggestion.id))
    if status:
        stmt = stmt.where(Suggestion.status == status)
    result = await db.execute(stmt.limit(100))
    items  = result.all()
    return [
        {
            "id": s.Suggestion.id, "suggestion": s.Suggestion.suggestion, "status": s.Suggestion.status,
            "created_at": s.Suggestion.created_at.isoformat() if s.Suggestion.created_at else None,
            "admin_response": s.Suggestion.admin_response,
            "user_email": s.email
        }
        for s in items
    ]


class SuggestionResponseRequest(BaseModel):
    response: str
    status:   str = "resolved"


@app.post("/api/v1/admin/suggestions/{suggestion_id}/respond")
async def respond_to_suggestion(
    suggestion_id: int,
    req:   SuggestionResponseRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    result = await db.execute(select(Suggestion).where(Suggestion.id == suggestion_id))
    s      = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    s.admin_response = req.response[:2000]  # enforce max length
    s.status         = req.status
    await db.commit()
    return {"success": True}


@app.delete("/api/v1/admin/suggestions/{suggestion_id}")
async def delete_suggestion(
    suggestion_id: int,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    result = await db.execute(select(Suggestion).where(Suggestion.id == suggestion_id))
    s      = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    await db.delete(s)
    await db.commit()
    return {"success": True}


#  ML Retrain Trigger 

@app.post("/api/v1/admin/ml/retrain")
async def trigger_ml_retrain(
    _auth: None = Depends(require_admin),
):
    """Publish a signal on Redis for the ML training job to pick up (or trigger a K8s job)."""
    try:
        redis = await get_redis()
        await redis.publish("ml:retrain_requested", "1")
        return {"success": True, "message": "Retrain signal sent"}
    except Exception as e:
        # Log the real exception server-side; return a generic message to the client
        logger.error("[ADMIN] Failed to signal ML retrain: %s", e)
        raise HTTPException(status_code=500, detail="Failed to queue retraining request.")


#  Health 

@app.get("/health")
async def health():
    return {"status": "ok"}
