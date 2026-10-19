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
          → If valid, issues a 15-minute JWT access token + 1-year refresh token
          → Access token used for all API requests; refresh token used to silently
            renew expired access tokens via POST /refresh (invisible to user)

Authentication model (two-token system):
  - Access Token:  15-minute JWT. Short-lived for security.
  - Refresh Token: 1-year opaque token stored as SHA-256 hash in DB.
                   On access token expiry, PWA silently calls /refresh.
                   Logout deletes all refresh tokens for the user.

Security features:
  - Only @duk.ac.in / @iiitmk.ac.in email addresses accepted
  - Rate limiting: 3 OTP requests per 5 min, 5 verify attempts per 15 min
  - OTP expires in 10 minutes (configurable via OTP_TTL_SECONDS)
  - Constant-time OTP comparison (prevents timing attacks)
  - JWT revocation via Redis blocklist (for logout)
  - Refresh token rotation on every renewal (prevents token replay attacks)

Endpoints:
  POST   /api/v1/auth/register       — Send OTP to email
  POST   /api/v1/auth/verify         — Verify OTP, get JWT + refresh token
  POST   /api/v1/auth/google         — Sign in with Google (domain-restricted)
  POST   /api/v1/auth/refresh        — Exchange refresh token for new access token
  POST   /api/v1/auth/logout         — Revoke JWT + delete refresh tokens
  PUT    /api/v1/auth/device-token   — Save FCM device token for push notifications
  PATCH  /api/v1/auth/preferences    — Update notification stop preferences
  GET    /health                     — Health check
