from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = BASE_DIR / ".env"

class CommonSettings(BaseSettings):
    DATABASE_URL: str | None = None
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 365
    # Grace window (seconds) during which a just-rotated refresh token still works,
    # so concurrent mobile refreshes don't race each other into a logout. The new
    # token keeps the full 365-day lifetime — sessions stay long-lived.
    REFRESH_TOKEN_GRACE_SECONDS: int = 300  # 5 min — covers iOS/Android background suspend
    GPS_API_KEY: str
    ADMIN_TOKEN: str
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str
    ENVIRONMENT: str = "development"
    # Opt-in for host-network deploys (docker-compose.prod.yml) where Redis and
    # OSRM genuinely run on the same machine. Without it, the production check
    # below rejects loopback URLs and every service fails at import.
    ALLOW_LOOPBACK_SERVICES: bool = False
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    # Gmail API (service account) — set these to use Gmail API instead of SMTP.
    # GMAIL_SERVICE_ACCOUNT_PATH takes priority over SMTP_* when set.
    GMAIL_SERVICE_ACCOUNT_PATH: str = ""
    GMAIL_SENDER_EMAIL: str = ""
    ALLOWED_EMAIL_DOMAINS: str = "duk.ac.in,iiitmk.ac.in"
    ALLOWED_ORIGINS: str = ""
    OTP_TTL_SECONDS: int = 600
    OSRM_URL: str = "http://127.0.0.1:5001"
    GPS_MIN_LAT: float = 8.2
    GPS_MAX_LAT: float = 8.7
    GPS_MIN_LON: float = 76.5
    GPS_MAX_LON: float = 77.3
    
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        extra="ignore",
        case_sensitive=True,
    )


    # Secret validation

    @field_validator("ALGORITHM")
    @classmethod
    def validate_algorithm(cls, value: str) -> str:
        # Restrict to HMAC-SHA families only. This closes the door on an
        # operator misconfiguring the JWT algorithm to something weak or
        # asymmetric (e.g. "none" or an RS*/ES* value the HS* code path can't
        # safely verify with a shared secret).
        allowed = {"HS256", "HS384", "HS512"}
        if value not in allowed:
            raise ValueError(f"ALGORITHM must be one of {sorted(allowed)}")
        return value

    @field_validator("SECRET_KEY", "ADMIN_TOKEN")
    @classmethod
    def validate_secret_length(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError(
                "SECRET_KEY and ADMIN_TOKEN must be at least 32 characters long"
            )
        return value

    @field_validator(
        "GPS_API_KEY",
        "ADMIN_USERNAME",
        "ADMIN_PASSWORD",
    )
    @classmethod
    def validate_required_secret(cls, value: str) -> str:
        if not value:
            raise ValueError("Required configuration value cannot be empty")
        return value

    # Numeric validation

    @field_validator("SMTP_PORT")
    @classmethod
    def validate_smtp_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
        return value

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES", "REFRESH_TOKEN_EXPIRE_DAYS", "OTP_TTL_SECONDS")
    @classmethod
    def validate_positive_lifetime(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Token and OTP lifetimes must be greater than 0")
        return value


    # Network URL validation

    @field_validator("REDIS_URL")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"redis", "rediss"}:
            raise ValueError(
                "REDIS_URL must use redis:// or rediss://"
            )
        if not parsed.netloc:
            raise ValueError("REDIS_URL must contain a valid host")
        return value

    @field_validator("OSRM_URL")
    @classmethod
    def validate_osrm_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                "OSRM_URL must use http:// or https://"
            )
        if not parsed.netloc:
            raise ValueError("OSRM_URL must contain a valid host")
        return value.rstrip("/")

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if not parsed.scheme:
            raise ValueError("DATABASE_URL must contain a valid URL scheme")
        if not parsed.netloc and parsed.scheme not in {"sqlite"}:
            raise ValueError("DATABASE_URL must contain a valid host")
        return value

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    @property
    def allowed_origins_list(self) -> list[str]:
        origins = [
            origin.strip()
            for origin in self.ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]
        for origin in origins:
            if origin == "*":
                raise ValueError(
                    "Wildcard '*' is not allowed in ALLOWED_ORIGINS"
                )
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError(
                    f"Invalid CORS origin scheme: {origin}"
                )
            if not parsed.netloc:
                raise ValueError(
                    f"Invalid CORS origin: {origin}"
                )
            if parsed.path not in {"", "/"} or parsed.params:
                raise ValueError(
                    f"CORS origins must contain only scheme and host: {origin}"
                )
        return origins

    @property
    def allowed_email_domains_list(self) -> list[str]:
        return [
            domain.strip().lower()
            for domain in self.ALLOWED_EMAIL_DOMAINS.split(",")
            if domain.strip()
        ]


    # GPS bounds
  
    @model_validator(mode="after")
    def validate_gps_bounds(self):
        if not -90 <= self.GPS_MIN_LAT <= 90:
            raise ValueError("GPS_MIN_LAT must be between -90 and 90")
        if not -90 <= self.GPS_MAX_LAT <= 90:
            raise ValueError("GPS_MAX_LAT must be between -90 and 90")
        if not -180 <= self.GPS_MIN_LON <= 180:
            raise ValueError("GPS_MIN_LON must be between -180 and 180")
        if not -180 <= self.GPS_MAX_LON <= 180:
            raise ValueError("GPS_MAX_LON must be between -180 and 180")
        if self.GPS_MIN_LAT >= self.GPS_MAX_LAT:
            raise ValueError("GPS_MIN_LAT must be less than GPS_MAX_LAT")
        if self.GPS_MIN_LON >= self.GPS_MAX_LON:
            raise ValueError("GPS_MIN_LON must be less than GPS_MAX_LON")
        return self

    # Environment validation

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        allowed = {"development", "staging", "production"}
        if value not in allowed:
            raise ValueError(
                f"ENVIRONMENT must be one of: {', '.join(sorted(allowed))}"
            )
        return value


    # Production fail-safe

    def _reject_loopback_service_urls(self) -> None:
        if self.REDIS_URL.startswith("redis://localhost"):
            raise ValueError(
                "Production REDIS_URL cannot use localhost"
            )
        if self.REDIS_URL.startswith("redis://127.0.0.1"):
            raise ValueError(
                "Production REDIS_URL cannot use 127.0.0.1"
            )
        if self.REDIS_URL.startswith("redis://[::1]"):
            raise ValueError(
                "Production REDIS_URL cannot use ::1"
            )
        
        if self.OSRM_URL in {
            "http://127.0.0.1:5001",
            "http://localhost:5001",
            "http://[::1]:5001",
        }:
            raise ValueError(
                "Production OSRM_URL cannot use a local/default endpoint"
            )

    @model_validator(mode="after")
    def validate_production_configuration(self):
        if self.ENVIRONMENT != "production":
            return self
            
        if not self.ALLOW_LOOPBACK_SERVICES:
            self._reject_loopback_service_urls()

        if not self.ALLOWED_ORIGINS.strip():
            raise ValueError(
                "ALLOWED_ORIGINS must be configured in production"
            )

        # Admin credentials are compared with constant_time_compare at login, so
        # a weak/default ADMIN_PASSWORD is the whole ballgame for the dashboard.
        # SECRET_KEY/ADMIN_TOKEN already require >=32 chars; hold the admin
        # password to a real minimum and reject obvious defaults in production.
        _weak_admin_passwords = {
            "admin", "password", "passw0rd", "changeme", "change_me",
            "admin123", "administrator", "duk", "dukadmin", "123456", "12345678",
        }
        if len(self.ADMIN_PASSWORD) < 12 or self.ADMIN_PASSWORD.strip().lower() in _weak_admin_passwords:
            raise ValueError(
                "Production ADMIN_PASSWORD must be at least 12 characters and "
                "not a common/default value"
            )

        # Trigger CORS validation during startup rather than lazily
        # when the property is first accessed.
        _ = self.allowed_origins_list
        return self

@lru_cache(maxsize=1)
def get_settings() -> CommonSettings:
    return CommonSettings()
