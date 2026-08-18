# services/trip-lifecycle/models_local.py
import uuid
from datetime import date
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
    # Status values: "scheduled" | "on_trip" | "late" | "completed" | "cancelled" | "active"
    status              = Column(String, nullable=False, default="scheduled")
    started_at          = Column(DateTime(timezone=True), nullable=True)
    ended_at            = Column(DateTime(timezone=True), nullable=True)
    late_by_minutes     = Column(Integer, nullable=True)
    cancellation_reason = Column(String, nullable=True)
    eta_notif_sent      = Column(Boolean, default=False)
    visited_stops       = Column(JSON, nullable=True)       # list of stop IDs visited


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


class UserNotificationPreference(Base):
    """New microservices preference table (distinct from users table columns)."""
    __tablename__ = "user_notification_preferences"
    id                  = Column(Integer, primary_key=True)
    user_id             = Column(Integer, nullable=True)
    route_id            = Column(Integer)
    boarding_stop_id    = Column(Integer)
    destination_stop_id = Column(Integer)
    direction           = Column(String)
    fcm_token           = Column(String)
    is_active           = Column(Boolean, default=True)


class User(Base):
    """Minimal projection of the users table — used to look up FCM tokens on trip start."""
    __tablename__ = "users"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_verified   = Column(Boolean, default=False)
    device_token     = Column(String, nullable=True)
    notifications_on = Column(Boolean, default=True)
    # Monolith per-user proximity preference columns
    proximity_alert_enabled   = Column(Boolean, default=False)
    boarding_alert_stop_id    = Column(Integer, nullable=True)
    destination_alert_stop_id = Column(Integer, nullable=True)
    last_alerted_trip_id      = Column(Integer, nullable=True)


class GpsLog(Base):
    """Minimal projection of gps_realtime — used to look up last position on POWER_OFF."""
    __tablename__ = "gps_realtime"
    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)
    speed       = Column(Float, nullable=True)
    server_time = Column("created_at", DateTime(timezone=True), server_default=func.now())
    trip_id     = Column(Integer, nullable=True)

