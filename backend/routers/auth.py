"""
routers/auth.py — Email OTP registration + JWT auth.
Only @duk.ac.in addresses accepted.
OTP is required for first-time login only; subsequent logins use the stored JWT.
"""
import logging
import uuid
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
        if not v.lower().endswith(f"@{settings.ALLOWED_EMAIL_DOMAIN}"):
            raise ValueError(
                f"Only @{settings.ALLOWED_EMAIL_DOMAIN} email addresses are allowed."
            )
        return v.lower()


class VerifyRequest(BaseModel):
    email: EmailStr
    otp:   str


class DeviceTokenRequest(BaseModel):
    device_token:     str
    notifications_on: bool = True


class ProximityPrefsRequest(BaseModel):
    """Payload for updating a user's proximity alert preferences."""
    proximity_alert_enabled: bool = False
    # 'time' (minutes) | 'distance' (metres by road) | 'stops' (stop count)
    proximity_alert_type:    Optional[str] = None
    # Value: e.g., 5 min / 1000 m / 2 stops
    proximity_alert_value:   Optional[int] = None
    # 'source' | 'destination' | 'both'
    proximity_alert_for:     Optional[str] = "source"
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
    if req.proximity_alert_type is not None:
        if req.proximity_alert_type not in ("time", "distance", "stops", None):
            raise HTTPException(status_code=400, detail="alert_type must be 'time', 'distance', or 'stops'.")
        user.proximity_alert_type = req.proximity_alert_type
    if req.proximity_alert_value is not None:
        user.proximity_alert_value = req.proximity_alert_value
    if req.proximity_alert_for is not None:
        if req.proximity_alert_for not in ("source", "destination", "both"):
            raise HTTPException(status_code=400, detail="alert_for must be 'source', 'destination', or 'both'.")
        user.proximity_alert_for = req.proximity_alert_for
    if req.notifications_on is not None:
        user.notifications_on = req.notifications_on

    # Reset the alerted-trip flag so the updated preference applies immediately
    user.last_alerted_trip_id = None
    await db.commit()

    return {
        "success":                 True,
        "proximity_alert_enabled": user.proximity_alert_enabled,
        "proximity_alert_type":    user.proximity_alert_type,
        "proximity_alert_value":   user.proximity_alert_value,
        "proximity_alert_for":     user.proximity_alert_for,
    }
