"""
routers/admin.py — Admin panel API endpoints.
Protected by X-Admin-Token header.
"""
import asyncio
import logging
import math
from datetime import date, datetime, timedelta, timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, update, func
from typing import List, Optional, Literal

from database import get_db
from models.route import Route, BusStop
from models.trip import Trip
from models.gps import GpsLog
from models.notification import AdminBroadcast, Suggestion, ScheduledNotification
from services.firebase import broadcast_to_all_users
from services.geofence import haversine_km
from services.trip_lifecycle import auto_complete_expired_trips
from routers.tracking import clear_stops_cache
from config import get_settings
from constants import IST_OFFSET, MORNING_END_MINS, EVENING_END_MINS

logger   = logging.getLogger(__name__)
settings = get_settings()
router   = APIRouter(prefix="/admin/api", tags=["admin"])


def require_admin(x_admin_token: Optional[str] = Header(None)):
    # Compare the header value against the token stored in .env
    # If they don't match → HTTP 403 Forbidden (admin only)
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin access required")


# ── Login (React dashboard uses this instead of a raw token prompt) ───────────
class LoginRequest(BaseModel):
    # Pydantic model — FastAPI auto-validates the JSON body against these fields
    username: str   # admin username (set in .env as ADMIN_USERNAME)
    password: str   # admin password (set in .env as ADMIN_PASSWORD)


@router.post("/login")
async def admin_login(req: LoginRequest):
    """
    Validates username + password from .env.
    Returns the ADMIN_TOKEN so the React app can use it as X-Admin-Token header.
    No database involved — credentials are env-level secrets.
    """
    # Both username and password must match exactly (case-sensitive)
    if req.username != settings.ADMIN_USERNAME or req.password != settings.ADMIN_PASSWORD:
        # 401 Unauthorized — wrong credentials
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Return the admin token; the React app stores it in memory (not localStorage for security)
    return {"token": settings.ADMIN_TOKEN}


# ── Trip Management ───────────────────────────────────────────────────────────
class TripStatusUpdate(BaseModel):
    trip_id:              int
    status:               Literal["active", "cancelled", "late", "stop_change", "scheduled"]
    late_by_minutes:      Optional[int] = None
    cancellation_reason:  Optional[str] = None
    reason:               Optional[str] = None  # general reason field for late / stop_change
    message:              Optional[str] = None  # freeform message for stop_change notification


