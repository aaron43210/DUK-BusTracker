# services/auth-service/main.py
"""
Auth Service — OTP-based email login for university students.

How login works:
  Step 1: Student sends their university email to POST /register
          → Server generates a 6-digit OTP, saves it, emails it to the student
          → Response is always identical ("OTP sent if email is registered")
            to prevent attackers from guessing which emails exist
  Step 2: Student sends email + OTP to POST /verify
          → Server checks OTP in constant time (prevents timing attacks)
          → If valid, issues a JWT access token (valid 7 days)
          → Token is used for all future authenticated requests

Security features:
  - Only @duk.ac.in / @iitmk.ac.in email addresses accepted
  - Rate limiting: 3 OTP requests per 5 min, 5 verify attempts per 15 min
  - OTP expires in 10 minutes (configurable via OTP_TTL_SECONDS)
  - Constant-time OTP comparison (prevents timing attacks)
  - JWT revocation via Redis blocklist (for logout)

Endpoints:
  POST   /api/v1/auth/register       — Send OTP to email
  POST   /api/v1/auth/verify         — Verify OTP, get JWT token
  POST   /api/v1/auth/logout         — Revoke JWT token
  PUT    /api/v1/auth/device-token   — Save FCM device token for push notifications
  PATCH  /api/v1/auth/preferences    — Update notification stop preferences
  GET    /health                     — Health check
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from libs.duk_common.auth import create_access_token, make_get_current_user_id
from libs.duk_common.settings import get_settings
from libs.duk_common.security import constant_time_compare, RateLimiter
from libs.duk_common.middleware import configure_app
from libs.duk_common.rate_limit_deps import make_rate_limit_dep
from libs.duk_common.redis_client import get_redis
from libs.duk_common.events import EventBus, SendOtpEvent
from database_local import get_db
from models_local import User
from email_helper import generate_otp, send_otp_email, hash_otp, verify_otp_hash

settings = get_settings()

app = FastAPI(title="auth-service", docs_url=None, redoc_url=None)
configure_app(app, allowed_origins=settings.allowed_origins_list)

# Blocklist is always wired in — every authenticated request checks Redis revocation
_auth = make_get_current_user_id(
    settings.SECRET_KEY,
    settings.ALGORITHM,
    redis_getter=get_redis,
)
get_current_user_id = _auth
get_current_jwt_payload = _auth.get_payload

#  Rate limiting dependencies 
# Max 3 OTP requests per email per 5 minutes
otp_send_limit = make_rate_limit_dep("otp:send", max_requests=3, window_seconds=300)
# Max 5 verify attempts per IP per 15 minutes
otp_verify_limit = make_rate_limit_dep("otp:verify", max_requests=5, window_seconds=900)
# Max 10 requests per IP per minute for general auth
auth_general_limit = make_rate_limit_dep("auth:general", max_requests=10, window_seconds=60)


#  Schemas 
class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    boarding_stop_id: int | None = None

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1 or len(v) > 200:
            raise ValueError("Name must be between 1 and 200 characters")
        return v

    @field_validator("email")
    @classmethod
    def duk_email_only(cls, v: str) -> str:
        v_clean = v.lower().strip()
        allowed = (
            f"@{settings.ALLOWED_EMAIL_DOMAIN}",
            "@duk.ac.in",
            "@iiitmk.ac.in",
        )
        if not any(v_clean.endswith(d) for d in allowed):
            raise ValueError(
                f"Only university email addresses are allowed."
            )
        return v_clean


class VerifyRequest(BaseModel):
    email: EmailStr
    otp: str

    @field_validator("otp")
    @classmethod
    def validate_otp_format(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) != 6:
            raise ValueError("OTP must be a 6-digit number")
        return v


class DeviceTokenRequest(BaseModel):
    device_token: str
    notifications_on: bool = True

    @field_validator("device_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        if len(v) > 512:
            raise ValueError("Device token too long")
        return v.strip()


class ProximityPrefsRequest(BaseModel):
    proximity_alert_enabled: Optional[bool] = None
    boarding_alert_stop_id: Optional[int] = None
    destination_alert_stop_id: Optional[int] = None
    notifications_on: Optional[bool] = None


# LogoutRequest removed — the server extracts the JTI from the JWT directly.
# Clients must NOT supply a JTI; the endpoint always revokes the calling token.


#  Routes 
@app.post("/api/v1/auth/register", status_code=202)
async def register(
    req: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Sends a 6-digit OTP to the student's university email address.

    Input:  RegisterRequest { name, email, boarding_stop_id }
    Output: { message: "If this email is registered, an OTP has been sent." }

    - Creates a new user if the email doesn't exist yet
    - Updates name/stop if user already exists (allows re-registration)
    - OTP expires in OTP_TTL_SECONDS (default 10 minutes)
    - Always returns the same message to prevent email enumeration
    - Rate limited: max 3 OTP sends per email per 5 minutes
    """
    redis = None
    try:
        redis = await get_redis()
        limiter = RateLimiter(redis)
        await limiter.check("otp:send", req.email, max_requests=3, window_seconds=300)
    except HTTPException:
        raise
    except Exception:
        pass  # allow if redis is down

    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            id=uuid.uuid4(),
            name=req.name.strip() or req.email.split("@")[0],
            email=req.email,
            boarding_stop_id=req.boarding_stop_id,
        )
        db.add(user)
    else:
        # Only update profile for unverified accounts (re-registration flow).
        # A verified account's name/stop can only be changed via authenticated
        # PATCH /preferences to prevent unauthenticated metadata overwrite.
        if not user.verified:
            if req.name.strip():
                user.name = req.name.strip()
            if req.boarding_stop_id is not None:
                user.boarding_stop_id = req.boarding_stop_id

    otp = generate_otp()
    # Store only the HMAC hash — never the plaintext OTP
    user.otp_code = hash_otp(otp)
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.OTP_TTL_SECONDS
    )
    await db.commit()

    # Publish OTP sending to worker queue (or direct background task if Redis unavailable)
    if redis is not None:
        try:
            bus = EventBus(redis)
            await bus.publish("auth.otp", SendOtpEvent(email=req.email, name=user.name, otp=otp))
        except Exception:
            background_tasks.add_task(send_otp_email, req.email, user.name, otp)
    else:
        background_tasks.add_task(send_otp_email, req.email, user.name, otp)

    # Always return same message to prevent user enumeration
    return {"message": "If this email is registered, an OTP has been sent."}


