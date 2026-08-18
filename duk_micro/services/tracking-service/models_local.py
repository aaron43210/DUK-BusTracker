# services/tracking-service/models_local.py
import uuid
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Date, JSON, BigInteger
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
