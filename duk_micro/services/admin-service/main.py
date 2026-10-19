# services/admin-service/main.py
"""
Admin Service — Internal dashboard API for managing trips, stops, and announcements.

All endpoints require a valid admin JWT (obtained via POST /api/v1/admin/login),
supplied either as:
  - Authorization: Bearer <admin-jwt>
  - X-Admin-Token: <admin-jwt>   (same signed JWT, alternate header — NOT a static secret)

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
import zoneinfo
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Literal

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request, Response, Cookie
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator, Field
from sqlalchemy import select, desc, and_, update, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.settings import get_settings
from libs.duk_common.redis_client import get_redis, redis_lock
from libs.duk_common.events import TripStatusChangedEvent, EventBus
from libs.duk_common.cache.stops_cache import invalidate_stops_cache
from libs.duk_common.audit_logger import record_audit_event
from libs.duk_common.constants import normalize_direction, MORNING_END_MINS, EVENING_END_MINS

from database_local import get_db
from libs.duk_common.models import (
    Trip, BusStop, Route, GpsLog, User, AdminBroadcast,
    Suggestion, ScheduledNotification, InAppNotification
)

logging.basicConfig(level=logging.INFO)
logger   = logging.getLogger(__name__)
from libs.duk_common.middleware import configure_app

settings = get_settings()

app = FastAPI(title="admin-service", docs_url=None, redoc_url=None)
admin_origins = [o for o in settings.allowed_origins_list if o != '*'] or ["http://localhost:5173"]
configure_app(app, allowed_origins=admin_origins, environment=settings.ENVIRONMENT)



#  Auth 

import jwt
from libs.duk_common.security import constant_time_compare, TokenBlocklist
from libs.duk_common.rate_limit_deps import make_rate_limit_dep

# Rate limit: 5 login attempts per 15 minutes per IP
admin_login_limit = make_rate_limit_dep("admin:login", max_requests=5, window_seconds=900)


async def require_admin(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(None),
    admin_session: Optional[str] = Cookie(None),
):
    """
    Validates dynamic signed Admin JWT.
    Checks expiration and Redis revocation blacklist.

    Token source order: the HttpOnly `admin_session` cookie (browser dashboard),
    then the X-Admin-Token / Authorization headers (non-browser/API callers).
    """
    token = admin_session or x_admin_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=403, detail="Admin access required")

    # 1. Decode and validate signed JWT
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin privileges required")
        
        jti = payload.get("jti")
        if jti:
            try:
                redis = await get_redis()
                bl = TokenBlocklist(redis)
                if await bl.is_revoked(jti):
                    raise HTTPException(status_code=401, detail="Admin session revoked")
            except HTTPException:
                raise
            except Exception as e:
                logger.error("[ADMIN] Redis unavailable for token revocation check — failing closed: %s", e)
                raise HTTPException(status_code=503, detail="Service temporarily unavailable")

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
        """Rejects empty username or password values."""
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v


@app.post("/api/v1/admin/login")
async def admin_login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _limit: None = Depends(admin_login_limit),
):
    """Authenticates the admin and sets a signed JWT session as an HttpOnly cookie."""
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
    jwt_token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    await record_audit_event(
        db=db,
        request=request,
        action="LOGIN_SUCCESS",
        admin_username=req.username,
        status_code=200,
        success=True,
        changes={"message": "Admin session authenticated successfully"}
    )

    # Set the session as an HttpOnly cookie so the JWT is never exposed to
    # JavaScript (defeats token theft via XSS). Secure in production; SameSite
    # strict blocks cross-site CSRF. 24h matches the JWT's own exp.
    response.set_cookie(
        key="admin_session",
        value=jwt_token,
        max_age=86400,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return {"success": True}


@app.post("/api/v1/admin/logout")
async def admin_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Revokes the authenticated admin JWT session and clears the session cookie."""
    jti = auth_data.get("jti") if isinstance(auth_data, dict) else None
    if jti:
        try:
            redis = await get_redis()
            bl = TokenBlocklist(redis)
            await bl.revoke(jti, expires_in_seconds=86400)
        except Exception as e:
            logger.error("[ADMIN] Redis unavailable during logout revocation: %s", e)
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    response.delete_cookie("admin_session", path="/")

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

