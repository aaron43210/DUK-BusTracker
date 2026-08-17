"""models/gps.py — GPS log model."""
from sqlalchemy import (
    Column, BigInteger, Float, String, DateTime, Index
)
from sqlalchemy.sql import func
from database import Base


class GpsLog(Base):
    __tablename__ = "gps_realtime"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    server_time = Column("created_at", DateTime(timezone=True), server_default=func.now(), index=True)
    ist_time    = Column(DateTime(timezone=False), nullable=True, index=True)  # IST auto-set by Supabase DEFAULT
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)
    speed       = Column(Float, nullable=True)
    event       = Column(String(30), nullable=True)

    __table_args__ = (
        Index("ix_gps_lat_id",   "lat", "id"),
        Index("ix_gps_ist_lat",  "ist_time", "lat"),
    )