@router.post("/trip/status")
async def update_trip_status(
    req:    TripStatusUpdate,
    db:     AsyncSession = Depends(get_db),
    _auth:  None = Depends(require_admin),
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

    # Broadcast trip status via WebSocket
    try:
        from routers.gps import manager
        await manager.broadcast({
            "type":      "trip_status",
            "trip_id":   trip.id,
            "status":    trip.status,
            "direction": trip.direction,
        })
    except Exception as e:
        logger.debug("[ADMIN] WebSocket broadcast skipped: %s", e)

    # Push notification to all users
    title, body = "", ""
    if req.status == "cancelled":
        title = "Bus Trip Cancelled"
        reason_text = req.cancellation_reason or req.reason or ''
        body  = f"Today's trip has been cancelled. {reason_text}".strip()
    elif req.status == "late":
        mins  = req.late_by_minutes or 0
        title = "Bus Running Late"
        reason_text = f" Reason: {req.reason}" if req.reason else ''
        body  = f"The bus will be approximately {mins} minutes late today.{reason_text}"
    elif req.status == "stop_change":
        title = "Bus Stop Change"
        body  = req.message or "There is a change to the bus stop schedule today."

    if title:
        result = await broadcast_to_all_users(db, title, body, {"trip_id": str(req.trip_id), "status": req.status})
        logger.info("[ADMIN] FCM broadcast result: %s", result)

    return {"success": True, "status": req.status}


@router.get("/trips")
async def list_trips(
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    await auto_complete_expired_trips(db)
    # Show past 10 days + future 7 days (so pre-cancelled upcoming trips appear)
    cutoff_past   = date.today() - timedelta(days=10)
    cutoff_future = date.today() + timedelta(days=365)
    result = await db.execute(
        select(Trip)
        .where(Trip.date.between(cutoff_past, cutoff_future))
        .order_by(desc(Trip.date), desc(Trip.id))
    )
    trips  = result.scalars().all()
    return [
        {
            "id":                  t.id,
            "date":                t.date.isoformat(),
            "direction":           t.direction,
            "status":              t.status,
            "late_by_minutes":     t.late_by_minutes,
            "cancellation_reason": t.cancellation_reason,
        }
        for t in trips
    ]


ensure_trips_lock = asyncio.Lock()

@router.post("/trips/today")
async def ensure_today_trips(
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    await auto_complete_expired_trips(db)
    """
    Auto-provision today's Morning and Evening trips if today is a weekday (Mon–Fri).
    Returns the list of today's trips (newly created or already existing).
    If today is Saturday or Sunday, returns an empty list without creating anything.
    """
    async with ensure_trips_lock:
        today = date.today()
        created = False

        # Ensure trips for today and the next 2 active weekdays (guarantees enough for 5 upcoming slots)
        days_ensured = 0
        i = 0
        while days_ensured < 3:
            target_date = today + timedelta(days=i)
            i += 1
            
            if target_date.weekday() >= 5: # Skip weekends
                continue
                
            days_ensured += 1

            existing_result = await db.execute(
                select(Trip).where(and_(Trip.date == target_date, Trip.route_id == 1))
            )
            existing_directions = {t.direction for t in existing_result.scalars().all()}

            for direction in ["forward", "reverse"]:
                if direction not in existing_directions:
                    trip = Trip(route_id=1, date=target_date, direction=direction, status="scheduled")
                    db.add(trip)
                    created = True

        if created:
            await db.commit()

    # Return all of today's trips
    all_today = await db.execute(
        select(Trip).where(and_(Trip.date == today, Trip.route_id == 1)).order_by(Trip.id)
    )
    today_trips = all_today.scalars().all()
    return {
        "created": created,
        "weekend": False,
        "trips": [
            {"id": t.id, "direction": t.direction, "status": t.status}
            for t in today_trips
        ]
    }


@router.post("/trip/create")
async def create_trip(
    route_id:      int,
    direction:     str,
    trip_date:     Optional[str] = None,
    create_return: bool = False,
    db:            AsyncSession = Depends(get_db),
    _auth:         None = Depends(require_admin),
):
    # Parse date or default to today
    d = date.fromisoformat(trip_date) if trip_date else date.today()

    # ── Guard: block past dates or expired time windows on today ─────────────
    now_ist = datetime.now(timezone.utc) + IST_OFFSET
    today_ist = now_ist.date()
    now_mins = now_ist.hour * 60 + now_ist.minute

    if d < today_ist:
        raise HTTPException(
            status_code=400,
            detail="Cannot create a special service trip for a past date.",
        )

    if d == today_ist:
        if direction == "forward" and now_mins >= MORNING_END_MINS:
            raise HTTPException(
                status_code=400,
                detail="Cannot create a Morning trip for today — the morning window (07:00–11:00 AM) has already passed.",
            )
        if direction == "reverse" and now_mins >= EVENING_END_MINS:
            raise HTTPException(
                status_code=400,
                detail="Cannot create an Evening trip for today — the evening window (05:30–08:30 PM) has already passed.",
            )

    # ── Guard: block if a trip already exists for this date+direction ─────────
    existing = (await db.execute(
        select(Trip).where(Trip.date == d, Trip.direction == direction)
    )).scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A trip already exists for {d} ({direction}) with status '{existing.status}'. "
                   "Use Pre-Cancel or Restore to manage it.",
        )

    # Create the primary trip
    trip = Trip(route_id=route_id, date=d, direction=direction, status="scheduled")
    db.add(trip)

    # Optionally create the return evening trip (only if it doesn't already exist)
    if create_return and direction == "forward":
        existing_return = (await db.execute(
            select(Trip).where(Trip.date == d, Trip.direction == "reverse")
        )).scalar_one_or_none()
        if not existing_return:
            db.add(Trip(route_id=route_id, date=d, direction="reverse", status="scheduled"))

    await db.commit()
    return {"success": True}



async def _void_trip_notifications(db: AsyncSession, trip_id: int):
    """Internal helper to cancel pending notifications when a trip is restored."""
    notif_result = await db.execute(
        select(ScheduledNotification).where(
            and_(ScheduledNotification.trip_id == trip_id,
                 ScheduledNotification.sent == False,
                 ScheduledNotification.cancelled == False)
        )
    )
    for n in notif_result.scalars().all():
        n.cancelled = True

class AdvanceCancelRequest(BaseModel):
    trip_date:          str
    direction:          str             # 'forward' | 'reverse'
    also_cancel_return: bool = False    # if morning, also cancel evening
    reason:             Optional[str] = None


@router.post("/trip/cancel-advance")
async def cancel_advance_trip(
    req:   AdvanceCancelRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """
    Pre-cancel trips for a specific date + direction.
    Creates the trip record if it doesn't exist yet so it always appears in the log.
    """
    d = date.fromisoformat(req.trip_date)
    reason = req.reason or "Pre-cancelled by admin"

    directions = [req.direction]
    if req.also_cancel_return and req.direction == "forward":
        directions.append("reverse")

    cancelled_ids = []
    for direction in directions:
        result = await db.execute(
            select(Trip).where(
                and_(Trip.date == d, Trip.direction == direction, Trip.route_id == 1)
            )
        )
        trip = result.scalar_one_or_none()
        if trip:
            if trip.status == "completed":
                raise HTTPException(status_code=400, detail="Cannot cancel a trip that has already been completed.")
            trip.status              = "cancelled"
            trip.cancellation_reason = reason
        else:
            # Create the trip record now so it shows in the log on that date
            trip = Trip(
                route_id=1, date=d, direction=direction,
                status="cancelled", cancellation_reason=reason,
            )
            db.add(trip)
        await db.flush()
        cancelled_ids.append(trip.id)

    await db.commit()

    # ── Immediate push notification ────────────────────────────────────────────
    dir_label  = "Morning" if req.direction == "forward" else "Evening"
    also_label = " and Evening" if req.also_cancel_return and req.direction == "forward" else ""
    date_label = d.strftime("%d %B")
    title = "Bus Service Cancelled"
    body  = (f"The {dir_label}{also_label} bus on {date_label} has been cancelled."
             f"{(' ' + req.reason) if req.reason else ''}")
    await broadcast_to_all_users(db, title, body, {"type": "advance_cancel", "date": req.trip_date})

    # ── Schedule deferred reminder notifications ───────────────────────────────
    # IST offsets: morning trip notifies at 06:00 IST, evening at 15:00 IST
    # We notify: (a) day before the trip, (b) on the day of the trip itself

    def _reminder_time(target_date: date, direction: str, day_offset: int) -> datetime:
        """Return the IST notification time as a naive UTC datetime."""
        hour = 6 if direction == "forward" else 15
        ist_dt = datetime.combine(target_date + timedelta(days=day_offset),
                                  datetime.min.time().replace(hour=hour))
        return ist_dt  # stored as IST naive; scheduler compares against IST now

    for trip_id, direction in zip(cancelled_ids,
                                   [req.direction] + (["reverse"] if req.also_cancel_return and req.direction == "forward" else [])):
        dir_l = "Morning" if direction == "forward" else "Evening"
        reminder_body = (f"Reminder: The {dir_l} bus on {date_label} is cancelled."
                         f"{(' ' + req.reason) if req.reason else ''}")

        # Only schedule future reminders (don't schedule for past dates)
        now_date = date.today()
        for day_offset in [-1, 0]:   # -1 = day before, 0 = day of
            remind_date = d + timedelta(days=day_offset)
            if remind_date >= now_date:
                send_dt = _reminder_time(remind_date, direction, 0)
                # Skip if send_at is already in the past today
                now_ist = datetime.now(timezone.utc).replace(tzinfo=None) + IST_OFFSET
                if send_dt > now_ist:
                    db.add(ScheduledNotification(
                        trip_id=trip_id,
                        title=title,
                        body=reminder_body,
                        send_at=send_dt,
                    ))

    await db.commit()
    return {"success": True, "cancelled_ids": cancelled_ids}


class RevokeCancelRequest(BaseModel):
    trip_id: int
    reason:  Optional[str] = None


@router.post("/trip/revoke-cancel")
async def revoke_cancel(
    req:   RevokeCancelRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """
    Restore a cancelled trip back to 'scheduled' and notify users.
    """
    result = await db.execute(select(Trip).where(Trip.id == req.trip_id))
    trip   = result.scalar_one_or_none()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    if trip.status != "cancelled":
        raise HTTPException(status_code=400, detail="Trip is not cancelled")

    dir_label  = "Morning" if trip.direction == "forward" else "Evening"
    date_label = trip.date.strftime("%d %B")
    if trip.date > date.today():
        await db.delete(trip)
        trip_deleted = True
    else:
        trip.status              = "scheduled"
        trip.cancellation_reason = None
        trip_deleted = False
    await db.commit()

    # Cancel any pending scheduled notifications for this trip
    await _void_trip_notifications(db, req.trip_id)
    await db.commit()

    # Immediate push notification
    title = "Bus Service Restored"
    body  = (f"The {dir_label} bus on {date_label} is back on schedule."
             f"{(' ' + req.reason) if req.reason else ''}")
    await broadcast_to_all_users(db, title, body, {"type": "revoke_cancel", "trip_id": str(req.trip_id)})

    # Schedule a day-of reminder so users are reminded the bus IS running
    if not trip_deleted:
        now_ist = datetime.now(timezone.utc).replace(tzinfo=None) + IST_OFFSET
        hour = 6 if trip.direction == "forward" else 15
        day_of_remind = datetime.combine(trip.date, datetime.min.time().replace(hour=hour))
        if day_of_remind > now_ist:
            db.add(ScheduledNotification(
                trip_id=trip.id,
                title="Bus Running Today",
                body=f"Reminder: The {dir_label} bus on {date_label} is running as scheduled.",
                send_at=day_of_remind,
            ))
            await db.commit()

    return {"success": True}


# ── Range Revoke ───────────────────────────────────────────────────────────────
class RangeRevokeRequest(BaseModel):
    trip_ids: List[int]          # list of trip IDs to restore
    reason:   Optional[str] = None


@router.post("/trip/revoke-cancel-range")
async def revoke_cancel_range(
    req:   RangeRevokeRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """
    Restore multiple cancelled trips at once. Pending scheduled notifications
    for each trip are voided. A day-of reminder is scheduled for future trips.
    Sends a single consolidated push notification to all users.
    """
    restored, skipped = [], []
    now_ist = datetime.now(timezone.utc).replace(tzinfo=None) + IST_OFFSET

    for trip_id in req.trip_ids:
        result = await db.execute(select(Trip).where(Trip.id == trip_id))
        trip   = result.scalar_one_or_none()
        if not trip or trip.status != "cancelled":
            skipped.append(trip_id)
            continue

        if trip.date > date.today():
            await db.delete(trip)
            trip_deleted = True
        else:
            trip.status              = "scheduled"
            trip.cancellation_reason = None
            trip_deleted = False

        if not trip_deleted:
            # Void pending scheduled notifications
            await _void_trip_notifications(db, trip_id)

            # Schedule day-of reminder if trip is still in the future
            hour = 6 if trip.direction == "forward" else 15
            day_of = datetime.combine(trip.date, datetime.min.time().replace(hour=hour))
            if day_of > now_ist:
                dir_l = "Morning" if trip.direction == "forward" else "Evening"
                db.add(ScheduledNotification(
                    trip_id=trip.id,
                    title="Bus Running Today",
                    body=(f"Reminder: The {dir_l} bus on {trip.date.strftime('%d %B')} "
                          f"is running as scheduled."),
                    send_at=day_of,
                ))
        restored.append(trip_id)

    await db.commit()

    if restored:
        await broadcast_to_all_users(
            db,
            "Bus Service Restored",
            (f"{len(restored)} cancelled trip(s) have been restored."
             f"{(' ' + req.reason) if req.reason else ''}"),
            {"type": "revoke_cancel_range"},
        )

    return {"success": True, "restored": restored, "skipped": skipped}


# ── Route History (Admin) ──────────────────────────────────────────────────────
@router.get("/route-history")
async def get_route_history(
    from_date: str = Query(..., description="Start date YYYY-MM-DD"),
    to_date:   str = Query(..., description="End date YYYY-MM-DD"),
    db:        AsyncSession = Depends(get_db),
    _auth:     None = Depends(require_admin),
):
    """
    Returns GPS trails for all completed trips in a date range.
    Each session has route_points (lat/lon/time trail) and stop_crossings.
    Also returns the full list of dates that have completed trips so the
    frontend calendar can restrict selection to those dates only.
    """
    try:
        d_from = datetime.strptime(from_date, "%Y-%m-%d").date()
        d_to   = datetime.strptime(to_date,   "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    if (d_to - d_from).days > 30:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 31 days")

    # Fetch all stops for crossing detection
    stops_res = await db.execute(
        select(BusStop).order_by(BusStop.route_id, BusStop.order_index)
    )
    all_stops = [{"id": s.id, "name": s.name, "lat": s.lat, "lon": s.lon}
                 for s in stops_res.scalars().all()]

    def find_nearest_stop(lat, lon, threshold_km=0.3):
        best, best_d = None, float("inf")
        for s in all_stops:
            d = haversine_km(lat, lon, s["lat"], s["lon"])
            if d < best_d:
                best_d, best = d, s
        return best if best_d <= threshold_km else None

    # Fetch completed trips in range
    # Find unique dates that have GPS logs in the range
    dates_res = await db.execute(
        select(func.date(GpsLog.server_time)).where(
            func.date(GpsLog.server_time).between(d_from, d_to),
            GpsLog.lat.isnot(None)
        ).distinct().order_by(func.date(GpsLog.server_time).desc())
    )
    valid_dates = dates_res.scalars().all()

    sessions = []
    for d in valid_dates:
        # Force the query bounds to be UTC aware. 
        # Since the database natively stores IST time but tags it as UTC (+00),
        # querying with a UTC-aware datetime prevents the database driver from shifting 
        # our query boundary backwards by 5.5 hours!
        day_start = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
        day_end   = day_start + timedelta(days=1)

        logs_res = await db.execute(
            select(GpsLog).where(
                GpsLog.lat.isnot(None),
                GpsLog.server_time >= day_start,
                GpsLog.server_time <  day_end,
            ).order_by(GpsLog.id)
        )
        logs = logs_res.scalars().all()

        route_points   = []
        stop_crossings = []
        last_crossed   = None
        last_lat = last_lon = None

        for log in logs:
            if last_lat is not None:
                if haversine_km(last_lat, last_lon, log.lat, log.lon) < 0.02:
                    continue  # skip stationary drift < 20 m
            
            # The server_time stored in the DB is natively IST, but marked as UTC by PG.
            # Strip the timezone info so we can format it directly as Indian time.
            ist_time = log.server_time.replace(tzinfo=None)

            route_points.append({
                "lat":  log.lat,
                "lon":  log.lon,
                "time": ist_time.strftime("%H:%M"),
            })
            nearest = find_nearest_stop(log.lat, log.lon)
            if nearest and nearest["name"] != last_crossed:
                last_crossed = nearest["name"]
                stop_crossings.append({
                    "stop_name":  nearest["name"],
                    "crossed_at": ist_time.strftime("%I:%M %p"),
                    "lat": nearest["lat"],
                    "lon": nearest["lon"],
                })
            last_lat, last_lon = log.lat, log.lon

        # Apply smart map matching (snaps on-road points, keeps off-road raw)
        # This fixes the "spiderweb" effect caused by raw GPS hardware inaccuracies
        route_points = await _apply_map_matching(route_points)

        sessions.append({
            "trip_id":        f"full-day-{d.isoformat()}",
            "trip_date":      d.isoformat(),
            "direction":      "full_day",
            "status":         "completed",
            "route_points":   route_points,
            "stop_crossings": stop_crossings,
        })

    # Return dates that have GPS logs so the UI calendar can restrict selection
    all_completed_res = await db.execute(
        select(func.date(GpsLog.server_time)).where(
            func.date(GpsLog.server_time) <= date.today(),
            GpsLog.lat.isnot(None)
        ).distinct()
    )
    completed_dates = [d.isoformat() for d in all_completed_res.scalars().all() if d is not None]

    return {"sessions": sessions, "completed_dates": completed_dates}


# ── Smart map matching helper ──────────────────────────────────────────────

async def _apply_map_matching(
    raw_points: list[dict],
) -> list[dict]:
    """
    Applies high-fidelity map matching by requesting OSRM routes between consecutive GPS points.
    This injects the exact physical road curvature, preventing straight lines from cutting through buildings.
    """
    if len(raw_points) < 2:
        return raw_points

    osrm_url = "http://localhost:5001/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?geometries=geojson&overview=full"
    
    # Limit concurrency to prevent socket exhaustion (Too many open files)
    sem = asyncio.Semaphore(100)
    
    async with httpx.AsyncClient() as client:
        async def fetch_route(pt1, pt2, idx):
            async with sem:
                # Skip OSRM for tiny micro-movements to save overhead
                dist_m = haversine_km(pt1["lat"], pt1["lon"], pt2["lat"], pt2["lon"]) * 1000
                if dist_m < 5.0:
                    return idx, [pt2]
                    
                try:
                    url = osrm_url.format(lon1=pt1["lon"], lat1=pt1["lat"], lon2=pt2["lon"], lat2=pt2["lat"])
                    resp = await client.get(url, timeout=3.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        
                        # Smart Fallback: Check if the bus actually went deep off-road (like inside the college campus)
                        wps = data.get("waypoints", [])
                        if len(wps) == 2:
                            if wps[0].get("distance", 0) > 15.0 or wps[1].get("distance", 0) > 15.0:
                                # The bus is far away from the main mapped road. Use raw points!
                                return idx, [pt2]

                        if data.get("routes") and data["routes"][0].get("geometry"):
                            coords = data["routes"][0]["geometry"]["coordinates"]
                            new_pts = []
                            for lon, lat in coords:
                                new_pts.append({
                                    "lat": lat,
                                    "lon": lon,
                                    "time": pt2["time"],
                                    "on_road": True
                                })
                            return idx, new_pts
                except Exception:
                    pass
                # Fallback to the raw point if OSRM fails or bus is wildly off-road
                return idx, [pt2]

        tasks = []
        for i in range(len(raw_points) - 1):
            tasks.append(fetch_route(raw_points[i], raw_points[i+1], i))
            
        results = await asyncio.gather(*tasks)

    # Reconstruct the array in original order
    results.sort(key=lambda x: x[0])
    
    matched_points = [raw_points[0]]
    for idx, pts in results:
        matched_points.extend(pts)
        
    return matched_points


# ── Stop Management ───────────────────────────────────────────────────────────

class StopCreate(BaseModel):
    route_id:    int
    name:        str
    lat:         float
    lon:         float
    order_index: int


class StopUpdate(BaseModel):
    name:        Optional[str]   = None
    lat:         Optional[float] = None
    lon:         Optional[float] = None
    order_index: Optional[int]   = None


class StopRoleRequest(BaseModel):
    role: str  # morning_origin | morning_destination | evening_origin | evening_destination | clear


@router.get("/stops")
async def list_stops(db: AsyncSession = Depends(get_db), _auth: None = Depends(require_admin)):
    result = await db.execute(
        select(BusStop).order_by(BusStop.route_id, BusStop.order_index)
    )
    stops = result.scalars().all()
    return [
        {
            "id":                    s.id,
            "route_id":              s.route_id,
            "name":                  s.name,
            "lat":                   s.lat,
            "lon":                   s.lon,
            "order_index":           s.order_index,
            "is_morning_origin":      bool(s.is_morning_origin),
            "is_morning_destination": bool(s.is_morning_destination),
            "is_evening_origin":      bool(s.is_evening_origin),
            "is_evening_destination": bool(s.is_evening_destination),
        }
        for s in stops
    ]


@router.post("/stops")
async def create_stop(
    req:   StopCreate,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    # Auto-shift existing stops to make room (offset hack to avoid unique constraint)
    await db.execute(
        update(BusStop)
        .where(BusStop.route_id == req.route_id)
        .where(BusStop.order_index >= req.order_index)
        .values(order_index=BusStop.order_index + 1000)
    )
    await db.execute(
        update(BusStop)
        .where(BusStop.route_id == req.route_id)
        .where(BusStop.order_index >= req.order_index + 1000)
        .values(order_index=BusStop.order_index - 999)
    )

    stop = BusStop(**req.model_dump())
    db.add(stop)
    await db.commit()
    await db.refresh(stop)
    
    clear_stops_cache()
    
    return {"id": stop.id, "name": stop.name}


@router.put("/stops/{stop_id}")
async def update_stop(
    stop_id: int,
    req:     StopUpdate,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop   = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")
        
    req_data = req.model_dump(exclude_none=True)
    
    if "order_index" in req_data and req_data["order_index"] != stop.order_index:
        new_order = req_data["order_index"]
        await db.execute(
            update(BusStop)
            .where(BusStop.route_id == stop.route_id)
            .where(BusStop.id != stop.id)
            .where(BusStop.order_index >= new_order)
            .values(order_index=BusStop.order_index + 1000)
        )
        await db.execute(
            update(BusStop)
            .where(BusStop.route_id == stop.route_id)
            .where(BusStop.id != stop.id)
            .where(BusStop.order_index >= new_order + 1000)
            .values(order_index=BusStop.order_index - 999)
        )

    for field, val in req_data.items():
        setattr(stop, field, val)
    await db.commit()
    
    clear_stops_cache()
    
    return {"success": True}


@router.delete("/stops/{stop_id}")
async def delete_stop(
    stop_id: int,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop   = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")
        
    route_id = stop.route_id
    deleted_order = stop.order_index
    
    await db.delete(stop)
    await db.flush() # Force deletion to release the order_index immediately
    
    # Reverse auto-shift to fill the gap (offset hack)
    await db.execute(
        update(BusStop)
        .where(BusStop.route_id == route_id)
        .where(BusStop.order_index > deleted_order)
        .values(order_index=BusStop.order_index + 1000)
    )
    await db.execute(
        update(BusStop)
        .where(BusStop.route_id == route_id)
        .where(BusStop.order_index > deleted_order + 1000)
        .values(order_index=BusStop.order_index - 1001)
    )
    
    await db.commit()
    
    clear_stops_cache()
    
    return {"success": True}


@router.put("/stops/{stop_id}/role")
async def set_stop_role(
    stop_id: int,
    req:     StopRoleRequest,
    db:      AsyncSession = Depends(get_db),
    _auth:   None = Depends(require_admin),
):
    """
    Assign a terminal role to a specific stop.
    Roles: morning_origin | morning_destination | evening_origin | evening_destination
    Setting a role automatically clears it from whichever stop previously held it.
    """
    role_col_map = {
        "morning_origin":      "is_morning_origin",
        "morning_destination": "is_morning_destination",
        "evening_origin":      "is_evening_origin",
        "evening_destination": "is_evening_destination",
    }
    col_name = role_col_map.get(req.role)
    if not col_name:
        raise HTTPException(status_code=400, detail=f"Invalid role '{req.role}'. Must be one of: {list(role_col_map.keys())}")

    # Verify the target stop exists
    result = await db.execute(select(BusStop).where(BusStop.id == stop_id))
    stop = result.scalar_one_or_none()
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")

    # Step 1: Clear this role from ALL stops (ensures only one stop holds each role)
    await db.execute(update(BusStop).values({col_name: False}))

    # Step 2: Assign the role exclusively to the selected stop
    await db.execute(update(BusStop).where(BusStop.id == stop_id).values({col_name: True}))
    await db.commit()

    clear_stops_cache()
    logger.info("[ADMIN] Stop #%d assigned role '%s'", stop_id, req.role)
    return {"success": True, "stop_id": stop_id, "role": req.role, "stop_name": stop.name}


# ── Route Management ──────────────────────────────────────────────────────────
@router.get("/routes")
async def list_routes(db: AsyncSession = Depends(get_db), _auth: None = Depends(require_admin)):
    result = await db.execute(select(Route))
    routes = result.scalars().all()
    return [{"id": r.id, "name": r.name, "description": r.description} for r in routes]


@router.post("/routes")
async def create_route(
    name:        str,
    description: str = "",
    db:          AsyncSession = Depends(get_db),
    _auth:       None = Depends(require_admin),
):
    route = Route(name=name, description=description)
    db.add(route)
    await db.commit()
    await db.refresh(route)
    
    clear_stops_cache()
    
    return {"id": route.id, "name": route.name}


# ── Broadcast Notifications ───────────────────────────────────────────────────
class BroadcastRequest(BaseModel):
    title:  str
    body:   str
    target: str = "all"


@router.post("/broadcast")
async def send_broadcast(
    req:   BroadcastRequest,
    db:    AsyncSession = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    result = await broadcast_to_all_users(db, req.title, req.body)

    log = AdminBroadcast(title=req.title, body=req.body, target=req.target, sent_count=result.get("sent", 0))
    db.add(log)
    await db.commit()
    return {"success": True, **result}


# ── Suggestions ───────────────────────────────────────────────────────────────
@router.get("/suggestions")
async def list_suggestions(db: AsyncSession = Depends(get_db), _auth: None = Depends(require_admin)):
    from models.user import User
    result = await db.execute(
        select(Suggestion, User.name, User.email)
        .outerjoin(User, Suggestion.user_id == User.id)
        .order_by(desc(Suggestion.id))
        .limit(100)
    )
    items = result.all()
    return [{
        "id": s.Suggestion.id, 
        "created_at": str(s.Suggestion.created_at), 
        "trip": s.Suggestion.trip, 
        "location": s.Suggestion.location, 
        "suggestion": s.Suggestion.suggestion,
        "status": s.Suggestion.status,
        "admin_response": s.Suggestion.admin_response,
        "user_name": s.name,
        "user_email": s.email
    } for s in items]

class SuggestionUpdate(BaseModel):
    status: str
    admin_response: Optional[str] = None

@router.patch("/suggestions/{sid}")
async def update_suggestion(
    sid: int, 
    req: SuggestionUpdate, 
    db: AsyncSession = Depends(get_db), 
    _auth: None = Depends(require_admin)
):
    from models.user import User
    from services.firebase import send_push_notification

    result = await db.execute(
        select(Suggestion, User)
        .outerjoin(User, Suggestion.user_id == User.id)
        .where(Suggestion.id == sid)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found")
        
    s, user = row
    
    # Update DB fields
    s.status = req.status
    if req.admin_response is not None:
        s.admin_response = req.admin_response
        
    await db.commit()

    # Send push notification if a response is given and status is final-ish
    if user and user.device_token and user.notifications_on and s.admin_response:
        title = "Response to your suggestion"
        body = f"Admin ({req.status}): {s.admin_response[:100]}..." if len(s.admin_response) > 100 else f"Admin ({req.status}): {s.admin_response}"
        await send_push_notification([user.device_token], title, body)
        
    return {"success": True}


@router.delete("/suggestions/{sid}")
async def delete_suggestion(sid: int, db: AsyncSession = Depends(get_db), _auth: None = Depends(require_admin)):
    result = await db.execute(select(Suggestion).where(Suggestion.id == sid))
    s      = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(s)
    await db.commit()
    return {"success": True}