def get_ist_now():
    """Returns the current date and time in Indian Standard Time."""
    return datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata"))

def get_ist_today():
    """Returns today's date in Indian Standard Time."""
    return get_ist_now().date()


async def _broadcast_to_all_users(db: AsyncSession, title: str, body: str, data: dict = None):
    """FCM push to all verified users with notifications on. Creates in-app record."""
    try:
        from libs.duk_common.firebase_app import send_multicast_chunked
        from libs.duk_common.notification_helpers import get_notification_tokens
        from libs.duk_common.models import User

        tokens = await get_notification_tokens(db, pref_column=User.notify_admin_broadcasts)

        # In-app notification (broadcast — no user_id)
        notif = InAppNotification(title=title, body=body, type="admin_broadcast")
        db.add(notif)
        await db.commit()

        # FIX: send_multicast_chunked is async — must be awaited
        sent_count, failed_count = await send_multicast_chunked(tokens, title, body, data)

        broadcast = AdminBroadcast(title=title, body=body, sent_count=sent_count, success=True)
        db.add(broadcast)
        await db.flush()
        await db.commit()
        # Return field names that match what the frontend reads (data.sent, data.failed)
        return {"sent": sent_count, "failed": max(0, failed_count), "success": True}
    except Exception as e:
        await db.rollback()
        logger.error("[ADMIN] FCM broadcast failed: %s", e)
        return {"sent": 0, "failed": 0, "success": False}


async def _void_trip_notifications(db: AsyncSession, trip_id: int):
    """Cancels pending scheduled notifications for a trip."""
    result = await db.execute(
        select(ScheduledNotification).where(
            and_(ScheduledNotification.trip_id == trip_id,
                 ScheduledNotification.status == "pending")
        )
    )
    for n in result.scalars().all():
        n.status = "cancelled"


async def auto_complete_expired_trips(db: AsyncSession) -> bool:
    """Marks expired trips as completed based on their schedule and current time."""
    now_ist   = get_ist_now()
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
            is_morning = normalize_direction(t.direction) == "forward"
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

    @field_validator("late_by_minutes")
    @classmethod
    def validate_late_by_minutes(cls, v):
        """Ensure late_by_minutes is not negative."""
        if v is not None and v < 0:
            raise ValueError("late_by_minutes cannot be negative")
        return v