"""
import os
import sys
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from libs.duk_common.auth import create_access_token, create_refresh_token, make_get_current_user_id
from libs.duk_common.settings import get_settings
from libs.duk_common.security import RateLimiter
from libs.duk_common.middleware import configure_app
from libs.duk_common.rate_limit_deps import make_rate_limit_dep
from libs.duk_common.redis_client import get_redis
from database_local import get_db
from libs.duk_common.models import User, BusStop, RefreshToken
from email_helper import generate_otp, send_otp_email, hash_otp, verify_otp_hash

settings = get_settings()

app = FastAPI(title="auth-service", docs_url=None, redoc_url=None)
configure_app(app, allowed_origins=settings.allowed_origins_list, environment=settings.ENVIRONMENT)

# Blocklist is always wired in — every authenticated request checks Redis revocation
_auth = make_get_current_user_id(
    secret_key=settings.SECRET_KEY,
    redis_getter=get_redis,
    algorithm=settings.ALGORITHM,
)
get_current_user_id = _auth.get_user_id
get_current_jwt_payload = _auth.get_payload

#  Rate limiting dependencies 
# Max 3 OTP requests per email per 5 minutes
otp_send_limit = make_rate_limit_dep("otp:send", max_requests=3, window_seconds=300)
# Max 5 verify attempts per IP and email per 15 minutes
otp_verify_limit = make_rate_limit_dep("otp:verify", max_requests=5, window_seconds=900)
# Max 10 requests per IP per minute for general auth
auth_general_limit = make_rate_limit_dep("auth:general", max_requests=10, window_seconds=60)
# Refresh needs a higher limit: the client retries silently (up to 3×) and multiple
# students share the same mobile carrier CGNAT IP. 120/min = enough for a bus full
# of students all refreshing simultaneously after a server restart.
refresh_limit = make_rate_limit_dep("auth:refresh", max_requests=120, window_seconds=60)


#  Schemas 
class RegisterRequest(BaseModel):
    name: str = ""
    email: EmailStr
    boarding_stop_id: int | None = None

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 200:
            raise ValueError("Name must be less than 200 characters")
        return v

    @field_validator("email")
    @classmethod
    def duk_email_only(cls, v: str) -> str:
        """Validates that the email belongs to an allowed university domain."""
        v_clean = v.lower().strip()
        allowed = [f"@{d}" for d in settings.allowed_email_domains_list]
        if not any(v_clean.endswith(d) for d in allowed):
            raise ValueError(
                f"Only university email addresses are allowed."
            )
        return v_clean


class VerifyRequest(BaseModel):
    email: EmailStr
    otp: str
    client_type: str = "mobile"  # "mobile" or "web"

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
        v = v.strip()
        if not v:
            raise ValueError("Device token cannot be empty")
        if len(v) > 512:
            raise ValueError("Device token too long")
        return v


import hashlib

class RefreshRequest(BaseModel):
    refresh_token: str

    @field_validator("refresh_token")
    @classmethod
    def validate_refresh_token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Refresh token cannot be empty")
        if len(v) > 512:
            raise ValueError("Refresh token too long")
        return v


class UpdateProfileRequest(BaseModel):
    name: str
    boarding_stop_id: int

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1 or len(v) > 200:
            raise ValueError("Name must be between 1 and 200 characters")
        return v


class ProximityPrefsRequest(BaseModel):
    proximity_alert_enabled: Optional[bool] = None
    boarding_alert_stop_id: Optional[int] = None
    destination_alert_stop_id: Optional[int] = None
    notifications_on: Optional[bool] = None
    notify_trip_started: Optional[bool] = None
    notify_delays: Optional[bool] = None
    notify_admin_broadcasts: Optional[bool] = None


# LogoutRequest removed — the server extracts the JTI from the JWT directly.
# Clients must NOT supply a JTI; the endpoint always revokes the calling token.


#  Routes 
@app.post("/api/v1/auth/register", status_code=202)
async def register(
    req: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(otp_send_limit),
):
    """Generates and sends an OTP for university student authentication."""
    try:
        redis = await get_redis()
    except Exception as e:
        logging.getLogger(__name__).error("Redis unavailable during registration: %s", e)
        raise HTTPException(status_code=503, detail="Service unavailable")

    limiter = RateLimiter(redis, key_prefix="otp:send:email", max_requests=3, window_seconds=300)
    allowed, remaining, retry_after = await limiter.check(req.email)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many OTP requests for this email. Please wait.")

    if req.boarding_stop_id is not None:
        stop_result = await db.execute(select(BusStop).where(BusStop.id == req.boarding_stop_id))
        stop = stop_result.scalar_one_or_none()
        if not stop:
            raise HTTPException(status_code=400, detail="Invalid boarding stop.")

    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            id=uuid.uuid4(),
            email=req.email,
            name=req.name.strip() or req.email.split("@")[0],
            boarding_stop_id=req.boarding_stop_id,
        )
        db.add(user)
    else:
        # A verified account's metadata is updated via PATCH /preferences
        if not user.verified:
            if req.name.strip():
                user.name = req.name.strip()
            if req.boarding_stop_id is not None:
                user.boarding_stop_id = req.boarding_stop_id

    otp = generate_otp()
    # Store only the HMAC hash — never the plaintext OTP
    hashed = hash_otp(otp)
    await redis.setex(f"otp:{req.email}", settings.OTP_TTL_SECONDS, hashed)

    await db.commit()

    # Dispatch OTP email directly to background tasks to prevent plaintext OTPs in Redis streams
    display_name = req.name.strip() if req.name else req.email.split("@")[0]
    background_tasks.add_task(send_otp_email, req.email, display_name, otp)

    # Always return same message to prevent user enumeration
    return {"message": "If this email is registered, an OTP has been sent."}


@app.post("/api/v1/auth/verify")
async def verify(
    req: VerifyRequest,
    db: AsyncSession = Depends(get_db),
    _limit: None = Depends(otp_verify_limit),
):
    """Verifies the OTP and returns a JWT access token."""
    try:
        redis = await get_redis()
    except Exception as e:
        logging.getLogger(__name__).error("Redis unavailable during verification: %s", e)
        raise HTTPException(status_code=503, detail="Service unavailable")

    limiter = RateLimiter(redis, key_prefix="otp:verify:email", max_requests=5, window_seconds=900)
    allowed, remaining, retry_after = await limiter.check(req.email)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many verification attempts for this email.")

    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    # Always run hash comparison even for non-existent users to prevent
    # timing-based email enumeration.
    _DUMMY_HASH = hash_otp("000000")
    stored_hash_raw = await redis.get(f"otp:{req.email}")
    if isinstance(stored_hash_raw, bytes):
        stored_hash = stored_hash_raw.decode("utf-8")
    elif stored_hash_raw is not None:
        stored_hash = stored_hash_raw
    else:
        stored_hash = _DUMMY_HASH

    otp_valid = verify_otp_hash(req.otp.strip(), stored_hash)

    if not user or not otp_valid or not stored_hash_raw:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    # Atomically clear OTP and mark verified
    await redis.delete(f"otp:{req.email}")
    if not user.verified:
        user.verified = True
        await db.commit()

    token, jti = create_access_token(
        str(user.id),
        settings.SECRET_KEY,
        settings.ALGORITHM,
        expire_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )

    # Force single-device login PER CLIENT: clear all existing refresh sessions for this specific client.
    from sqlalchemy import delete
    await db.execute(
        delete(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .where(RefreshToken.client_type == req.client_type)
    )

    # Generate a secure refresh token and store its hash in the DB
    refresh_plaintext, refresh_hash, refresh_expires = create_refresh_token(
        expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    db_refresh = RefreshToken(
        user_id=user.id,
        token_hash=refresh_hash,
        client_type=req.client_type,
        family_id=uuid.uuid4(),
        expires_at=refresh_expires,
    )
    db.add(db_refresh)
    await db.commit()

    return {
        "access_token": token,
        "refresh_token": refresh_plaintext,
        "token_type": "bearer",
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "boarding_stop_id": user.boarding_stop_id,
            "notify_trip_started": user.notify_trip_started,
            "notify_delays": user.notify_delays,
            "notify_admin_broadcasts": user.notify_admin_broadcasts,
        },
    }


logger = logging.getLogger(__name__)


@app.post("/api/v1/auth/logout")
async def logout(
    request: Request,
    payload: dict = Depends(get_current_jwt_payload),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke the calling JWT by adding its JTI to the Redis blocklist,
    and clear the user's FCM device token so they stop receiving pushes.
    Returns 503 if Redis is unavailable, making the API response truthful.
    """
    try:
        body = await request.json()
        req_refresh_token = body.get("refresh_token")
    except Exception:
        req_refresh_token = None

    jti = payload.get("jti")
    user_id = payload.get("sub")

    if jti:
        try:
            redis = await get_redis()
            from libs.duk_common.security import TokenBlocklist
            bl = TokenBlocklist(redis)
            exp = payload.get("exp", 0)
            remaining = max(int(exp - datetime.now(timezone.utc).timestamp()), 1)
            await bl.revoke(jti, remaining)
        except Exception as e:
            logger.error("[AUTH] Redis unavailable during JWT revocation: %s", e)
            raise HTTPException(status_code=503, detail="Service unavailable")

    if user_id:
        try:
            from sqlalchemy import update, delete
            user_uuid = uuid.UUID(user_id)
            # Clear FCM device token — stops all push notifications immediately
            await db.execute(update(User).where(User.id == user_uuid).values(device_token=None))
            
            # If the client provided the specific refresh token they are logging out of,
            # delete ONLY that one token to support multi-device logins.
            if req_refresh_token:
                incoming_hash = hashlib.sha256(req_refresh_token.encode()).hexdigest()
                await db.execute(
                    delete(RefreshToken)
                    .where(RefreshToken.user_id == user_uuid)
                    .where(RefreshToken.token_hash == incoming_hash)
                )
            else:
                # Fallback: Delete ALL refresh tokens for this user
                await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_uuid))
                
            await db.commit()
        except Exception as e:
            logger.error("[AUTH] DB unavailable during device token cleanup: %s", e)

    return {"success": True}


