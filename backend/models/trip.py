"""models/trip.py — Trip model (one record per bus run per day)."""
from sqlalchemy import (
    Column, Integer, String, Date, DateTime, ForeignKey, Text, Boolean, JSON, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Trip(Base):
    __tablename__ = "trips"

    id                  = Column(Integer, primary_key=True, index=True)
    route_id            = Column(Integer, ForeignKey("routes.id"), nullable=False)
    date                = Column(Date, nullable=False)
    # 'forward' = morning (Central Poly → DUK), 'reverse' = evening (DUK → Central Poly)
    direction           = Column(String(10), nullable=False)
    # scheduled | active | completed | cancelled | late
    status              = Column(String(20), default="scheduled", nullable=False)
    late_by_minutes     = Column(Integer, nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    started_at          = Column(DateTime(timezone=True), nullable=True)
    ended_at            = Column(DateTime(timezone=True), nullable=True)
    # Prevents the automated ETA-based late notification from firing more than once per trip
    eta_notif_sent      = Column(Boolean, default=False, nullable=False)
    # List of stop IDs that the bus has physically reached (within 250m) during this trip
    visited_stops       = Column(JSON, default=list, nullable=False)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    route    = relationship("Route", back_populates="trips")

    __table_args__ = (
        Index("ix_trips_date_dir_status", "date", "direction", "status"),
    )
