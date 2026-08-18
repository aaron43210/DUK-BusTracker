# services/notification-service/scheduled_dispatcher.py
"""
Scheduled Notification Dispatcher — ported from backend/services/notification_scheduler.py.
Polls the scheduled_notifications table every 60 seconds and fires due reminders
via Celery tasks (which then call Firebase and create in-app notifications).
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.." ))
from libs.duk_common.redis_client import close_redis

from database_local import AsyncSessionLocal
from models_local import ScheduledNotification
from tasks import send_fcm_notification_task, create_in_app_notification_task
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 60


async def dispatch_due_notifications():
    """Check scheduled_notifications for rows where send_at <= now and fire them."""
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduledNotification).where(
                ScheduledNotification.send_at <= now,
                ScheduledNotification.sent == False,
                ScheduledNotification.cancelled == False,
            )
        )
        due = result.scalars().all()

        if not due:
            return

        logger.info("[SCHEDULER] Dispatching %d due notifications", len(due))

        # Fetch all verified user tokens once
        from models_local import User
        tokens_result = await db.execute(
            select(User.device_token).where(
                User.email_verified == True,
                User.notifications_on != False,
                User.device_token.isnot(None),
            )
        )
        tokens = [row[0] for row in tokens_result.all() if row[0]]

        for notif in due:
            notif.sent = True
            if tokens:
                for i in range(0, len(tokens), 500):
                    send_fcm_notification_task.delay(tokens[i:i+500], notif.title, notif.body, {"trip_id": str(notif.trip_id or "")})
            create_in_app_notification_task.delay(None, notif.title, notif.body, "scheduled_alert")
            logger.info("[SCHEDULER] Fired notif %d: %s", notif.id, notif.title)

        await db.commit()


async def main():
    logger.info("[SCHEDULER] Starting scheduled notification dispatcher (poll every %ds)...", POLL_INTERVAL_S)
    while True:
        try:
            await dispatch_due_notifications()
        except Exception as exc:
            logger.error("[SCHEDULER] Error in dispatch loop: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
