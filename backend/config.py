"""
config.py — DUK Bus Tracker
Centralised environment configuration.  All secrets come from .env (never committed).
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://dukbus:dukbus@localhost:5432/dukbus"

    # ── JWT ───────────────────────────────────────────────────────────────────
    SECRET_KEY: str = "REDACTED_SECRET_KEY"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_DAYS: int = 30   # long-lived; OTP-verified once only

    # ── GPS Hardware API key (same as old server) ─────────────────────────────
    GPS_API_KEY: str = "REDACTED_API_KEY"

    # ── Admin ─────────────────────────────────────────────────────────────────
    ADMIN_TOKEN: str    = "CHANGE_ME_ADMIN_TOKEN"   # sent as X-Admin-Token header on every API call
    ADMIN_USERNAME: str = "admin"                   # login username for the React dashboard
    ADMIN_PASSWORD: str = "REDACTED_PASSWORD"                # login password for the React dashboard

    # ── SMTP (fill in when ready) ─────────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "placeholder@gmail.com"
    SMTP_PASSWORD: str = "PLACEHOLDER"
    SMTP_FROM: str = "DUK Bus Tracker <placeholder@gmail.com>"

    # ── Firebase FCM (fill in when ready) ────────────────────────────────────
    FCM_SERVER_KEY: str = "PLACEHOLDER_FCM_SERVER_KEY"
    FIREBASE_PROJECT_ID: str = "PLACEHOLDER_PROJECT_ID"

    # ── Allowed email domain ──────────────────────────────────────────────────
    ALLOWED_EMAIL_DOMAIN: str = "duk.ac.in"

    # ── OTP TTL in seconds ────────────────────────────────────────────────────
    OTP_TTL_SECONDS: int = 600  # 10 minutes

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
