"""
routers/auth.py — Email OTP registration + JWT auth.
Only @duk.ac.in addresses accepted.
OTP is required for first-time login only; subsequent logins use the stored JWT.
"""
import logging
import uuid
from uuid import UUID
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from models.user import User

from services.auth import create_access_token, get_current_user_id
from services.email import generate_otp, send_otp_email
from config import get_settings

logger    = logging.getLogger(__name__)
settings  = get_settings()
router    = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    name:             str
    email:            EmailStr
    boarding_stop_id: int | None = None

    @field_validator("email")
    @classmethod
    def duk_email_only(cls, v: str) -> str:
        v_clean = v.lower().strip()
        allowed = (f"@{settings.ALLOWED_EMAIL_DOMAIN}", "@duk.ac.in", "@iitmk.ac.in")
        if not any(v_clean.endswith(d) for d in allowed):
            raise ValueError(
                f"Only university email addresses (@{settings.ALLOWED_EMAIL_DOMAIN} / @iitmk.ac.in) are allowed."
            )
        return v_clean


class VerifyRequest(BaseModel):
    email: EmailStr
    otp:   str


class DeviceTokenRequest(BaseModel):
    device_token:     str
    notifications_on: bool = True


class ProximityPrefsRequest(BaseModel):
    """Payload for updating a user's proximity alert preferences."""
    proximity_alert_enabled: bool = False
    boarding_alert_stop_id:  Optional[int] = None
    destination_alert_stop_id: Optional[int] = None
    notifications_on:        Optional[bool] = None

# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/register", status_code=202)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Step 1: User submits name + @duk.ac.in email.
    - If user already exists and is verified, just send a new OTP (re-login).
    - If user doesn't exist, create a record and send OTP.
    OTP is sent via SMTP; in dev mode it's printed to the server log.
    """
    # Upsert user
    result = await db.execute(select(User).where(User.email == req.email))
    user   = result.scalar_one_or_none()

    if not user:
        user = User(
            id=uuid.uuid4(),
            name=req.email.split("@")[0] if not req.name.strip() else req.name.strip(),
            email=req.email,
            boarding_stop_id=req.boarding_stop_id,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        # Update name/boarding stop if provided
        if req.name.strip():
            user.name = req.name.strip()
        if req.boarding_stop_id:
            user.boarding_stop_id = req.boarding_stop_id
        await db.commit()

    otp = generate_otp()
    user.otp_code = otp
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.OTP_TTL_SECONDS)
    await db.commit()

    send_otp_email(req.email, user.name, otp)

    return {"message": "OTP sent to your @duk.ac.in email. Check your inbox."}


@router.post("/verify")
async def verify(req: VerifyRequest, db: AsyncSession = Depends(get_db)):
    """
    Step 2: User submits OTP.
    On success → mark user verified + return JWT.
    """
    
    result = await db.execute(select(User).where(User.email == req.email))
    user   = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please register first.")

    if not user.otp_code or user.otp_code != req.otp.strip():
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")
        
    if not user.otp_expires_at or datetime.now(timezone.utc) > user.otp_expires_at:
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    user.verified = True
    user.otp_code = None
    user.otp_expires_at = None
    await db.commit()

    token = create_access_token(str(user.id))
    return {
        "access_token": token,
        "token_type":   "bearer",
        "user": {
            "id":               str(user.id),
            "name":             user.name,
            "email":            user.email,
            "boarding_stop_id": user.boarding_stop_id,
        },
    }


@router.put("/device-token")
async def update_device_token(
    req:     DeviceTokenRequest,
    db:      AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Store/update the FCM device token for push notifications."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    user.device_token = req.device_token
    user.notifications_on = req.notifications_on
    await db.commit()
    
    return {"success": True}


@router.patch("/preferences")
async def update_preferences(
    req: ProximityPrefsRequest,
    db:  AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Update a user's proximity alert preferences and notification settings.
    Called from the mobile app Settings screen.
    """
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    result = await db.execute(select(User).where(User.id == current_user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.proximity_alert_enabled = req.proximity_alert_enabled
    if req.boarding_alert_stop_id is not None:
        user.boarding_alert_stop_id = req.boarding_alert_stop_id
    if req.destination_alert_stop_id is not None:
        user.destination_alert_stop_id = req.destination_alert_stop_id
    if req.notifications_on is not None:
        user.notifications_on = req.notifications_on

    # Reset the alerted-trip flag so the updated preference applies immediately
    user.last_alerted_trip_id = None
    await db.commit()

    return {
        "success":                 True,
        "proximity_alert_enabled":   user.proximity_alert_enabled,
        "boarding_alert_stop_id":    user.boarding_alert_stop_id,
        "destination_alert_stop_id": user.destination_alert_stop_id,
    }


@router.get("/me/notifications")
async def get_my_notifications(
    db: AsyncSession = Depends(get_db), 
    current_user_id: UUID = Depends(get_current_user_id)
):
    """Fetch persistent notifications (suggestion responses and broadcasts) for the current user."""
    from models.notification import Suggestion, AdminBroadcast
    from sqlalchemy import desc
    
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")
        
    # Get user's suggestions with responses
    s_result = await db.execute(
        select(Suggestion)
        .where(Suggestion.user_id == current_user_id, Suggestion.admin_response.isnot(None))
        .order_by(desc(Suggestion.id))
        .limit(20)
    )
    suggestions = s_result.scalars().all()
    
    items = []
    for s in suggestions:
        title = "Response to your suggestion"
        body = f"Admin ({s.status}): {s.admin_response}"
        items.append({
            "id": f"sugg_{s.id}",
            "notification": {
                "title": title,
                "body": body
            },
            "time": s.created_at.isoformat() if s.created_at else None,
            "type": "suggestion"
        })
        
    # Get recent admin broadcasts
    b_result = await db.execute(
        select(AdminBroadcast)
        .order_by(desc(AdminBroadcast.id))
        .limit(10)
    )
    broadcasts = b_result.scalars().all()
    for b in broadcasts:
        items.append({
            "id": f"bc_{b.id}",
            "notification": {
                "title": b.title,
                "body": b.body
            },
            "time": b.sent_at.isoformat() if b.sent_at else None,
            "type": "broadcast"
        })
        
    # Sort descending by time
    items.sort(key=lambda x: x["time"] or "", reverse=True)
    return {"notifications": items[:30]}
