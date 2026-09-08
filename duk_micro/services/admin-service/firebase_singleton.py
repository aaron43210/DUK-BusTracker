# services/admin-service/firebase_singleton.py
"""
Thread-safe Firebase Admin SDK singleton.
Initialize once at startup, not per-request.
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


def get_firebase_app() -> Optional[firebase_admin.App]:
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    with _lock:
        if _firebase_app is not None:
            return _firebase_app

        creds_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        creds_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase_creds.json")

        try:
            if creds_json:
                cred_dict = json.loads(creds_json)
                cred = credentials.Certificate(cred_dict)
            elif os.path.exists(creds_path):
                cred = credentials.Certificate(creds_path)
            else:
                logger.error("[FIREBASE] No credentials found. Push notifications disabled.")
                return None

            _firebase_app = firebase_admin.initialize_app(cred)
            logger.info("[FIREBASE] Initialized successfully")
            return _firebase_app

        except Exception as e:
            logger.error("[FIREBASE] Initialization failed: %s", e)
            return None


async def send_multicast_chunked(
    tokens: list[str],
    title: str,
    body: str,
    data: dict | None = None,
    chunk_size: int = 500,
) -> tuple[int, int]:
    """
    Send FCM multicast, chunked into batches of 500.
    Returns (total_success, total_failure).
    """
    app = get_firebase_app()
    if not app:
        return 0, len(tokens)

    total_success = 0
    total_failure = 0
    loop = asyncio.get_event_loop()

    for i in range(0, len(tokens), chunk_size):
        chunk = tokens[i : i + chunk_size]
        msg = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=chunk,
        )
        try:
            resp = await loop.run_in_executor(
                None,
                messaging.send_each_for_multicast,
                msg,
            )
            total_success += resp.success_count
            total_failure += resp.failure_count
        except Exception as e:
            logger.error("[FIREBASE] Multicast chunk failed: %s", e)
            total_failure += len(chunk)

    return total_success, total_failure
