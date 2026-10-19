# services/notification-api/main.py
"""
Notification API — Manages in-app notifications and user notification preferences.

This service handles the passenger-facing notification layer:
  - Saves FCM notification preferences (which stops to alert on, FCM token)
  - Retrieves in-app notification history for the logged-in user
  - Marks notifications as read
  - Retrieves admin broadcast messages
  - Receives student suggestions from the PWA

Note: Actual push notification SENDING is done by the notification-service
(which listens to Redis events from trip-lifecycle). This API only handles
the CRUD side (preferences, history, suggestions).

Endpoints:
  POST   /api/v1/notifications/preferences   — Save FCM token + stop preferences
  GET    /api/v1/notifications               — Get notification history
  PUT    /api/v1/notifications/{id}/read     — Mark notification as read
  GET    /api/v1/notifications/broadcasts    — Get admin broadcast messages
  POST   /api/v1/suggestion                 — Submit a suggestion
  GET    /health                             — Health check
"""
import os
import sys
import uuid
from typing import Optional
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.auth import make_get_current_user_id
from libs.duk_common.settings import get_settings

from database_local import get_db
from libs.duk_common.models import User, UserNotificationPreference, InAppNotification, Suggestion

from libs.duk_common.middleware import configure_app

app = FastAPI(title="notification-api", docs_url=None, redoc_url=None)
settings = get_settings()
configure_app(app, allowed_origins=settings.allowed_origins_list, environment=settings.ENVIRONMENT)

from libs.duk_common.redis_client import get_redis
from libs.duk_common.rate_limit_deps import make_rate_limit_dep

_auth = make_get_current_user_id(
    secret_key=settings.SECRET_KEY,
    redis_getter=get_redis,
    algorithm=settings.ALGORITHM,
)
get_current_user_id = _auth.get_user_id

notif_general_limit = make_rate_limit_dep("notif:general", max_requests=30, window_seconds=60)
suggestion_limit = make_rate_limit_dep("notif:suggestion", max_requests=3, window_seconds=3600)

class NotifPrefRequest(BaseModel):
    route_id: int
    direction: str
    boarding_stop_id: int
    destination_stop_id: int

@app.post("/api/v1/notifications/preferences")
async def save_preferences(req: NotifPrefRequest, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user_id), _limit: None = Depends(notif_general_limit)):
    """Creates or updates the authenticated user's notification preferences.
    
    This endpoint allows a user to specify their preferences for notifications, 
    including which route, direction, boarding stop, and destination stop they 
    are interested in. 
    
    Returns:
        dict: A dictionary with a status indicator.
    
    Raises:
        HTTPException: If the user is not authenticated or if the request is rate-limited.
    """
    stmt = select(UserNotificationPreference).where(UserNotificationPreference.user_id == uuid.UUID(user_id))
    result = await db.execute(stmt)
    pref = result.scalar_one_or_none()
    
    if not pref:
        pref = UserNotificationPreference(user_id=uuid.UUID(user_id))
        db.add(pref)
        
    pref.route_id = req.route_id
    pref.direction = req.direction
    pref.boarding_stop_id = req.boarding_stop_id
    pref.destination_stop_id = req.destination_stop_id
    pref.is_active = True
    
    await db.commit()
    return {"status": "ok"}

@app.get("/api/v1/notifications")
async def get_my_notifications(
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
    _limit: None = Depends(notif_general_limit)
):
    """Fetch all In-App Notifications for the current user.
    
    Fetches notifications for the authenticated user, including broadcast 
    notifications (sent to all users). Returns notifications from the last 
    7 days, ordered by creation time in descending order.
    
    Args:
        db: The database session.
        current_user_id: The ID of the currently authenticated user.
        _limit: Rate limit dependency.
        
    Returns:
        dict: A dictionary containing a list of notifications.
    
    Raises:
        HTTPException: If the user is not authenticated or if the request is rate-limited.
    """
    user_uuid = uuid.UUID(current_user_id)
    
    user_result = await db.execute(select(User).where(User.id == user_uuid))
    user_obj = user_result.scalar_one_or_none()
    user_created_at = user_obj.created_at if user_obj and user_obj.created_at else datetime.min.replace(tzinfo=timezone.utc)
    
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    
    result = await db.execute(
        select(InAppNotification)
        .where(
            (InAppNotification.created_at >= seven_days_ago) &
            (
                (InAppNotification.user_id == user_uuid) |
                ((InAppNotification.user_id.is_(None)) & (InAppNotification.created_at >= user_created_at))
            )
        )
        .order_by(desc(InAppNotification.created_at))
        .limit(20)
    )
    in_app_notifs = result.scalars().all()
    
    redis = await get_redis()
    items = []
    
    for n in in_app_notifs:
        is_read = n.is_read
        if n.user_id is None:
            # Check Redis for broadcast read state
            cache_key = f"notif_read:{user_uuid}:{n.id}"
            if await redis.get(cache_key):
                is_read = True
                
        items.append({
            "id": n.id,
            "notification": {
                "title": n.title,
                "body": n.body
            },
            "type": n.type,
            "is_read": is_read,
            "time": n.created_at.isoformat() if n.created_at else None,
        })
        
    return {"notifications": items}

@app.put("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
    _limit: None = Depends(notif_general_limit)
):
    """Mark a notification as read.
    
    Args:
        notification_id: The ID of the notification to mark as read.
        db: The database session.
        current_user_id: The ID of the currently authenticated user.
        _limit: Rate limit dependency.
        
    Returns:
        dict: A dictionary with a success indicator.
    
    Raises:
        HTTPException: If the notification is not found or if the user is not authorized to mark it as read.
    """
    user_uuid = uuid.UUID(current_user_id)
    result = await db.execute(
        select(InAppNotification)
        .where(InAppNotification.id == notification_id)
    )
    notification = result.scalars().first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
        
    if notification.user_id is None:
        # Broadcast notification: save read state to Redis for 7 days
        redis = await get_redis()
        cache_key = f"notif_read:{user_uuid}:{notification.id}"
        await redis.set(cache_key, "1", ex=86400 * 7)
        return {"success": True}
        
    if notification.user_id != user_uuid:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    notification.is_read = True
    await db.commit()
    
    return {"success": True}


class SuggestionRequest(BaseModel):
    trip:       Optional[str] = None
    location:   Optional[str] = None
    suggestion: str


@app.post("/api/v1/suggestion", status_code=201)
async def submit_suggestion(
    req: SuggestionRequest,
    db:  AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
    _limit: None = Depends(suggestion_limit)
):
    """Submit a user suggestion. Authenticated, but trip/location are optional.
    
    Args:
        req: The suggestion request containing trip, location, and suggestion text.
        db: The database session.
        current_user_id: The ID of the currently authenticated user.
        _limit: Rate limit dependency.
        
    Returns:
        dict: A dictionary with the created suggestion.
    
    Raises:
        HTTPException: If the suggestion text is empty, if the user is not authenticated, or if the request is rate-limited.
    """
    import html
    text = html.escape(req.suggestion.strip())[:500] if req.suggestion else ""
    safe_trip = html.escape(req.trip.strip())[:100] if req.trip else None
    safe_location = html.escape(req.location.strip())[:200] if req.location else None

    if not text:
        raise HTTPException(status_code=400, detail="Suggestion text is required.")

    user_uuid = uuid.UUID(current_user_id) if current_user_id else None

    s = Suggestion(
        trip=safe_trip,
        location=safe_location,
        suggestion=text,
        user_id=user_uuid,
        status="pending",
    )
    db.add(s)
    await db.commit()
    return {"success": True, "message": "Suggestion submitted successfully."}




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
