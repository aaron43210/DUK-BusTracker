# services/notification-service/event_consumer.py
"""
Notification event consumer — listens to Redis Streams and dispatches FCM / in-app notifications.

Streams consumed:
  notifications.proximity  — bus approaching a user's stop
  notifications.late_eta   — bus running > 10 min late
  notifications.trip_started — trip just moved from scheduled → on_trip
  notifications.scheduled  — pre-cancel reminders scheduled by admin
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.redis_client import get_redis, close_redis
from libs.duk_common.events import EventBus

from tasks import send_fcm_notification_task, create_in_app_notification_task
from database_local import AsyncSessionLocal
from models_local import User, InAppNotification
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_proximity_alert(event_data: dict):
    logger.info("[NOTIF] Proximity alert: %s", event_data)
    alert_type = event_data.get("alert_type")
    tokens     = event_data.get("user_tokens", [])
    stop_name  = event_data.get("stop_name", "your stop")
    trip_id    = event_data.get("trip_id")

    if not tokens:
        return

    if alert_type == "boarding":
        title = "Bus is Approaching!"
        body  = f"Your bus is nearing {stop_name}. Please be ready to board."
    else:
        title = "Destination Approaching!"
        body  = f"Your bus is nearing {stop_name}. Get ready to alight."

    send_fcm_notification_task.delay(tokens, title, body, {"trip_id": str(trip_id)})


async def handle_late_eta(event_data: dict):
    logger.info("[NOTIF] ETA late event: %s", event_data)
    tokens      = event_data.get("user_tokens", [])
    delay_min   = event_data.get("delay_min", 0)
    arrival_str = event_data.get("arrival_str", "")
    trip_id     = event_data.get("trip_id")

    if not tokens:
        return

    title = "Bus Running Late"
    body  = f"The bus will be approximately {int(delay_min)} minutes late. Expected at {arrival_str}."
    send_fcm_notification_task.delay(tokens, title, body, {"trip_id": str(trip_id), "type": "late_eta"})


async def handle_trip_started(event_data: dict):
    """Fan-out trip-started notification to ALL verified users."""
    logger.info("[NOTIF] Trip started: %s", event_data)
    trip_id   = event_data.get("trip_id")
    direction = event_data.get("direction", "forward")

    dir_str = "Morning" if direction == "forward" else "Evening"
    title   = f"{dir_str} Bus Trip Started"
    body    = "The bus has started its journey. Track it live in the app."

    async with AsyncSessionLocal() as db:
        # Fetch all verified users who have notifications enabled and have a device token
        result = await db.execute(
            select(User).where(
                User.email_verified == True,
                User.notifications_on != False,
                User.device_token.isnot(None),
            )
        )
        users  = result.scalars().all()
        tokens = [u.device_token for u in users if u.device_token]

        # Write one InAppNotification row (broadcast — no specific user_id)
        create_in_app_notification_task.delay(
            None,   # user_id=None means broadcast
            title,
            body,
            "trip_started",
        )

    if tokens:
        # Chunk into batches of 500 (Firebase limit)
        for i in range(0, len(tokens), 500):
            chunk = tokens[i:i + 500]
            send_fcm_notification_task.delay(chunk, title, body, {"trip_id": str(trip_id), "type": "trip_started"})

    logger.info("[NOTIF] Trip-started FCM dispatched to %d devices", len(tokens))


async def handle_scheduled_notification(event_data: dict):
    """Fire a pre-cancel reminder that was scheduled by the admin service."""
    logger.info("[NOTIF] Scheduled notification due: %s", event_data)
    notif_id = event_data.get("notif_id")
    title    = event_data.get("title", "Trip Update")
    body     = event_data.get("body", "")
    trip_id  = event_data.get("trip_id")

    # Fetch tokens for all verified, notifications-on users
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(
                User.email_verified == True,
                User.notifications_on != False,
                User.device_token.isnot(None),
            )
        )
        users  = result.scalars().all()
        tokens = [u.device_token for u in users if u.device_token]

    if tokens:
        for i in range(0, len(tokens), 500):
            send_fcm_notification_task.delay(tokens[i:i+500], title, body, {"trip_id": str(trip_id)})

    # Write in-app record
    create_in_app_notification_task.delay(None, title, body, "scheduled_alert")
    logger.info("[NOTIF] Scheduled notif %s dispatched to %d devices", notif_id, len(tokens))


# ── Entry Point ───────────────────────────────────────────────────────────────

async def main():
    redis = await get_redis()
    bus   = EventBus(redis)
    logger.info("[NOTIF] Starting notification event consumer (all streams)...")

    streams = [
        ("notifications.proximity",   "notif-proximity-group",  "worker-1", handle_proximity_alert),
        ("notifications.late_eta",    "notif-late-eta-group",   "worker-1", handle_late_eta),
        ("notifications.trip_started","notif-trip-start-group", "worker-1", handle_trip_started),
        ("notifications.scheduled",   "notif-scheduled-group",  "worker-1", handle_scheduled_notification),
    ]

    tasks = [
        asyncio.create_task(
            bus.consume(stream=s, group=g, consumer=c, handler=h, batch_size=50)
        )
        for s, g, c, h in streams
    ]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        for t in tasks:
            t.cancel()
    finally:
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())