@app.post("/api/v1/auth/refresh")
async def refresh_token(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    _limit: None = Depends(refresh_limit),
):
    """
    Exchange a valid refresh token for a new access token (+ rotated refresh token).

    Token rotation with a grace window: each refresh issues a brand-new refresh
    token (with the full 365-day lifetime, so the session stays long-lived), and
    the old token is shortened to a brief grace window rather than being reused
    forever. This makes a stolen token detectable/revocable while still tolerating
    the concurrent double-refresh that mobile connections do (both requests land
    inside the grace window, so neither logs the user out).

    Input:  RefreshRequest { refresh_token: "..." }
    Output: { access_token, refresh_token, expires_in_minutes }
    """
    from sqlalchemy import delete
    from datetime import timedelta

    # Hash the incoming token to look up in DB (we never store plaintext)
    incoming_hash = hashlib.sha256(req.refresh_token.encode()).hexdigest()

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == incoming_hash)
    )
    db_token = result.scalar_one_or_none()

    if not db_token:
        raise HTTPException(status_code=401, detail="Invalid refresh token.")

    now = datetime.now(timezone.utc)
    token_expires = db_token.expires_at.replace(tzinfo=timezone.utc)

    # Refresh-token reuse detection: a token that was already rotated and whose
    # grace window has elapsed must never be accepted again. Re-presentation past
    # grace means the token was captured and replayed, so revoke the ENTIRE family
    # (every descendant of the original login) and force a fresh login.
    if db_token.rotated and token_expires < now and db_token.family_id is not None:
        await db.execute(
            delete(RefreshToken).where(RefreshToken.family_id == db_token.family_id)
        )
        await db.commit()
        logger.warning(
            "[AUTH] Refresh-token reuse detected for user %s — revoked token family",
            db_token.user_id,
        )
        raise HTTPException(status_code=401, detail="Session invalidated. Please log in again.")

    if token_expires < now:
        # Expired (and never rotated) — benign idle expiry; clean it up.
        await db.delete(db_token)
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token expired. Please log in again.")

    # Grace-window idempotency: the token was already rotated but is still within
    # its grace window. This is the normal mobile scenario — the client sent the
    # refresh request, got suspended before storing the response, and is replaying
    # the old token. We MUST NOT mint a new refresh token here, because doing so
    # would fork the session: the legitimate client (who did receive the successor)
    # and the replayer would both have independently valid, indefinitely-renewable
    # sessions with no way for the user to detect the split.
    #
    # Solution: issue a fresh access token (short-lived, 15 min, no harm) but
    # return the SAME refresh token the client just presented. The client can keep
    # using it until the grace window closes, at which point they will either have
    # stored the real successor (Token B) or will hit a genuine 401.
    if db_token.rotated:
        # Already rotated, still within grace — idempotent response, no forking.
        grace_access_token, _ = create_access_token(
            str(db_token.user_id),
            settings.SECRET_KEY,
            settings.ALGORITHM,
            expire_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        )
        logger.info(
            "[AUTH] Grace-window replay for user %s — returning same refresh token (no fork)",
            db_token.user_id,
        )
        return {
            "access_token": grace_access_token,
            "refresh_token": req.refresh_token,  # same token the client sent — idempotent
            "token_type": "bearer",
            "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        }

    # Issue new access token
    new_access_token, _ = create_access_token(
        str(db_token.user_id),
        settings.SECRET_KEY,
        settings.ALGORITHM,
        expire_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )

    # Issue a NEW refresh token with the full long lifetime (sliding 365-day session).
    # Each successful use resets the clock — students who use the app regularly
    # are never logged out due to inactivity (mirrors Amazon / Google behaviour).
    new_refresh_plaintext, new_refresh_hash, new_refresh_expires = create_refresh_token(
        expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    db.add(RefreshToken(
        user_id=db_token.user_id,
        token_hash=new_refresh_hash,
        client_type=db_token.client_type,
        # Successor stays in the same lineage. Legacy tokens issued before this
        # column existed have family_id=None; seed a fresh family in that case so
        # reuse detection applies going forward.
        family_id=db_token.family_id or uuid.uuid4(),
        expires_at=new_refresh_expires,
    ))

    # Mark the presented token as rotated so any later replay past the grace
    # window trips the reuse-detection branch above.
    db_token.rotated = True

    # Keep the OLD token alive only for a short grace window instead of forever,
    # so a racing second refresh from the same device still succeeds but a leaked
    # token stops working almost immediately. Only ever shorten, never extend.
    grace_deadline = now + timedelta(seconds=settings.REFRESH_TOKEN_GRACE_SECONDS)
    if db_token.expires_at.replace(tzinfo=timezone.utc) > grace_deadline:
        db_token.expires_at = grace_deadline

    # Opportunistic cleanup so rotation doesn't grow the table unbounded.
    await db.execute(
        delete(RefreshToken)
        .where(RefreshToken.user_id == db_token.user_id)
        .where(RefreshToken.expires_at < now)
    )

    await db.commit()

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_plaintext,  # client must store the rotated token
        "token_type": "bearer",
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    }


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
    
    # Strip the token from any other user first to prevent multi-account duplicates
    if req.device_token:
        from sqlalchemy import update
        await db.execute(
            update(User)
            .where(User.device_token == req.device_token)
            .where(User.id != user_uuid)
            .values(device_token=None)
        )

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
    """Updates the user's notification stop preferences."""
    user_uuid = uuid.UUID(current_user_id)
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if req.boarding_alert_stop_id is not None:
        stop_result = await db.execute(select(BusStop).where(BusStop.id == req.boarding_alert_stop_id))
        if not stop_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid boarding alert stop.")
            
    if req.destination_alert_stop_id is not None:
        stop_result = await db.execute(select(BusStop).where(BusStop.id == req.destination_alert_stop_id))
        if not stop_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid destination alert stop.")

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
    except Exception as e:
        logger.warning("[AUTH] Redis unavailable during preference publish: %s", e)

    return {
        "success": True,
        "proximity_alert_enabled": user.proximity_alert_enabled,
        "boarding_alert_stop_id": user.boarding_alert_stop_id,
        "destination_alert_stop_id": user.destination_alert_stop_id,
    }


@app.patch("/api/v1/auth/profile")
async def update_profile(
    req: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
    _limit: None = Depends(auth_general_limit),
):
    """Updates core profile data (name and boarding_stop_id) after onboarding."""
    user_uuid = uuid.UUID(current_user_id)
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    stop_result = await db.execute(select(BusStop).where(BusStop.id == req.boarding_stop_id))
    if not stop_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Invalid boarding stop.")

    user.name = req.name
    user.boarding_stop_id = req.boarding_stop_id
    await db.commit()
    await db.refresh(user)

    return {
        "success": True,
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "boarding_stop_id": user.boarding_stop_id,
            "notify_trip_started": user.notify_trip_started,
            "notify_delays": user.notify_delays,
            "notify_admin_broadcasts": user.notify_admin_broadcasts,
        }
    }


