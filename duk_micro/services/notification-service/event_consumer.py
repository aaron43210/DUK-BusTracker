# services/notification-service/event_consumer.py
"""
Notification event consumer — listens to Redis Streams and dispatches FCM / in-app notifications.

Streams consumed:
  notifications.proximity  — bus approaching a user's stop
  notifications.late_eta   — bus running > 10 min late
  notifications.trip_started — trip just moved from scheduled → on_trip

(Pre-scheduled reminders are dispatched directly via Celery by
scheduled_dispatcher.py — there is no "notifications.scheduled" stream.)
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.redis_client import get_redis, close_redis
from libs.duk_common.events import EventBus, ProximityAlertEvent, EtaLateEvent, TripStartedEvent

from tasks import send_fcm_notification_task, create_in_app_notification_task
from database_local import AsyncSessionLocal
from libs.duk_common.models import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


#  Handlers 



async def handle_proximity_alert(event: ProximityAlertEvent):
    """Handle proximity alerts for buses.

    Sends FCM notifications to users whose stops are approaching.

    Args:
        event: ProximityAlertEvent containing alert information.

    Raises:
        Exception: Logs any errors encountered during processing.
    """
    try:
        logger.info("[NOTIF] Proximity alert: %s", event)
        alert_type = event.alert_type
        tokens     = event.user_tokens
        stop_name  = event.stop_name or "your stop"
        trip_id    = event.trip_id

        if not tokens:
            return

        if alert_type == "boarding":
            title = "Bus Approaching!"
            body  = f"Your bus is nearing {stop_name}. Please head to the stop and be ready to board."
        else:
            title = "Destination Approaching!"
            body  = f"Your bus is nearing {stop_name}. Please gather your belongings and prepare to alight."

        send_fcm_notification_task.delay(tokens, title, body, {"trip_id": str(trip_id)})
    except Exception as e:
        logger.exception("[NOTIF] handle_proximity_alert crashed: %s", e)


async def handle_late_eta(event: EtaLateEvent):
    """Handle late ETA alerts for buses.

    Sends FCM notifications to users when a bus is running late.

    Args:
        event: EtaLateEvent containing alert information.

    Raises:
        Exception: Logs any errors encountered during processing.
    """
    try:
        logger.info("[NOTIF] ETA late event: %s", event)
        tokens      = event.user_tokens
        delay_min   = event.delay_min
        arrival_str = event.arrival_str
        trip_id     = event.trip_id

        if not tokens:
            return

        title = "Bus Delayed"
        body  = f"Your bus is running ~{int(delay_min)} mins late due to traffic. New ETA is {arrival_str}."
        send_fcm_notification_task.delay(tokens, title, body, {"trip_id": str(trip_id), "type": "late_eta"})
    except Exception as e:
        logger.exception("[NOTIF] handle_late_eta crashed: %s", e)


async def handle_trip_started(event: TripStartedEvent):
    """Handle trip-started events.

    Sends push notifications only to users who have notify_trip_started enabled.
    Always creates an in-app notification for all users regardless of this setting.

    Args:
        event: TripStartedEvent containing trip information.

    Raises:
        Exception: Logs any errors encountered during processing.
    """
    try:
        logger.info("[NOTIF] Trip started: %s", event)
        trip_id   = event.trip_id
        direction = event.direction

        dir_str = "Morning" if direction == "forward" else "Evening"
        title   = f"{dir_str} Bus Trip Started!"
        body    = "The bus is now on the move. Tap to track it live in the app."

        from libs.duk_common.notification_helpers import get_notification_tokens
        async with AsyncSessionLocal() as db:
            # Only send push to users who have trip-started notifications enabled
            tokens = await get_notification_tokens(db, pref_column=User.notify_trip_started)

            # In-app notification is always created, regardless of push preference
            create_in_app_notification_task.delay(None, title, body, "trip_started")

        if tokens:
            for i in range(0, len(tokens), 500):
                chunk = tokens[i:i + 500]
                send_fcm_notification_task.delay(chunk, title, body, {"trip_id": str(trip_id), "type": "trip_started"})

        logger.info("[NOTIF] Trip-started FCM dispatched to %d devices", len(tokens))
    except Exception as e:
        logger.exception("[NOTIF] handle_trip_started crashed: %s", e)



#  Entry Point

async def main():
    redis = await get_redis()
    bus   = EventBus(redis)
    logger.info("[NOTIF] Starting notification event consumer (all streams)...")

    # Use process PID to create a unique consumer ID per instance.
    # This prevents duplicate processing if multiple instances run simultaneously.
    consumer_id = f"worker-{os.getpid()}"

    streams = [
        ("notifications.proximity",    "notif-proximity-group",  consumer_id, handle_proximity_alert, ProximityAlertEvent),
        ("notifications.late_eta",     "notif-late-eta-group",   consumer_id, handle_late_eta, EtaLateEvent),
        ("notifications.trip_started", "notif-trip-start-group", consumer_id, handle_trip_started, TripStartedEvent),
    ]

    tasks = [
        asyncio.create_task(
            bus.consume(stream=s, group=g, consumer=c, handler=h, batch_size=50, model_cls=cls)
        )
        for s, g, c, h, cls in streams
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

