# libs/duk_common/settings.py
import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import field_validator

# Resolve absolute path to duk_micro directory (3 levels up from libs/duk_common/settings.py)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = BASE_DIR / ".env"


class CommonSettings(BaseSettings):
    DATABASE_URL: str | None = None
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_DAYS: int = 7  # Reduced from 30

    GPS_API_KEY: str
    ADMIN_TOKEN: str
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str  # Used only for admin login endpoint

    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    ALLOWED_EMAIL_DOMAIN: str = "duk.ac.in"
    ALLOWED_ORIGINS: str = ""  # Comma-separated list of allowed CORS origins

    OTP_TTL_SECONDS: int = 600

    OSRM_URL: str = "http://127.0.0.1:5001"

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parsed, whitespace-trimmed, empty-filtered CORS origin list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    # Minimum secret key length enforcement
    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return v

    @field_validator("ADMIN_TOKEN")
    @classmethod
    def validate_admin_token(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("ADMIN_TOKEN must be at least 32 characters long")
        return v

    model_config = {
        "env_file": str(ENV_FILE_PATH),
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> CommonSettings:
    return CommonSettings()
