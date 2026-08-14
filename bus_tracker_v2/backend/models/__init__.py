# models/__init__.py — import all models so Alembic can detect them
from .route import Route, BusStop
from .user import User
from .trip import Trip
from .gps import GpsLog
from .notification import AdminBroadcast, Suggestion, ScheduledNotification

__all__ = [
    "Route", "BusStop",
    "User",
    "Trip",
    "GpsLog",
    "AdminBroadcast", "Suggestion", "ScheduledNotification",
]
