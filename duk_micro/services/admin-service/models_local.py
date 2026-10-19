"""
Service-local ORM facade.
The actual ORM models remain defined in the shared ORM module.
This facade provides a stable service-local import boundary.
"""
from libs.duk_common.models import (
    Base,
    Trip,
    BusStop,
    Route,
    GpsLog,
    GpsRealtime,
    User,
    AdminBroadcast,
    Suggestion,
    ScheduledNotification,
    InAppNotification,
    AdminAuditLog,
    UserNotificationPreference,
)

__all__ = [
    "Base",
    "Trip",
    "BusStop",
    "Route",
    "GpsLog",
    "GpsRealtime",
    "User",
    "AdminBroadcast",
    "Suggestion",
    "ScheduledNotification",
    "InAppNotification",
    "AdminAuditLog",
    "UserNotificationPreference",
]
