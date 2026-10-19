# libs/duk_common/auth.py
"""
JWT authentication utilities with:
- JTI (JWT ID) for revocation support
- Timing-safe token validation
- Proper algorithm restriction
- Blocklist always wired in (lazy Redis fetch)
"""
import os
import uuid
import logging
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Awaitable
from enum import Enum
from dataclasses import dataclass

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from libs.duk_common.security import TokenBlocklist

logger = logging.getLogger(__name__)

class TokenRevocationMode(str, Enum):
    FAIL_OPEN = "fail_open"    # Default: allow if Redis down (availability)
    FAIL_CLOSED = "fail_closed" # Strict: deny if Redis down (security)

def _get_revocation_mode() -> TokenRevocationMode:
    mode_str = os.environ.get("TOKEN_REVOCATION_MODE", "fail_closed").strip().lower()
    try:
        return TokenRevocationMode(mode_str)
    except ValueError:
        raise ValueError("TOKEN_REVOCATION_MODE must be one of: fail_open, fail_closed")

_REVOCATION_MODE = _get_revocation_mode()

@dataclass
class AuthDependencies:
    """Typed container for authentication FastAPI dependencies."""
    get_user_id: Callable
    get_payload: Callable

security = HTTPBearer(auto_error=False)

SUPPORTED_ALGORITHMS = {"HS256", "HS384", "HS512"}

# Name of the HttpOnly cookie the admin dashboard login sets (see
# admin-service: response.set_cookie(key="admin_session", ...)). Since the admin
# JWT is no longer readable by JS, the dashboard can't attach it as an
# `Authorization: Bearer` header to the shared tracking endpoints (/latest,
# /stops, /trip_state, ...). The browser DOES send this cookie (same origin,
# credentials:'include'), so we accept it as a token source here. It's signed
# with the same SECRET_KEY and carries sub/exp/jti like any access token.
ADMIN_SESSION_COOKIE = "admin_session"


def _validate_algorithm(algorithm: str) -> None:
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"Unsupported algorithm: {algorithm}. Use one of {SUPPORTED_ALGORITHMS}")


def _validate_secret(secret_key: str) -> None:
    if not secret_key or not isinstance(secret_key, str) or len(secret_key) < 16:
        raise ValueError("A strong secret_key (>=16 chars) must be provided")


def create_access_token(
    user_id: str,
    secret_key: str,
    algorithm: str = "HS256",
    expire_minutes: int = 15,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
) -> tuple[str, str]:
    """
    Returns (token, jti) tuple. expire_minutes defaults to 15 for short-lived access tokens.
    Store jti to enable revocation.
    """
    _validate_algorithm(algorithm)
    _validate_secret(secret_key)

    if expire_minutes <= 0:
        raise ValueError("expire_minutes must be a positive integer")

    jti = str(uuid.uuid4())
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": jti,
    }

    if issuer:
        payload["iss"] = issuer
    if audience:
        payload["aud"] = audience

    token = jwt.encode(payload, secret_key, algorithm=algorithm)
    return token, jti


def create_refresh_token(expire_days: int = 365) -> tuple[str, str, datetime]:
    """
    Generate a cryptographically secure refresh token.
    Returns (plaintext_token, sha256_hash, expires_at).
    - Store the HASH in the database, never the plaintext.
    - Send the plaintext to the client.
    """
    if expire_days <= 0:
        raise ValueError("expire_days must be a positive integer")

    plaintext = secrets.token_urlsafe(64)  # 512 bits of entropy
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)
    return plaintext, token_hash, expires_at


def decode_token(
    token: str,
    secret_key: str,
    algorithm: str = "HS256",
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
) -> Optional[dict]:
    """
    Returns full payload dict (including jti) or None.
    All required claims (sub, exp, jti) are enforced.
    """
    _validate_algorithm(algorithm)
    _validate_secret(secret_key)
    
    decode_kwargs = {
        "algorithms": [algorithm],
        "options": {"require": ["sub", "exp", "jti"]},
    }
    
    if issuer:
        decode_kwargs["issuer"] = issuer
        decode_kwargs["options"]["require"].append("iss")
    if audience:
        decode_kwargs["audience"] = audience
        decode_kwargs["options"]["require"].append("aud")
        
    try:
        payload = jwt.decode(token, secret_key, **decode_kwargs)
        return payload
    except JWTError as e:
        logger.debug("[AUTH] JWT validation failed: %s", e)
        return None


def make_get_current_user_id(
    secret_key: str,
    redis_getter: Callable[[], Awaitable],
    algorithm: str = "HS256",
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
) -> AuthDependencies:
    """
    Returns an async FastAPI dependency that:
      1. Validates the JWT signature and expiry.
      2. Always checks the Redis blocklist (requires redis_getter).
      3. Returns the user_id (sub claim).

    Args:
        secret_key    - HMAC secret used to sign tokens.
        redis_getter  - Async callable that returns a live Redis connection.
        algorithm     - JWT algorithm (must be HS256/HS384/HS512).
        issuer        - Optional expected issuer.
        audience      - Optional expected audience.
    """
    _validate_secret(secret_key)
    _validate_algorithm(algorithm)
    
    if redis_getter is None:
        raise ValueError("redis_getter is required to enable JWT revocation checks")

    async def _validate_and_get_payload(token: str) -> dict:
        """Shared helper: decode token + check blocklist. Returns full payload."""
        payload = decode_token(
            token,
            secret_key,
            algorithm,
            issuer=issuer,
            audience=audience
        )
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            redis = await redis_getter()
            bl = TokenBlocklist(redis)
            jti = payload.get("jti", "")
            if jti and await bl.is_revoked(jti):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception as e:
            if _REVOCATION_MODE == TokenRevocationMode.FAIL_CLOSED:
                logger.error("[AUTH] Redis unavailable — denying request (fail_closed mode): %s", e)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication service temporarily unavailable",
                )
            else:
                logger.warning(
                    "[AUTH] Redis unavailable — allowing request (fail_open mode). "
                    "Revoked tokens may be accepted. Error: %s", e
                )

        return payload

    def _extract_token(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials],
    ) -> str:
        """Token from the Authorization: Bearer header, else the admin_session cookie."""
        if credentials and credentials.credentials:
            return credentials.credentials
        cookie_token = request.cookies.get(ADMIN_SESSION_COOKIE)
        if cookie_token:
            return cookie_token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async def get_current_user_id(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> str:
        payload = await _validate_and_get_payload(_extract_token(request, credentials))
        return payload["sub"]

    async def get_current_user_payload(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> dict:
        """Returns the full decoded + validated JWT payload dict."""
        return await _validate_and_get_payload(_extract_token(request, credentials))

    return AuthDependencies(
        get_user_id=get_current_user_id,
        get_payload=get_current_user_payload,
    )
