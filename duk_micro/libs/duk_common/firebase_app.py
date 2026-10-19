# libs/duk_common/firebase_app.py
"""
Single shared Firebase Admin SDK entrypoint for the whole system.

Consolidates what used to be three separate copies (admin-service/firebase_singleton.py,
notification-service/firebase_helper.py, and an inline import in gps-ingestion). Because
mega_gateway runs every service in one process, there must be exactly ONE
firebase_admin app — this module reuses an already-initialised app instead of calling
initialize_app twice (which raises).

Everything here is a no-op when Firebase credentials are absent, so the code is inert
until FIREBASE_CREDENTIALS_JSON / FIREBASE_CREDENTIALS_PATH and FIREBASE_DATABASE_URL
are configured on the server.
"""
import asyncio
import json
import logging
import os
import threading
from typing import Optional

import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_firebase_app: Optional[firebase_admin.App] = None

# Realtime Database path the public website reads (no PII — bus position only).
PUBLIC_BUS_PATH = "live_bus"


def get_firebase_app() -> Optional[firebase_admin.App]:
    """Return the process-wide Firebase app, initialising it once. Returns None if
    no credentials are configured (push + RTDB then silently no-op)."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    with _lock:
        if _firebase_app is not None:
            return _firebase_app

        # Reuse an app another module may already have created (single process).
        if firebase_admin._apps:
            _firebase_app = firebase_admin.get_app()
            return _firebase_app

        creds_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        creds_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase_creds.json")
        try:
            if creds_json:
                cred = credentials.Certificate(json.loads(creds_json))
            elif os.path.exists(creds_path):
                cred = credentials.Certificate(creds_path)
            else:
                logger.error("[FIREBASE] No credentials found. Push + RTDB disabled.")
                return None

            options = {}
            db_url = os.environ.get("FIREBASE_DATABASE_URL")
            if db_url:
                options["databaseURL"] = db_url

            _firebase_app = firebase_admin.initialize_app(cred, options=options or None)
            logger.info("[FIREBASE] Initialized successfully")
            return _firebase_app
        except Exception as e:
            logger.error("[FIREBASE] Initialization failed: %s", e)
            return None


async def publish_public_bus_state(data: dict, path: str = PUBLIC_BUS_PATH) -> None:
    """Best-effort write of a public snapshot to Realtime Database so the public
    website (which no longer hits the JWT-gated API) can render the live view.
    `path` defaults to the fast position node (`live_bus`); the trip/power meta
    snapshot is written to a separate node (e.g. `bus_meta`).
    No-op without Firebase or FIREBASE_DATABASE_URL. Never raises to the caller."""
    if not get_firebase_app() or not os.environ.get("FIREBASE_DATABASE_URL"):
        return
    try:
        from firebase_admin import db
        await asyncio.to_thread(db.reference(path).set, data)
    except Exception as e:
        logger.error("[FIREBASE] RTDB publish failed (%s): %s", path, e)


async def send_multicast_chunked(
    tokens: list[str],
    title: str,
    body: str,
    data: dict | None = None,
    chunk_size: int = 500,
) -> tuple[int, int]:
    """Send an FCM multicast in batches of 500. Returns (success, failure)."""
    app = get_firebase_app()
    if not app:
        return 0, len(tokens)

    total_success = 0
    total_failure = 0
    loop = asyncio.get_running_loop()

    for i in range(0, len(tokens), chunk_size):
        chunk = tokens[i : i + chunk_size]
        msg = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=chunk,
            android=messaging.AndroidConfig(
                notification=messaging.AndroidNotification(default_sound=True)
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))
            ),
        )
        try:
            resp = await loop.run_in_executor(None, messaging.send_each_for_multicast, msg)
            total_success += resp.success_count
            total_failure += resp.failure_count
        except Exception as e:
            logger.error("[FIREBASE] Multicast chunk failed: %s", e)
            total_failure += len(chunk)

    return total_success, total_failure


def send_multicast_message(tokens: list[str], title: str, body: str, data: dict = None):
    """Synchronous single-batch FCM multicast (used by notification-service).
    Returns the SDK response, or a mock with all-failures when Firebase is absent."""
    app = get_firebase_app()
    if not app:
        class _MockResponse:
            success_count = 0
            failure_count = len(tokens)
        return _MockResponse()

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=data or {},
        tokens=tokens,
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(default_sound=True)
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))
        ),
    )
    return messaging.send_each_for_multicast(message)