# ── Google OAuth ──────────────────────────────────────────────────────────────

class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token (JWT) from the GIS client
    client_type: str = "mobile"  # "mobile" or "web"


@app.post("/api/v1/auth/google")
async def google_auth(
    req: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db),
    _limit: None = Depends(auth_general_limit),
):
    """
    Authenticates a user via Google Sign-In.

    Flow:
      1. Verifies the Google ID token using Google's public certs.
      2. Checks the email domain is @duk.ac.in or @iiitmk.ac.in.
      3. Creates the user in the DB if they don't exist (auto-verified).
      4. Issues our own JWT access token + refresh token — identical to OTP flow.
    """
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        if not google_client_id:
            raise HTTPException(status_code=503, detail="Google OAuth is not configured on this server.")

        # Verify the ID token signature and expiry against Google's public keys
        id_info = id_token.verify_oauth2_token(
            req.credential,
            google_requests.Request(),
            google_client_id,
        )
    except ValueError as exc:
        logging.getLogger(__name__).warning("[GOOGLE_AUTH] Invalid Google token: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid Google token.")
    except Exception as exc:
        logging.getLogger(__name__).error("[GOOGLE_AUTH] Token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Google token verification failed.")

    email: str = id_info.get("email", "").lower().strip()
    email_verified: bool = id_info.get("email_verified", False)
    name: str = id_info.get("name", email.split("@")[0])

    if not email_verified:
        raise HTTPException(status_code=400, detail="Google account email is not verified.")

    # Enforce university domain restriction using the SAME configured allowlist
    # as OTP login (settings.ALLOWED_EMAIL_DOMAINS). Previously this used a
    # separate hardcoded set, so changing the OTP allowlist silently left Google
    # login out of sync.
    allowed_domains = set(settings.allowed_email_domains_list)
    domain = email.split("@")[-1] if "@" in email else ""
    if domain not in allowed_domains:
        raise HTTPException(
            status_code=403,
            detail="Only university email addresses are allowed."
        )

    # Find or create user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=email,
            name=name,
            verified=True,   # Google already verified the email
            notifications_on=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    elif not user.verified:
        user.verified = True
        await db.commit()

    # Issue our own JWT access token + refresh token (identical to OTP flow)
    token, jti = create_access_token(
        str(user.id),
        settings.SECRET_KEY,
        settings.ALGORITHM,
        expire_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )

    # Force single-device login PER CLIENT: clear all existing refresh sessions for this specific client.
    from sqlalchemy import delete
    await db.execute(
        delete(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .where(RefreshToken.client_type == req.client_type)
    )

    refresh_plaintext, refresh_hash, refresh_expires = create_refresh_token(
        expire_days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    db_refresh = RefreshToken(
        user_id=user.id,
        token_hash=refresh_hash,
        client_type=req.client_type,
        family_id=uuid.uuid4(),
        expires_at=refresh_expires,
    )
    db.add(db_refresh)
    await db.commit()

    return {
        "access_token": token,
        "refresh_token": refresh_plaintext,
        "token_type": "bearer",
        "expires_in_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "boarding_stop_id": user.boarding_stop_id,
            "notify_trip_started": user.notify_trip_started,
            "notify_delays": user.notify_delays,
            "notify_admin_broadcasts": user.notify_admin_broadcasts,
        },
    }


@app.get("/api/v1/auth/health")
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
