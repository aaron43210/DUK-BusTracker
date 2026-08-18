# services/notification-service/models_local.py
import uuid
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    """Minimal User projection — only columns needed to fan-out notifications."""
    __tablename__ = "users"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email            = Column(String(120), unique=True, nullable=False)
    email_verified   = Column(Boolean, default=False)
    device_token     = Column(String, nullable=True)       # FCM token
    notifications_on = Column(Boolean, default=True)


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(UUID(as_uuid=True), nullable=True)   # None = broadcast
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    type       = Column(String(50), nullable=False)
    is_read    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ScheduledNotification(Base):
    """Pre-cancel reminder notifications scheduled by the admin service."""
    __tablename__ = "scheduled_notifications"
    id         = Column(Integer, primary_key=True, index=True)
    trip_id    = Column(Integer, nullable=True)
    send_at    = Column(DateTime(timezone=True), nullable=False)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    sent       = Column(Boolean, default=False)
    cancelled  = Column(Boolean, default=False)
