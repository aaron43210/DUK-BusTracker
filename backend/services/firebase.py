"""
services/firebase.py — Firebase Cloud Messaging push notifications.
Uses Firebase Admin SDK.
"""
import logging
from typing import Optional
import os
import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)

# Initialize Firebase Admin SDK using the local service account JSON
try:
    cred_path = os.path.join(os.path.dirname(__file__), '..', 'firebase-service-account.json')
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        logger.info("[FCM] Firebase Admin initialized successfully.")
    else:
        logger.warning("[FCM] firebase-service-account.json not found. Push notifications will be disabled.")
except Exception as e:
    logger.error("[FCM] Failed to initialize Firebase Admin: %s", e)


async def send_push_notification(
    device_tokens: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
    urgent: bool = False,
) -> dict:
    """
    Send FCM push notification to a list of device tokens using Firebase Admin SDK.
    """
    if not device_tokens:
        return {"sent": 0, "failed": 0}

    if not firebase_admin._apps:
        logger.warning("[FCM] Firebase Admin not initialized. Would send '%s' to %d devices.", title, len(device_tokens))
        return {"sent": 0, "failed": 0, "mode": "placeholder"}

    # We must stringify data values for Firebase Admin FCM
    fcm_data = {str(k): str(v) for k, v in (data or {}).items()}

    android_config = None
    if urgent:
        android_config = messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                default_sound=True,
                default_vibrate_timings=True,
            ),
        )

    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        data=fcm_data,
        android=android_config,
        tokens=device_tokens,
    )

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        # Run the synchronous network call in a thread pool to avoid blocking the server
        future = loop.run_in_executor(None, messaging.send_each_for_multicast, message)
        # Apply a strict 10-second timeout
        response = await asyncio.wait_for(future, timeout=10.0)

        logger.info(
            "[FCM] Sent %d notifications, %d failed.",
            response.success_count,
            response.failure_count
        )
        return {"sent": response.success_count, "failed": response.failure_count}
    except asyncio.TimeoutError:
        logger.error("[FCM] Push notification request timed out after 10 seconds.")
        return {"sent": 0, "failed": len(device_tokens)}
    except Exception as e:
        logger.error("[FCM] Exception sending push notification: %s", e)
        return {"sent": 0, "failed": len(device_tokens)}


async def broadcast_to_all_users(db, title: str, body: str, data: Optional[dict] = None, urgent: bool = False) -> dict:
    """Fetch all user FCM tokens from DB and broadcast."""
    from sqlalchemy import select
    from models.user import User

    result = await db.execute(
        select(User.device_token).where(
            User.device_token.isnot(None),
            User.notifications_on.is_(True),
            User.verified.is_(True),
        )
    )
    tokens = [row[0] for row in result.fetchall() if row[0]]
    return await send_push_notification(tokens, title, body, data, urgent=urgent)
