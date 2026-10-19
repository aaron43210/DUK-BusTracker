# services/notification-service/firebase_helper.py
"""Compatibility shim. The implementation now lives in the shared
libs/duk_common/firebase_app.py so the whole system uses ONE Firebase app.
Kept so existing `from firebase_helper import ...` call sites keep working."""
from libs.duk_common.firebase_app import (
    get_firebase_app,
    send_multicast_message,
    send_multicast_chunked,
    publish_public_bus_state,
)

# Explicit re-exports (this module exists only to forward these).
__all__ = [
    "get_firebase_app",
    "send_multicast_message",
    "send_multicast_chunked",
    "publish_public_bus_state",
]
