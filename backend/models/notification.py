"""models/notification.py — Admin broadcast log + scheduled notification jobs."""
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from database import Base


class AdminBroadcast(Base):
    __tablename__ = "admin_broadcasts"

    id         = Column(Integer, primary_key=True, index=True)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    # none | all | morning_users | evening_users
    target     = Column(String(30), default="all")
    sent_at    = Column(DateTime(timezone=True), server_default=func.now())
    sent_count = Column(Integer, default=0)
    success    = Column(Boolean, default=True)


class ScheduledNotification(Base):
    """
    Stores future push notifications that must fire at a specific time.
    Used for pre-cancellation reminders:
      - Day before the trip (morning: 06:00 IST, evening: 15:00 IST)
      - Day of the trip (same times)
    A background task polls this table every 60 s and dispatches due rows.
    Rows are voided (cancelled=True) when the parent trip is restored.
    """
    __tablename__ = "scheduled_notifications"

    id         = Column(Integer, primary_key=True, index=True)
    # FK to trips table — used to void jobs when a trip is restored
    trip_id    = Column(Integer, ForeignKey("trips.id", ondelete="CASCADE"), nullable=False)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    # When to fire this notification (UTC datetime)
    send_at    = Column(DateTime, nullable=False)
    sent       = Column(Boolean, default=False)     # True after dispatch
    cancelled  = Column(Boolean, default=False)     # True when trip is restored
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Suggestion(Base):
    __tablename__ = "suggestions"

    id             = Column(Integer, primary_key=True, index=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    trip           = Column(String(50), nullable=True)
    location       = Column(String(100), nullable=True)
    suggestion     = Column(Text, nullable=False)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status         = Column(String(30), default="pending")  # pending, approved, will_consider, rejected
    admin_response = Column(Text, nullable=True)
