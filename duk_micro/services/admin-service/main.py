# services/admin-service/main.py
"""
Admin Service — full port of backend/routers/admin.py.
Protected by X-Admin-Token header or admin login endpoint.
"""
import asyncio
import html as html_mod
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Literal

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func, update

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.settings import get_settings
from libs.duk_common.redis_client import get_redis
from libs.duk_common.events import TripStatusChangedEvent, EventBus
from libs.duk_common.cache.stops_cache import invalidate_stops_cache

from database_local import get_db
from models_local import (
    Trip, BusStop, Route, GpsLog, User, AdminBroadcast,
    Suggestion, ScheduledNotification, InAppNotification
)

logging.basicConfig(level=logging.INFO)
logger   = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="admin-service")

IST_OFFSET      = timedelta(hours=5, minutes=30)
MORNING_END_MINS = 660    # 11:00 AM IST
EVENING_END_MINS = 1230   # 20:30 IST


# ── Auth ───────────────────────────────────────────────────────────────────────

def require_admin(x_admin_token: Optional[str] = Header(None)):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin access required")


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/v1/admin/login")
async def admin_login(req: LoginRequest):
    if req.username != settings.ADMIN_USERNAME or req.password != settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"token": settings.ADMIN_TOKEN}


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _broadcast_to_all_users(db: AsyncSession, title: str, body: str, data: dict = None):
    """FCM push to all verified users with notifications on. Creates in-app record."""
    try:
        from firebase_admin import messaging
        import firebase_admin, json
        if not firebase_admin._apps:
            creds_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
            creds_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase_creds.json")
            from firebase_admin import credentials
            if creds_json:
                cred = credentials.Certificate(json.loads(creds_json))
            elif os.path.exists(creds_path):
                cred = credentials.Certificate(creds_path)
            else:
                logger.warning("[ADMIN] Firebase credentials not found")
                return {"sent_count": 0, "success": False}
            firebase_admin.initialize_app(cred)

        result = await db.execute(
            select(User).where(
                User.email_verified == True,
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

        sent_count = 0
        for i in range(0, len(tokens), 500):
            chunk = tokens[i:i+500]
            msg   = messaging.MulticastMessage(
                notification=messaging.Notification(title=title, body=body),
                data={k: str(v) for k, v in (data or {}).items()},
                tokens=chunk,
            )
            resp = messaging.send_each_for_multicast(msg)
            sent_count += resp.success_count

        db.add(AdminBroadcast(title=title, body=body, sent_count=sent_count, success=True))
        await db.commit()
        return {"sent_count": sent_count, "success": True}
    except Exception as e:
        logger.error("[ADMIN] FCM broadcast failed: %s", e)
        return {"sent_count": 0, "success": False}


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


# ── Trip Management ────────────────────────────────────────────────────────────

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
        body  = f"Today's trip has been cancelled. {req.cancellation_reason or req.reason or ''}".strip()
    elif req.status == "late":
        mins  = req.late_by_minutes or 0
        title = "Bus Running Late"
        body  = f"The bus will be approximately {mins} minutes late today."
        if req.reason:
            body += f" Reason: {req.reason}"
    elif req.status == "stop_change":
        title = "Bus Stop Change"
        body  = req.message or "There is a change to the bus stop schedule today."

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
    body       = f"The {dir_label} bus on {date_label} has been cancelled.{(' ' + req.reason) if req.reason else ''}"
    await _broadcast_to_all_users(db, title, body, {"type": "advance_cancel", "date": req.trip_date})

    # Schedule deferred reminders
    now_ist = (datetime.now(timezone.utc) + IST_OFFSET).replace(tzinfo=None)
    for trip_id, direction in zip(cancelled_ids, directions):
        dir_l         = "Morning" if direction == "forward" else "Evening"
        reminder_body = f"Reminder: The {dir_l} bus on {date_label} is cancelled.{(' ' + req.reason) if req.reason else ''}"
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
    body  = f"The {dir_label} bus on {date_label} is back on schedule.{(' ' + req.reason) if req.reason else ''}"
    await _broadcast_to_all_users(db, title, body, {"type": "revoke_cancel", "trip_id": str(req.trip_id)})
    return {"success": True}


# ── Stop Management ────────────────────────────────────────────────────────────

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


@app.put("/api/v1/admin/stops/{stop_id}")
async def edit_stop(
    stop_id: int,
    req:     dict,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop   = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")
    for key, value in req.items():
        if hasattr(stop, key):
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
    await db.delete(stop)
    await db.commit()
    redis = await get_redis()
    await invalidate_stops_cache(redis)
    return {"success": True}


@app.post("/api/v1/admin/stops/reorder")
async def reorder_stops(
    req:   List[dict],
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
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
    return {"success": True}


# ── GPS / Data ─────────────────────────────────────────────────────────────────

@app.get("/api/v1/admin/gps/history")
async def gps_history(
    limit:  int = Query(200, le=1000),
    offset: int = 0,
    db:     AsyncSession = Depends(get_db),
    _auth:  None = Depends(require_admin),
):
    result = await db.execute(
        select(GpsLog)
        .where(GpsLog.lat.isnot(None))
        .order_by(desc(GpsLog.id))
        .limit(limit)
        .offset(offset)
    )
    logs = result.scalars().all()
    return [
        {
            "id": l.id,
            "lat": l.lat, "lon": l.lon,
            "speed": l.speed,
            "event": l.event,
            "server_time": l.server_time.isoformat() if l.server_time else None,
            "ist_time": l.ist_time.isoformat() if l.ist_time else None,
        }
        for l in logs
    ]


# ── Broadcasts & Suggestions ───────────────────────────────────────────────────

class BroadcastRequest(BaseModel):
    title: str
    body:  str


@app.post("/api/v1/admin/broadcast")
async def send_broadcast(
    req:   BroadcastRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    result = await _broadcast_to_all_users(db, req.title, req.body, {"type": "admin_broadcast"})
    return result


@app.get("/api/v1/admin/suggestions")
async def list_suggestions(
    status: Optional[str] = None,
    db:     AsyncSession = Depends(get_db),
    _auth:  None = Depends(require_admin),
):
    stmt = select(Suggestion).order_by(desc(Suggestion.id))
    if status:
        stmt = stmt.where(Suggestion.status == status)
    result = await db.execute(stmt.limit(100))
    items  = result.scalars().all()
    return [
        {
            "id": s.id, "suggestion": s.suggestion, "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "admin_response": s.admin_response,
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
    s.admin_response = req.response
    s.status         = req.status
    await db.commit()
    return {"success": True}


# ── ML Retrain Trigger ─────────────────────────────────────────────────────────

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
        raise HTTPException(status_code=500, detail=f"Failed to signal retrain: {e}")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
