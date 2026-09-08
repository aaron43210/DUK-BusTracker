# services/admin-service/models_local.py
import uuid
from datetime import date as date_type
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, Date, JSON, BigInteger, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Trip(Base):
    __tablename__ = "trips"
    id                  = Column(Integer, primary_key=True)
    route_id            = Column(Integer, nullable=False)
    bus_id              = Column(Integer, nullable=True)
    date                = Column(Date, nullable=True)
    direction           = Column(String, nullable=False)
    status              = Column(String, nullable=False, default="scheduled")
    started_at          = Column(DateTime(timezone=True), nullable=True)
    ended_at            = Column(DateTime(timezone=True), nullable=True)
    late_by_minutes     = Column(Integer, nullable=True)
    cancellation_reason = Column(String, nullable=True)
    eta_notif_sent      = Column(Boolean, default=False)
    visited_stops       = Column(JSON, nullable=True)


class BusStop(Base):
    __tablename__ = "bus_stops"
    id                    = Column(Integer, primary_key=True)
    route_id              = Column(Integer)
    name                  = Column(String)
    lat                   = Column(Float)
    lon                   = Column(Float)
    order_index           = Column(Integer)
    morning_time          = Column(String, nullable=True)
    evening_time          = Column(String, nullable=True)
    is_morning_origin      = Column(Boolean, default=False)
    is_morning_destination = Column(Boolean, default=False)
    is_evening_origin      = Column(Boolean, default=False)
    is_evening_destination = Column(Boolean, default=False)


class Route(Base):
    __tablename__ = "routes"
    id   = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


class GpsLog(Base):
    __tablename__ = "gps_realtime"
    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)
    speed       = Column(Float, nullable=True)
    event       = Column(String(30), nullable=True)
    ist_time    = Column(DateTime(timezone=False), nullable=True)
    server_time = Column("created_at", DateTime(timezone=True), server_default=func.now())
    trip_id     = Column(Integer, nullable=True)
    gps_time    = Column(DateTime(timezone=True), nullable=True)
    source      = Column(String(20), nullable=True, index=True)


class User(Base):
    __tablename__ = "users"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email            = Column(String(120), unique=True)
    verified         = Column(Boolean, default=False)
    device_token     = Column(String, nullable=True)
    notifications_on = Column(Boolean, default=True)


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


class ScheduledNotification(Base):
    __tablename__ = "scheduled_notifications"
    id        = Column(Integer, primary_key=True, index=True)
    trip_id   = Column(Integer, nullable=True)
    send_at   = Column(DateTime(timezone=False), nullable=False)
    title     = Column(String(200), nullable=False)
    body      = Column(Text, nullable=False)
    sent      = Column(Boolean, default=False)
    cancelled = Column(Boolean, default=False)


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(UUID(as_uuid=True), nullable=True)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    type       = Column(String(50), nullable=False)
    is_read    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    id             = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    ist_time       = Column(DateTime(timezone=False), nullable=True)
    admin_username = Column(String(100), nullable=False, default="admin")
    action         = Column(String(50), nullable=False)
    endpoint       = Column(String(255), nullable=True)
    method         = Column(String(10), nullable=True)
    ip_address     = Column(String(50), nullable=True)
    device_os      = Column(String(100), nullable=True)
    browser        = Column(String(100), nullable=True)
    user_agent     = Column(Text, nullable=True)
    location_info  = Column(JSON, nullable=True)
    changes        = Column(JSON, nullable=True)
    status_code    = Column(Integer, default=200)
    success        = Column(Boolean, default=True)

