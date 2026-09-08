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
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.auth import make_get_current_user_id
from libs.duk_common.settings import get_settings

from database_local import get_db, AsyncSessionLocal
from models_local import UserNotificationPreference, InAppNotification, Suggestion, AdminBroadcast

from libs.duk_common.middleware import configure_app

app = FastAPI(title="notification-api")
settings = get_settings()
configure_app(app, allowed_origins=settings.allowed_origins_list)

get_current_user_id = make_get_current_user_id(settings.SECRET_KEY, settings.ALGORITHM)

class NotifPrefRequest(BaseModel):
    route_id: int
    direction: str
    boarding_stop_id: int
    destination_stop_id: int
    fcm_token: str

@app.post("/api/v1/notifications/preferences")
async def save_preferences(req: NotifPrefRequest, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    stmt = select(UserNotificationPreference).where(UserNotificationPreference.user_id == str(user_id))
    result = await db.execute(stmt)
    pref = result.scalar_one_or_none()
    
    if not pref:
        pref = UserNotificationPreference(user_id=str(user_id))
        db.add(pref)
        
    pref.route_id = req.route_id
    pref.direction = req.direction
    pref.boarding_stop_id = req.boarding_stop_id
    pref.destination_stop_id = req.destination_stop_id
    pref.fcm_token = req.fcm_token
    pref.is_active = True
    
    await db.commit()
    return {"status": "ok"}

@app.get("/api/v1/notifications")
async def get_my_notifications(
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetch all In-App Notifications for the current user."""
    user_uuid = uuid.UUID(current_user_id)
    
    # 1. Fetch In-App Notifications
    result = await db.execute(
        select(InAppNotification)
        .where((InAppNotification.user_id == user_uuid) | (InAppNotification.user_id.is_(None)))
        .order_by(desc(InAppNotification.created_at))
        .limit(100)
    )
    in_app_notifs = result.scalars().all()
    
    items = []
    seen_signatures = set()
    for n in in_app_notifs:
        seen_signatures.add((n.title, n.body))
        items.append({
            "id": n.id,
            "notification": {
                "title": n.title,
                "body": n.body
            },
            "type": n.type,
            "is_read": n.is_read,
            "time": n.created_at.isoformat() if n.created_at else None,
        })
        
    # 2. Fetch legacy Suggestion responses
    s_result = await db.execute(
        select(Suggestion)
        .where(Suggestion.user_id == user_uuid, Suggestion.admin_response.isnot(None))
        .order_by(desc(Suggestion.id))
        .limit(20)
    )
    suggestions = s_result.scalars().all()
    
    for s in suggestions:
        body_text = f"Admin ({s.status}): {s.admin_response[:100]}..." if len(s.admin_response) > 100 else f"Admin ({s.status}): {s.admin_response}"
        title_text = "Response to your suggestion"
        
        if (title_text, body_text) not in seen_signatures:
            seen_signatures.add((title_text, body_text))
            items.append({
                "id": f"sugg_{s.id}",
                "notification": {
                    "title": title_text,
                    "body": body_text
                },
                "type": "suggestion_response",
                "is_read": False,
                "time": s.created_at.isoformat() if s.created_at else None,
            })
            
    # 3. Fetch legacy AdminBroadcasts
    b_result = await db.execute(
        select(AdminBroadcast)
        .order_by(desc(AdminBroadcast.id))
        .limit(10)
    )
    broadcasts = b_result.scalars().all()
    
    for b in broadcasts:
        if (b.title, b.body) not in seen_signatures:
            seen_signatures.add((b.title, b.body))
            items.append({
                "id": f"bc_{b.id}",
                "notification": {
                    "title": b.title,
                    "body": b.body
                },
                "type": "broadcast",
                "is_read": False,
                "time": b.sent_at.isoformat() if b.sent_at else None,
            })
            
    items.sort(key=lambda x: x["time"] if x["time"] else "", reverse=True)
    return {"notifications": items}

@app.put("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Mark a notification as read."""
    user_uuid = uuid.UUID(current_user_id)
    result = await db.execute(
        select(InAppNotification)
        .where(InAppNotification.id == notification_id)
    )
    notification = result.scalars().first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
        
    if notification.user_id is None:
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
):
    """Submit a user suggestion. Authenticated, but trip/location are optional."""
    import html
    text = html.escape(req.suggestion.strip())[:500]
    if not text:
        raise HTTPException(status_code=400, detail="Suggestion text is required.")

    user_uuid = uuid.UUID(current_user_id) if current_user_id else None

    s = Suggestion(
        trip=req.trip,
        location=req.location,
        suggestion=text,
        user_id=user_uuid,
        status="pending",
    )
    db.add(s)
    await db.commit()
    return {"success": True, "message": "Suggestion submitted successfully."}


@app.get("/health")
async def health():
    return {"status": "ok"}