@app.post("/api/v1/admin/trip/status")
async def update_trip_status(
    req:     TripStatusUpdate,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    result = await db.execute(select(Trip).where(Trip.id == req.trip_id))
    trip   = result.scalar_one_or_none()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    # "stop_change" is a transient announcement to passengers, NOT a lifecycle
    # state. Persisting it onto trip.status would strand the trip in a status no
    # other code (trip_state, lifecycle) understands, effectively removing the
    # bus from normal handling. So only persist real lifecycle statuses here;
    # the announcement is still broadcast + pushed below.
    if req.status != "stop_change":
        trip.status = req.status
    if req.late_by_minutes is not None:
        trip.late_by_minutes = req.late_by_minutes
    if req.cancellation_reason:
        trip.cancellation_reason = req.cancellation_reason
    await db.commit()

    # Publish to Redis so realtime-gateway can broadcast to WS clients.
    # Broadcast req.status (the admin's intended signal, incl. "stop_change")
    # rather than trip.status, so clients still see the announcement even though
    # it was not persisted onto the trip row.
    try:
        redis = await get_redis()
        bus   = EventBus(redis)
        ev    = TripStatusChangedEvent(
            trip_id=trip.id, status=req.status, direction=trip.direction,
            late_by_minutes=req.late_by_minutes,
            cancellation_reason=req.cancellation_reason,
            message=req.message,
        )
        await bus.publish("trip.status", ev)
        await redis.publish("bus:broadcast", ev.model_dump_json())
    except Exception as e:
        logger.error("[ADMIN] Redis broadcast failed: %s", e)

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

    await record_audit_event(
        db=db,
        request=request,
        action="TRIP_STATUS_UPDATED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/trip/status",
        changes={"trip_id": req.trip_id, "new_status": req.status,
                 "late_by_minutes": req.late_by_minutes, "reason": req.cancellation_reason or req.reason}
    )

    return {"success": True, "status": req.status}


@app.get("/api/v1/admin/trips")
async def list_trips(
    limit: int = Query(100, le=500),
    offset: int = 0,
    db:    AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Lists all trips for a given date range, defaulting to the last 10 days to 1 year in the future.

    Args:
        limit: Maximum number of trips to return
        offset: Number of trips to skip
        db: Database session
        auth_data: Authentication data from the admin token
    """
    await auto_complete_expired_trips(db)
    cutoff_past   = get_ist_today() - timedelta(days=10)
    cutoff_future = get_ist_today() + timedelta(days=365)
    result = await db.execute(
        select(Trip)
        .where(Trip.date.between(cutoff_past, cutoff_future))
        .order_by(desc(Trip.date), desc(Trip.id))
        .limit(limit).offset(offset)
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


class EnsureTripsRequest(BaseModel):
    route_id: int = 1

@app.post("/api/v1/admin/trips/today")
async def ensure_today_trips(
    req:   EnsureTripsRequest,
    request: Request,
    db:    AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Creates all missing trips for the next 3 weekdays if not already present."""
    await auto_complete_expired_trips(db)
    # Cross-process lock (was a process-local asyncio.Lock) so two workers can't
    # both insert the same day's trips concurrently.
    redis = await get_redis()
    async with redis_lock(redis, f"lock:ensure_trips:{req.route_id}", ttl_s=15.0, wait_s=10.0):
        today   = get_ist_today()
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
                select(Trip).where(and_(Trip.date == target_date, Trip.route_id == req.route_id))
            )
            existing_directions = {t.direction for t in existing_result.scalars().all()}
            for direction in ["forward", "reverse"]:
                if direction not in existing_directions:
                    db.add(Trip(route_id=req.route_id, date=target_date, direction=direction, status="scheduled"))
                    created = True
        if created:
            await db.commit()

    all_today = await db.execute(
        select(Trip).where(and_(Trip.date == today, Trip.route_id == req.route_id)).order_by(Trip.id)
    )
    today_trips = all_today.scalars().all()

    await record_audit_event(
        db=db,
        request=request,
        action="TRIPS_ENSURED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/trips/today",
        changes={"route_id": req.route_id, "created": created}
    )

    return {
        "created": created,
        "trips": [{"id": t.id, "direction": t.direction, "status": t.status} for t in today_trips],
    }


class TripStartRequest(BaseModel):
    route_id:  int = 1
    bus_id:    int = 1
    direction: Literal["forward", "reverse"] = "forward"
    date:      Optional[str] = None


@app.post("/api/v1/admin/trip/start")
async def start_trip(
    req:     TripStartRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Create a trip in 'active' status and publish to Redis for trip-lifecycle."""
    route_id  = req.route_id
    direction = req.direction
    trip_date = req.date or get_ist_today().isoformat()
    d         = date.fromisoformat(trip_date)

    # Route and Bus validation
    route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
        
    # Check for active trip
    active_trip = (await db.execute(
        select(Trip).where(
            Trip.route_id == route_id,
            Trip.bus_id == req.bus_id,
            Trip.status == "active",
            Trip.date == d
        )
    )).scalar_one_or_none()
    if active_trip:
        raise HTTPException(status_code=409, detail="An active trip already exists for this route")

    trip = Trip(route_id=route_id, bus_id=req.bus_id, date=d, direction=direction, status="active")
    db.add(trip)
    await db.commit()
    await db.refresh(trip)

    # Publish to Redis so realtime-gateway shows the new trip
    try:
        redis = await get_redis()
        await redis.publish("bus:trip_start", f'{{"trip_id": {trip.id}, "direction": "{direction}"}}')
    except Exception as e:
        logger.debug("[ADMIN] Redis trip_start publish failed: %s", e)

    await record_audit_event(
        db=db,
        request=request,
        action="TRIP_STARTED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/trip/start",
        changes={"trip_id": trip.id, "direction": direction, "date": str(d)}
    )

    return {"status": "ok", "trip_id": trip.id}


class TripCreateRequest(BaseModel):
    route_id:      int = 1
    direction:     Literal["forward", "reverse"]
    trip_date:     Optional[str] = None
    create_return: bool = False


@app.post("/api/v1/admin/trip/create")
async def create_trip(
    req:     TripCreateRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Creates a scheduled trip and optionally its return trip."""
    d          = date.fromisoformat(req.trip_date) if req.trip_date else get_ist_today()
    now_ist    = get_ist_now()
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
        select(Trip).where(Trip.date == d, Trip.direction == req.direction, Trip.route_id == req.route_id)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Trip already exists with status '{existing.status}'.")

    db.add(Trip(route_id=req.route_id, date=d, direction=req.direction, status="scheduled"))
    if req.create_return and req.direction == "forward":
        if not (await db.execute(select(Trip).where(Trip.date == d, Trip.direction == "reverse"))).scalar_one_or_none():
            db.add(Trip(route_id=req.route_id, date=d, direction="reverse", status="scheduled"))
    await db.commit()

    await record_audit_event(
        db=db,
        request=request,
        action="TRIP_CREATED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/trip/create",
        changes={"route_id": req.route_id, "direction": req.direction, "date": str(d), "create_return": req.create_return}
    )

    return {"success": True}


class AdvanceCancelRequest(BaseModel):
    route_id:           int = 1
    trip_date:          str
    direction:          Literal["forward", "reverse"]
    also_cancel_return: bool = False
    reason:             Optional[str] = None


@app.post("/api/v1/admin/trip/cancel-advance")
async def cancel_advance_trip(
    req:     AdvanceCancelRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Cancels upcoming trips, notifies users, and schedules reminders."""
    d      = date.fromisoformat(req.trip_date)
    reason = req.reason or "Pre-cancelled by admin"
    directions     = [req.direction]
    if req.also_cancel_return and req.direction == "forward":
        directions.append("reverse")

    cancelled_ids = []
    for direction in directions:
        result = await db.execute(
            select(Trip).where(and_(Trip.date == d, Trip.direction == direction, Trip.route_id == req.route_id))
        )
        trip = result.scalar_one_or_none()
        if trip:
            if trip.status == "completed":
                raise HTTPException(status_code=400, detail="Cannot cancel a completed trip.")
            trip.status, trip.cancellation_reason = "cancelled", reason
        else:
            trip = Trip(route_id=req.route_id, date=d, direction=direction, status="cancelled", cancellation_reason=reason)
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
    for trip_id, direction in zip(cancelled_ids, directions):
        dir_l         = "Morning" if direction == "forward" else "Evening"
        reminder_body = f"Reminder: The {dir_l} bus on {date_label} is cancelled.{(' ' + safe_reason) if safe_reason else ''}"
        for day_offset in [-1, 0]:
            remind_date = d + timedelta(days=day_offset)
            if remind_date >= get_ist_today():
                hour    = 6 if direction == "forward" else 15
                send_dt_naive_ist = datetime.combine(remind_date, datetime.min.time().replace(hour=hour))
                send_dt_ist = send_dt_naive_ist.replace(tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))
                send_dt_utc = send_dt_ist.astimezone(timezone.utc)
                if send_dt_utc > datetime.now(timezone.utc):
                    db.add(ScheduledNotification(trip_id=trip_id, title=title, body=reminder_body, send_at=send_dt_utc))

    await db.commit()

    await record_audit_event(
        db=db,
        request=request,
        action="TRIP_ADVANCE_CANCELLED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/trip/cancel-advance",
        changes={"date": req.trip_date, "direction": req.direction, "reason": req.reason, "cancelled_ids": cancelled_ids}
    )

    return {"success": True, "cancelled_ids": cancelled_ids}


class RevokeCancelRequest(BaseModel):
    trip_id: int
    reason:  Optional[str] = None


@app.post("/api/v1/admin/trip/restore")
async def restore_trip(
    req:     RevokeCancelRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Restores a cancelled trip and cancels its pending reminders."""
    result = await db.execute(select(Trip).where(Trip.id == req.trip_id))
    trip   = result.scalar_one_or_none()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    if trip.status != "cancelled":
        raise HTTPException(status_code=400, detail="Trip is not cancelled")

    dir_label  = "Morning" if trip.direction == "forward" else "Evening"
    date_label = trip.date.strftime("%d %B") if trip.date else ""

    trip.status, trip.cancellation_reason = "scheduled", None
    await db.commit()

    await _void_trip_notifications(db, req.trip_id)
    await db.commit()

    title = "Bus Service Restored"
    safe_reason = html_mod.escape(req.reason) if req.reason else ""
    body  = f"The {dir_label} bus on {date_label} is back on schedule.{(' ' + safe_reason) if safe_reason else ''}"
    await _broadcast_to_all_users(db, title, body, {"type": "revoke_cancel", "trip_id": str(req.trip_id)})

    await record_audit_event(
        db=db,
        request=request,
        action="TRIP_RESTORED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/trip/restore",
        changes={"trip_id": req.trip_id, "reason": req.reason}
    )

    return {"success": True}


class RevokeCancelRangeRequest(BaseModel):
    trip_ids: List[int]
    reason:   Optional[str] = None


@app.post("/api/v1/admin/trip/restore-range")
async def restore_trip_range(
    req:     RevokeCancelRangeRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Restore multiple cancelled trips at once and send a single consolidated push notification."""
    if not req.trip_ids:
        raise HTTPException(status_code=400, detail="trip_ids must not be empty")
    if len(req.trip_ids) > 50:
        raise HTTPException(status_code=400, detail="Cannot restore more than 50 trips at once")

    restored = []
    result = await db.execute(select(Trip).where(Trip.id.in_(req.trip_ids)))
    trips = result.scalars().all()

    for trip in trips:
        if trip.status != "cancelled":
            continue
        trip.status, trip.cancellation_reason = "scheduled", None
        await _void_trip_notifications(db, trip.id)
        restored.append(trip.id)

    await db.commit()

    if restored:
        title = "Bus Service Restored"
        safe_reason = html_mod.escape(req.reason) if req.reason else ""
        body  = f"{len(restored)} trip(s) restored to schedule.{(' ' + safe_reason) if safe_reason else ''}"
        await _broadcast_to_all_users(db, title, body, {"type": "revoke_cancel_range"})

    await record_audit_event(
        db=db,
        request=request,
        action="TRIPS_RESTORED_RANGE",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/trip/restore-range",
        changes={"requested_ids": req.trip_ids, "restored_ids": restored, "reason": req.reason}
    )

    return {"success": True, "restored_count": len(restored), "restored_ids": restored}


#  Stop Management

@app.get("/api/v1/admin/stops")
async def list_stops(
    limit: int = Query(100, le=500),
    offset: int = 0,
    db:    AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Returns paginated bus stops for the admin dashboard."""
    result = await db.execute(select(BusStop).order_by(BusStop.route_id, BusStop.morning_order_index.nullslast()).limit(limit).offset(offset))
    stops  = result.scalars().all()
    return [
        {
            "id": s.id, "route_id": s.route_id, "name": s.name,
            "lat": s.lat, "lon": s.lon,
            "order_index":         s.order_index,
            "morning_order_index": s.morning_order_index if s.morning_order_index is not None else s.order_index,
            "evening_order_index": s.evening_order_index if s.evening_order_index is not None else s.order_index,
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
    morning_time: Optional[str] = None
    evening_time: Optional[str] = None

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v):
        if v is not None and not (-90 <= v <= 90):
            """Validates that latitude is within the valid geographic range."""
            raise ValueError("lat must be between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, v):
        if v is not None and not (-180 <= v <= 180):
            """Validates that longitude is within the valid geographic range."""
            raise ValueError("lon must be between -180 and 180")
        return v


@app.post("/api/v1/admin/stops", status_code=201)
async def add_stop(
    req:     StopCreateRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Creates a bus stop and invalidates the stop cache."""
    from sqlalchemy import func
    result = await db.execute(
        select(
            func.max(BusStop.morning_order_index),
            func.max(BusStop.evening_order_index)
        ).where(BusStop.route_id == req.route_id)
    )
    max_morning, max_evening = result.first()
    
    next_morning = (max_morning + 1) if max_morning is not None else 0
    next_evening = (max_evening + 1) if max_evening is not None else 0

    stop = BusStop(
        **req.model_dump(),
        order_index=next_morning,  # fallback
        morning_order_index=next_morning,
        evening_order_index=next_evening
    )
    db.add(stop)
    await db.commit()
    await db.refresh(stop)
    redis = await get_redis()
    try:
        await invalidate_stops_cache(redis)
    except Exception as e:
        logger.error("[ADMIN] Failed to invalidate stops cache: %s", e)

    await record_audit_event(
        db=db,
        request=request,
        action="STOP_CREATED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/stops",
        changes={"stop_id": stop.id, "name": stop.name, "route_id": stop.route_id}
    )

    return {"success": True, "id": stop.id}


class StopUpdateRequest(BaseModel):
    name:         Optional[str]   = None
    lat:          Optional[float] = None
    lon:          Optional[float] = None
    morning_time: Optional[str]   = None
    evening_time: Optional[str]   = None

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v):
        if v is not None and not (-90 <= v <= 90):
            """Validates that latitude is within the valid geographic range."""
            raise ValueError("lat must be between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, v):
        if v is not None and not (-180 <= v <= 180):
            """Validates that longitude is within the valid geographic range."""
            raise ValueError("lon must be between -180 and 180")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is not None and not v.strip():
            """Validates that the stop name is not empty."""
            raise ValueError("name cannot be empty")
        return v


@app.put("/api/v1/admin/stops/{stop_id}")
async def edit_stop(
    stop_id: int,
    req:     StopUpdateRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Updates an existing bus stop and invalidates the stop cache."""
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
    try:
        await invalidate_stops_cache(redis)
    except Exception as e:
        logger.error("[ADMIN] Failed to invalidate stops cache: %s", e)

    await record_audit_event(
        db=db,
        request=request,
        action="STOP_EDITED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint=f"/api/v1/admin/stops/{stop_id}",
        changes={"stop_id": stop_id, **req.model_dump(exclude_unset=True)}
    )

    return {"success": True}


@app.delete("/api/v1/admin/stops/{stop_id}")
async def delete_stop(
    stop_id: int,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Deletes a bus stop when its route has no active or scheduled trips."""
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

    route_id = stop.route_id
    m_idx = stop.morning_order_index
    e_idx = stop.evening_order_index
    
    await db.delete(stop)
    
    # Close the gaps in the sequence
    if m_idx is not None:
        await db.execute(
            update(BusStop)
            .where(BusStop.route_id == route_id, BusStop.morning_order_index > m_idx)
            .values(morning_order_index=BusStop.morning_order_index - 1)
        )
    if e_idx is not None:
        await db.execute(
            update(BusStop)
            .where(BusStop.route_id == route_id, BusStop.evening_order_index > e_idx)
            .values(evening_order_index=BusStop.evening_order_index - 1)
        )
        
    await db.commit()
    redis = await get_redis()
    try:
        await invalidate_stops_cache(redis)
    except Exception as e:
        logger.error("[ADMIN] Failed to invalidate stops cache: %s", e)

    await record_audit_event(
        db=db,
        request=request,
        action="STOP_DELETED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint=f"/api/v1/admin/stops/{stop_id}",
        changes={"stop_id": stop_id, "stop_name": stop.name, "route_id": stop.route_id}
    )

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
    auth_data: dict = Depends(require_admin),
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
    try:
        await invalidate_stops_cache(redis)
    except Exception as e:
        logger.error("[ADMIN] Failed to invalidate stops cache: %s", e)

    await record_audit_event(
        db=db,
        request=request,
        action="STOP_ROLE_CHANGED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint=f"/api/v1/admin/stops/{stop_id}/role",
        changes={"stop_id": stop_id, "stop_name": stop.name, "new_role": req.role}
    )

    return {"success": True, "role": req.role, "stop_id": stop_id}



class StopReorderItem(BaseModel):
    id: int
    morning_order_index: int
    evening_order_index: int


@app.post("/api/v1/admin/stops/reorder")
async def reorder_stops(
    req:     List[StopReorderItem],
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Reorders bus stops within a single route and invalidates the stop cache."""
    if len(req) > 200:
        raise HTTPException(status_code=400, detail="Cannot reorder more than 200 stops at once")
        
    if not req:
        return {"success": True}
        
    stop_ids = [item.id for item in req]
    result = await db.execute(select(BusStop).where(BusStop.id.in_(stop_ids)))
    stops = result.scalars().all()
    
    if len(stops) != len(req):
        raise HTTPException(status_code=400, detail="One or more stops not found")
        
    route_ids = {s.route_id for s in stops}
    if len(route_ids) != 1:
        raise HTTPException(status_code=400, detail="Cannot reorder stops across multiple routes")
        
    morning_indexes = set()
    evening_indexes = set()
    for item in req:
        if item.morning_order_index < 0 or item.evening_order_index < 0:
            raise HTTPException(status_code=400, detail="order index cannot be negative")
        if item.morning_order_index in morning_indexes:
            raise HTTPException(status_code=400, detail="Duplicate morning_order_index found")
        if item.evening_order_index in evening_indexes:
            raise HTTPException(status_code=400, detail="Duplicate evening_order_index found")
        morning_indexes.add(item.morning_order_index)
        evening_indexes.add(item.evening_order_index)

    for item in req:
        await db.execute(
            update(BusStop).where(BusStop.id == item.id).values(
                morning_order_index=item.morning_order_index,
                evening_order_index=item.evening_order_index,
            )
        )
    await db.commit()
    redis = await get_redis()
    try:
        await invalidate_stops_cache(redis)
    except Exception as e:
        logger.error("[ADMIN] Failed to invalidate stops cache: %s", e)

    await record_audit_event(
        db=db,
        request=request,
        action="STOPS_REORDERED",
        admin_username=auth_data.get("sub", "admin"),
        endpoint="/api/v1/admin/stops/reorder",
        changes={"reordered_count": len(req)}
    )

    return {"success": True}


#  GPS / Data 

@app.get("/api/v1/admin/gps/history")
async def gps_history(
    limit:     int = Query(500, le=500),
    offset:    int = 0,
    from_date: Optional[str] = None,
    to_date:   Optional[str] = None,
    db:        AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Returns filtered historical GPS data for the admin dashboard."""
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
        """Validates and sanitizes the broadcast title."""
        if len(v.strip()) == 0:
            raise ValueError("Title cannot be empty")
        if len(v) > 100:
            raise ValueError("Title must be 100 characters or fewer")
        return v.strip()

    @field_validator("body")
    @classmethod
    def body_length(cls, v: str) -> str:
        """Validates and sanitizes the broadcast body."""
        if len(v.strip()) == 0:
            raise ValueError("Body cannot be empty")
        if len(v) > 500:
            raise ValueError("Body must be 500 characters or fewer")
        return v.strip()


@app.post("/api/v1/admin/broadcast")
async def send_broadcast(
    req:     BroadcastRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Broadcasts a message to all users and records the event."""
    result = await _broadcast_to_all_users(db, req.title, req.body, {"type": "admin_broadcast"})
    await record_audit_event(
        db=db,
        request=request,
        action="BROADCAST_SENT",
        admin_username=auth_data.get("sub", "admin"),
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
    status: Optional[Literal["pending", "resolved", "rejected"]] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db:     AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Returns paginated user suggestions with optional status filtering."""
    stmt = select(Suggestion, User.email).outerjoin(User, Suggestion.user_id == User.id).order_by(desc(Suggestion.id))
    if status:
        stmt = stmt.where(Suggestion.status == status)
    result = await db.execute(stmt.limit(limit).offset(offset))
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
    """Defines an admin response and status for a student suggestion."""
    response: str = Field(..., max_length=2000)
    status:   Literal["pending", "resolved", "rejected"] = "resolved"


@app.post("/api/v1/admin/suggestions/{suggestion_id}/respond")
async def respond_to_suggestion(
    suggestion_id: int,
    req:   SuggestionResponseRequest,
    db:    AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Updates a student suggestion with an admin response and status."""
    result = await db.execute(select(Suggestion).where(Suggestion.id == suggestion_id))
    s      = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    s.admin_response = req.response[:2000]  # enforce max length
    s.status         = req.status
    
    # Notify the user who made the suggestion
    push_target = None
    if s.user_id:
        user = await db.scalar(select(User).where(User.id == s.user_id))
        if user:
            title = f"Suggestion {req.status.capitalize()}"
            body = f"Admin reply: \"{req.response}\""

            # Save in-app notification ALWAYS
            notif = InAppNotification(user_id=user.id, title=title, body=body, type="suggestion_update")
            db.add(notif)

            # Defer the push until after commit (see below)
            if user.notifications_on and user.device_token:
                push_target = (user.device_token, title, body)

    await db.commit()

    # Send the push AFTER the commit, and AWAIT it. The previous code used
    # asyncio.create_task(...) without holding a reference, so the task could be
    # garbage-collected before it ran (push silently lost) and its exceptions
    # were swallowed. Awaiting here guarantees it runs and errors are logged.
    if push_target:
        from libs.duk_common.firebase_app import send_multicast_chunked
        token, title, body = push_target
        try:
            await send_multicast_chunked([token], title, body, {"type": "suggestion_update"})
        except Exception as e:
            logger.error("[ADMIN] Suggestion-reply push failed: %s", e)

    return {"success": True}


@app.delete("/api/v1/admin/suggestions/{suggestion_id}")
async def delete_suggestion(
    suggestion_id: int,
    request: Request,
    db:    AsyncSession = Depends(get_db),
    auth_data: dict = Depends(require_admin),
):
    """Deletes a student suggestion and records the admin action."""
    result = await db.execute(select(Suggestion).where(Suggestion.id == suggestion_id))
    s      = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    await db.delete(s)
    await db.commit()
    
    await record_audit_event(
        db=db,
        request=request,
        action="SUGGESTION_DELETED",
        admin_username=auth_data.get("sub", "admin") if isinstance(auth_data, dict) else "admin",
        endpoint=f"/api/v1/admin/suggestions/{suggestion_id}",
        changes={"suggestion_id": suggestion_id}
    )
    return {"success": True}


#  Health 



@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    redis_status = "error"
    try:
        redis = await get_redis()
        if await redis.ping():
            redis_status = "ok"
    except Exception:
        pass

    status_code = 200 if db_status == "ok" and redis_status == "ok" else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if status_code == 200 else "degraded", "db": db_status, "redis": redis_status}
    )
