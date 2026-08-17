import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List
from pydantic import BaseModel
from uuid import UUID

from database import get_db
from services.auth import get_current_user_id
from models.user import User
from models.notification import InAppNotification

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


@router.get("")
async def get_my_notifications(
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetch all In-App Notifications for the current user."""
    # Convert string to UUID for postgres query
    user_uuid = uuid.UUID(current_user_id)
    
    # 1. Fetch In-App Notifications (modern approach)
    result = await db.execute(
        select(InAppNotification)
        .where((InAppNotification.user_id == user_uuid) | (InAppNotification.user_id.is_(None)))
        .order_by(desc(InAppNotification.created_at))
        .limit(100)
    )
    in_app_notifs = result.scalars().all()
    
    items = []
    
    # Track which suggestion/broadcast IDs have already been migrated to InAppNotification
    # so we don't duplicate them. (In modern flows, admin.py creates an InAppNotification)
    # However, since InAppNotification doesn't store a reference ID to the original suggestion,
    # we'll just fetch all and let the frontend deduplicate by 'title/body' or we can just append them.
    # Actually, we can just append them, and if there are duplicates, the frontend will show them.
    # To be safe, let's just append them.
    
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
        
    # 2. Fetch legacy Suggestion responses (where admin_response is not null)
    from models.notification import Suggestion, AdminBroadcast
    s_result = await db.execute(
        select(Suggestion)
        .where(Suggestion.user_id == user_uuid, Suggestion.admin_response.isnot(None))
        .order_by(desc(Suggestion.id))
        .limit(20)
    )
    suggestions = s_result.scalars().all()
    
    for s in suggestions:
        # Check if we already have an InAppNotification for this exact response to avoid duplicates
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
            
    # Sort all items by time descending
    items.sort(key=lambda x: x["time"] if x["time"] else "", reverse=True)
    
    return {"notifications": items}


@router.put("/{notification_id}/read")
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
        # Global broadcast; handled via frontend localStorage
        return {"success": True}
        
    if notification.user_id != user_uuid:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    notification.is_read = True
    await db.commit()
    
    return {"success": True}
