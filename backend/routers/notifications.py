from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List
from pydantic import BaseModel

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
    # Assuming user_id is properly populated during fan-out, but NULL means global broadcast
    result = await db.execute(
        select(InAppNotification)
        .where((InAppNotification.user_id == current_user_id) | (InAppNotification.user_id.is_(None)))
        .order_by(desc(InAppNotification.created_at))
        .limit(100)
    )
    notifications = result.scalars().all()
    
    return {"notifications": [
        {
            "id": n.id,
            "notification": {
                "title": n.title,
                "body": n.body
            },
            "type": n.type,
            "is_read": n.is_read,
            "time": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifications
    ]}


@router.put("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Mark a notification as read."""
    result = await db.execute(
        select(InAppNotification)
        .where(
            InAppNotification.id == notification_id,
            InAppNotification.user_id == current_user_id
        )
    )
    notification = result.scalars().first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
        
    notification.is_read = True
    await db.commit()
    
    return {"success": True}
