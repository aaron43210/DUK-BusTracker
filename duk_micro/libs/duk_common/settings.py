# libs/duk_common/settings.py
import os
from pydantic_settings import BaseSettings
from functools import lru_cache

class CommonSettings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_DAYS: int = 30
    GPS_API_KEY: str
    ADMIN_TOKEN: str
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    ALLOWED_EMAIL_DOMAIN: str = "duk.ac.in"
    OTP_TTL_SECONDS: int = 600
    OSRM_URL: str = "http://127.0.0.1:5001"

    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache()
def get_settings() -> CommonSettings:
    return CommonSettings()
