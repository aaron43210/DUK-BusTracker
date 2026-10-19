# services/admin-service/firebase_singleton.py
"""Compatibility shim. The implementation now lives in the shared
libs/duk_common/firebase_app.py so the whole system uses ONE Firebase app.
Kept so existing `from firebase_singleton import ...` call sites keep working."""
from libs.duk_common.firebase_app import (
    get_firebase_app,
    send_multicast_chunked,
    send_multicast_message,
    publish_public_bus_state,
)

# Explicit re-exports (this module exists only to forward these).
__all__ = [
    "get_firebase_app",
    "send_multicast_chunked",
    "send_multicast_message",
    "publish_public_bus_state",
]
