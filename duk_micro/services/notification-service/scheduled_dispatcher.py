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
from database_local import AsyncSessionLocal
from libs.duk_common.models import ScheduledNotification
from tasks import send_fcm_notification_task, create_in_app_notification_task
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 60


async def dispatch_due_notifications():
    """Check scheduled_notifications for rows where send_at <= now and fire them.

    Raises:
        Exception: Logs any errors encountered during processing.
    """
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduledNotification).where(
                ScheduledNotification.send_at <= now,
                ScheduledNotification.status == "pending",
            )
        )
        due = result.scalars().all()

        if not due:
            return

        logger.info("[SCHEDULER] Dispatching %d due notifications", len(due))

        from libs.duk_common.notification_helpers import get_notification_tokens
        from libs.duk_common.models import User
        # Respect the per-user admin-broadcast opt-out, consistent with the
        # notifications.* stream consumer (handle_scheduled_notification used
        # User.notify_admin_broadcasts). Without this, pre-cancel reminders were
        # pushed even to users who had turned admin broadcasts off.
        tokens = await get_notification_tokens(db, pref_column=User.notify_admin_broadcasts)

        for notif in due:
            notif.status = "sent"
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
