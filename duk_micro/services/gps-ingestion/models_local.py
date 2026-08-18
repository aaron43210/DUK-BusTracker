# services/gps-ingestion/models_local.py
"""Minimal models needed by gps-ingestion-service."""
from sqlalchemy import Column, BigInteger, Float, String, DateTime, Integer
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class GpsLog(Base):
    __tablename__ = "gps_realtime"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    server_time = Column("created_at", DateTime(timezone=True), server_default=func.now(), index=True)
    ist_time = Column(DateTime(timezone=False), nullable=True, index=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    speed = Column(Float, nullable=True)
    event = Column(String(30), nullable=True)
    gps_time = Column(DateTime(timezone=True), nullable=True)
    trip_id = Column(Integer, nullable=True)
