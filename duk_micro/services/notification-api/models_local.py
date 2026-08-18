# services/notification-api/models_local.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase
import uuid

class Base(DeclarativeBase):
    pass

class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    route_id = Column(Integer)
    boarding_stop_id = Column(Integer)
    destination_stop_id = Column(Integer)
    direction = Column(String)
    fcm_token = Column(String)
    is_active = Column(Boolean)

class AdminBroadcast(Base):
    __tablename__ = "admin_broadcasts"
    id         = Column(Integer, primary_key=True, index=True)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    target     = Column(String(30), default="all")
    sent_at    = Column(DateTime(timezone=True), server_default=func.now())
    sent_count = Column(Integer, default=0)
    success    = Column(Boolean, default=True)

class Suggestion(Base):
    __tablename__ = "suggestions"
    id             = Column(Integer, primary_key=True, index=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    trip           = Column(String(50), nullable=True)
    location       = Column(String(100), nullable=True)
    suggestion     = Column(Text, nullable=False)
    user_id        = Column(UUID(as_uuid=True), nullable=True)
    status         = Column(String(30), default="pending")
    admin_response = Column(Text, nullable=True)

class InAppNotification(Base):
    __tablename__ = "in_app_notifications"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(UUID(as_uuid=True), nullable=True)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    type       = Column(String(50), nullable=False)
    is_read    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
