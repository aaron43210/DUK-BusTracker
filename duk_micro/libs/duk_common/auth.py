# libs/duk_common/auth.py
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

def create_access_token(user_id: str, secret_key: str, algorithm: str = "HS256", expire_days: int = 30) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=expire_days)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, secret_key, algorithm=algorithm)

def decode_token(token: str, secret_key: str, algorithm: str = "HS256") -> str | None:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        return payload.get("sub")
    except JWTError:
        return None

def make_get_current_user_id(secret_key: str, algorithm: str = "HS256"):
    def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
        user_id = decode_token(credentials.credentials, secret_key, algorithm)
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_id
    return get_current_user_id
