# services/auth-service/models_local.py
import uuid
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name             = Column(String(200), nullable=False)
    email            = Column(String(320), unique=True, nullable=False, index=True)
    boarding_stop_id = Column(Integer, nullable=True)
    device_token     = Column(String(512), nullable=True)   # FCM token
    notifications_on = Column(Boolean, default=True)
    verified         = Column(Boolean, default=False)       # True after first OTP verify
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    last_seen        = Column(DateTime(timezone=True), onupdate=func.now())
    
    #  Authentication 
    otp_code         = Column(String(64), nullable=True)
    otp_expires_at   = Column(DateTime(timezone=True), nullable=True)

    #  Per-user proximity alert preferences 
    proximity_alert_enabled = Column(Boolean, default=False)
    boarding_alert_stop_id = Column(Integer, nullable=True)
    destination_alert_stop_id = Column(Integer, nullable=True)
    last_dest_alerted_trip_id = Column(Integer, nullable=True)
    last_alerted_trip_id    = Column(Integer, nullable=True)
