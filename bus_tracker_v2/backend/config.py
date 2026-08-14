import os
from pydantic_settings import BaseSettings
from functools import lru_cache

ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── JWT ───────────────────────────────────────────────────────────────────
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_DAYS: int = 30   # long-lived; OTP-verified once only

    # ── GPS Hardware API key (same as old server) ─────────────────────────────
    GPS_API_KEY: str

    # ── Admin ─────────────────────────────────────────────────────────────────
    ADMIN_TOKEN: str
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str

    # ── SMTP (fill in when ready) ─────────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str
    SMTP_PASSWORD: str
    SMTP_FROM: str


    # ── Allowed email domain ──────────────────────────────────────────────────
    ALLOWED_EMAIL_DOMAIN: str = "duk.ac.in"

    # ── OTP TTL in seconds ────────────────────────────────────────────────────
    OTP_TTL_SECONDS: int = 600  # 10 minutes

    class Config:
        env_file = ENV_FILE_PATH
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()

