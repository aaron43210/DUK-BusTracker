# services/auth-service/main.py
"""Auth Service"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.auth import create_access_token, make_get_current_user_id
from libs.duk_common.settings import get_settings

from database_local import get_db
from models_local import User
from email_helper import generate_otp, send_otp_email

app = FastAPI(title="auth-service")
settings = get_settings()

get_current_user_id = make_get_current_user_id(settings.SECRET_KEY, settings.ALGORITHM)

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
    proximity_alert_enabled:   Optional[bool] = None   # None = "don't change"
    boarding_alert_stop_id:    Optional[int] = None
    destination_alert_stop_id: Optional[int] = None
    notifications_on:          Optional[bool] = None

# ── Routes ────────────────────────────────────────────────────────────────────
@app.post("/api/v1/auth/register", status_code=202)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
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
    else:
        if req.name.strip():
            user.name = req.name.strip()
        if req.boarding_stop_id:
            user.boarding_stop_id = req.boarding_stop_id

    otp = generate_otp()
    user.otp_code = otp
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.OTP_TTL_SECONDS)
    await db.commit()

    send_otp_email(req.email, user.name, otp)

    return {"message": "OTP sent to your @duk.ac.in email. Check your inbox."}

@app.post("/api/v1/auth/verify")
async def verify(req: VerifyRequest, db: AsyncSession = Depends(get_db)):
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

    token = create_access_token(str(user.id), settings.SECRET_KEY, settings.ALGORITHM)
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

@app.put("/api/v1/auth/device-token")
async def update_device_token(
    req:     DeviceTokenRequest,
    db:      AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    user_uuid = uuid.UUID(user_id)
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
        
    user.device_token = req.device_token
    user.notifications_on = req.notifications_on
    await db.commit()
    
    return {"success": True}

@app.patch("/api/v1/auth/preferences")
async def update_preferences(
    req: ProximityPrefsRequest,
    db:  AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user_uuid = uuid.UUID(current_user_id)
    result = await db.execute(select(User).where(User.id == user_uuid))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Only update fields that were explicitly sent in the JSON payload
    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    # Reset the alerted-trip flag so updated preferences apply immediately
    user.last_alerted_trip_id = None
    await db.commit()

    # Publish event so trip-lifecycle can refresh its per-user preference cache
    try:
        from libs.duk_common.redis_client import get_redis
        from libs.duk_common.events import UserPreferencesUpdatedEvent
        redis = await get_redis()
        ev = UserPreferencesUpdatedEvent(
            user_id=str(user.id),
            proximity_alert_enabled=user.proximity_alert_enabled,
            boarding_alert_stop_id=user.boarding_alert_stop_id,
            destination_alert_stop_id=user.destination_alert_stop_id,
            notifications_on=user.notifications_on,
            device_token=user.device_token,
        )
        await redis.publish("user:preferences_updated", ev.model_dump_json())
    except Exception:
        pass  # non-blocking; trip-lifecycle will re-read DB on next GPS tick if cache misses

    return {
        "success":                   True,
        "proximity_alert_enabled":   user.proximity_alert_enabled,
        "boarding_alert_stop_id":    user.boarding_alert_stop_id,
        "destination_alert_stop_id": user.destination_alert_stop_id,
    }

@app.get("/health")
async def health():
    return {"status": "ok"}