@app.post("/api/v1/auth/verify")
async def verify(
    req: VerifyRequest,
    db: AsyncSession = Depends(get_db),
    _limit: None = Depends(otp_verify_limit),
):
    """
    Verifies the OTP and returns a JWT access token.

    Input:  VerifyRequest { email, otp (6 digits) }
    Output: {
        access_token, token_type, expires_in_days,
        user: { id, name, email, boarding_stop_id }
    }

    - Returns 400 if OTP is wrong or expired (same error, prevents enumeration)
    - Uses constant-time comparison to prevent timing attacks
    - OTP is cleared after successful verification
    - Rate limited: max 5 verify attempts per IP and email
    """
    try:
        redis = await get_redis()
        limiter = RateLimiter(redis)
        await limiter.check("otp:verify", req.email, max_requests=5, window_seconds=900)
    except HTTPException:
        raise
    except Exception:
        pass

    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    # Always run hash comparison even for non-existent users to prevent
    # timing-based email enumeration.
    _DUMMY_HASH = hash_otp("000000")
    stored_hash = user.otp_code if (user and user.otp_code) else _DUMMY_HASH

    otp_valid = verify_otp_hash(req.otp.strip(), stored_hash)

    if not user or not otp_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    if not user.otp_expires_at or datetime.now(timezone.utc) > user.otp_expires_at:
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    # Atomically clear OTP and mark verified to prevent race-condition double-use
    user.verified = True
    user.otp_code = None
    user.otp_expires_at = None
    await db.commit()

    token, jti = create_access_token(
        str(user.id),
        settings.SECRET_KEY,
        settings.ALGORITHM,
        expire_days=settings.ACCESS_TOKEN_EXPIRE_DAYS,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_days": settings.ACCESS_TOKEN_EXPIRE_DAYS,
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "boarding_stop_id": user.boarding_stop_id,
        },
    }


@app.post("/api/v1/auth/logout")
async def logout(
    payload: dict = Depends(get_current_jwt_payload),
):
    """
    Revoke the calling JWT by adding its JTI to the Redis blocklist.

    The JTI is extracted server-side from the validated JWT — the client
    does NOT supply it. This ensures a token can only revoke itself.
    """
    jti = payload.get("jti")
    if jti:
        try:
            redis = await get_redis()
            from libs.duk_common.security import TokenBlocklist
            bl = TokenBlocklist(redis)
            # TTL = remaining lifetime of the token so blocklist entry self-cleans
            exp = payload.get("exp", 0)
            remaining = max(int(exp - datetime.now(timezone.utc).timestamp()), 1)
            await bl.revoke(jti, remaining)
        except Exception as e:
            import logging
            logging.error(f"[AUTH] Redis unavailable during JWT revocation: {e}")
    return {"success": True}


@app.put("/api/v1/auth/device-token")
async def update_device_token(
    req: DeviceTokenRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    _limit: None = Depends(auth_general_limit),
):
    """
    Saves the user's FCM device token for push notifications.

    Input:  DeviceTokenRequest { device_token, notifications_on }
    Output: { success: true }

    Called when the PWA receives a new FCM registration token from Firebase.
    The token is stored in the user row and used by the notification service
    to send push notifications to this specific device.
    Requires: Bearer JWT token in Authorization header
    """
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
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
    _limit: None = Depends(auth_general_limit),
):
    """
    Updates the user's notification stop preferences.

    Input:  ProximityPrefsRequest {
        proximity_alert_enabled,    — True/False master switch
        boarding_alert_stop_id,     — Stop ID to alert when bus approaches for boarding
        destination_alert_stop_id,  — Stop ID to alert when bus approaches destination
        notifications_on            — Master switch for all push notifications
    }
    Output: { success: true, proximity_alert_enabled, boarding_alert_stop_id, destination_alert_stop_id }

    After saving, publishes a UserPreferencesUpdatedEvent to Redis so the
    trip-lifecycle service can refresh its proximity alert cache immediately.
    Requires: Bearer JWT token in Authorization header
    """
    user_uuid = uuid.UUID(current_user_id)
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if hasattr(user, key):
            setattr(user, key, value)

    user.last_alerted_trip_id = None
    await db.commit()

    try:
        redis = await get_redis()
        from libs.duk_common.events import UserPreferencesUpdatedEvent
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
        pass

    return {
        "success": True,
        "proximity_alert_enabled": user.proximity_alert_enabled,
        "boarding_alert_stop_id": user.boarding_alert_stop_id,
        "destination_alert_stop_id": user.destination_alert_stop_id,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
