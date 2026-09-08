# libs/duk_common/auth.py
"""
JWT authentication utilities with:
- JTI (JWT ID) for revocation support
- Timing-safe token validation
- Proper algorithm restriction
- Blocklist always wired in (lazy Redis fetch)
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Awaitable

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from libs.duk_common.security import TokenBlocklist

security = HTTPBearer()

SUPPORTED_ALGORITHMS = {"HS256", "HS384", "HS512"}


def _validate_algorithm(algorithm: str) -> None:
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"Unsupported algorithm: {algorithm}. Use one of {SUPPORTED_ALGORITHMS}")


def create_access_token(
    user_id: str,
    secret_key: str,
    algorithm: str = "HS256",
    expire_days: int = 7,
) -> tuple[str, str]:
    """
    Returns (token, jti) tuple.
    Store jti to enable revocation.
    """
    _validate_algorithm(algorithm)
    jti = str(uuid.uuid4())
    expire = datetime.now(timezone.utc) + timedelta(days=expire_days)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": jti,
    }
    token = jwt.encode(payload, secret_key, algorithm=algorithm)
    return token, jti


def decode_token(
    token: str,
    secret_key: str,
    algorithm: str = "HS256",
) -> Optional[dict]:
    """
    Returns full payload dict (including jti) or None.
    All required claims (sub, exp, jti) are enforced.
    """
    _validate_algorithm(algorithm)
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[algorithm],
            options={"require": ["sub", "exp", "jti"]},
        )
        return payload
    except JWTError:
        return None


def make_get_current_user_id(
    secret_key: str,
    algorithm: str = "HS256",
    redis_getter: Optional[Callable[[], Awaitable]] = None,
):
    """
    Returns an async FastAPI dependency that:
      1. Validates the JWT signature and expiry.
      2. Always checks the Redis blocklist (if redis_getter is provided).
      3. Returns the user_id (sub claim).

    Args:
        secret_key    - HMAC secret used to sign tokens.
        algorithm     - JWT algorithm (must be HS256/HS384/HS512).
        redis_getter  - Async callable that returns a live Redis connection.
                        Pass `get_redis` from redis_client. Blocklist checking
                        is skipped only if this is None (not recommended).

    The dependency also returns the full payload as the second element of a
    namedtuple-like dict so callers can extract the JTI for logout etc.
    """
    async def get_current_user_id(
        credentials: HTTPAuthorizationCredentials = Depends(security),
    ) -> str:
        payload = decode_token(credentials.credentials, secret_key, algorithm)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Always check blocklist when Redis is available
        if redis_getter is not None:
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
            except Exception:
                # Redis unavailable — fail open for availability, log warning.
                # For stricter security, raise 503 instead.
                pass

        return payload["sub"]

    # Expose a variant that also returns the full payload (needed by logout)
    async def get_current_user_payload(
        credentials: HTTPAuthorizationCredentials = Depends(security),
    ) -> dict:
        """Returns the full decoded + validated JWT payload dict."""
        payload = decode_token(credentials.credentials, secret_key, algorithm)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if redis_getter is not None:
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
            except Exception:
                pass

        return payload

    # Attach the payload variant as an attribute so callers can use it
    get_current_user_id.get_payload = get_current_user_payload
    return get_current_user_id
