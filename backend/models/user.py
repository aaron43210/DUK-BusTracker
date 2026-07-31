"""models/user.py \u2014 User model (email-verified, no password)."""
import uuid
from sqlalchemy import (
    Column, String, Integer, ForeignKey, DateTime, Boolean
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name             = Column(String(200), nullable=False)
    email            = Column(String(320), unique=True, nullable=False, index=True)
    boarding_stop_id = Column(Integer, ForeignKey("bus_stops.id"), nullable=True)
    device_token     = Column(String(512), nullable=True)   # FCM token
    notifications_on = Column(Boolean, default=True)
    verified         = Column(Boolean, default=False)       # True after first OTP verify
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    last_seen        = Column(DateTime(timezone=True), onupdate=func.now())
    
    # ── Authentication ────────────────────────────────────────────────────────
    otp_code         = Column(String(10), nullable=True)
    otp_expires_at   = Column(DateTime(timezone=True), nullable=True)

    # ── Per-user proximity alert preferences ──────────────────────────────────
    # Whether the user has opted into proximity alerts for their stop
    proximity_alert_enabled = Column(Boolean, default=False)
    # Trigger type: 'time' (minutes), 'distance' (metres by road), 'stops' (stop count)
    proximity_alert_type    = Column(String(20), nullable=True)
    # Trigger value: e.g., 5 for 5 minutes / 1000 for 1 km / 2 for 2 stops away
    proximity_alert_value   = Column(Integer, nullable=True)
    # Which stop to alert for: 'source' (boarding) | 'destination' | 'both'
    proximity_alert_for     = Column(String(20), default="source")
    # Tracks the last trip we already fired the proximity alert for (prevents spam)
    last_alerted_trip_id    = Column(Integer, ForeignKey("trips.id"), nullable=True)

    boarding_stop = relationship("BusStop", back_populates="users")
