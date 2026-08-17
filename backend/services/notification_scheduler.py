"""
services/notification_scheduler.py
Background asyncio task that checks the scheduled_notifications table
every 60 seconds and fires any notifications whose send_at has passed.

Schedule for pre-cancelled trips:
  * Immediate  -- fired inline in admin.py when admin clicks Pre-Cancel
  * Day-before -- fires at 06:00 IST (morning) or 15:00 IST (evening)
  * Day-of     -- fires at 06:00 IST (morning) or 15:00 IST (evening)

When a trip is restored, pending rows are marked cancelled=True.
"""
import asyncio
import logging
from datetime import datetime, timezone
from constants import IST_OFFSET

from sqlalchemy import select, update, and_

from database import AsyncSessionLocal
from models.notification import ScheduledNotification
from services.firebase import broadcast_to_all_users

logger = logging.getLogger(__name__)


def _now_ist_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + IST_OFFSET


async def _dispatch_due_notifications():
    async with AsyncSessionLocal() as db:
        try:
            now = _now_ist_naive()
            result = await db.execute(
                select(ScheduledNotification).where(
                    and_(
                        ScheduledNotification.send_at <= now,
                        ScheduledNotification.sent.is_(False),
                        ScheduledNotification.cancelled.is_(False),
                    )
                )
            )
            due = result.scalars().all()
            if not due:
                return
            logger.info("[SCHEDULER] Dispatching %d due notification(s)", len(due))
            sent_ids: list[int] = []

            for notif in due:
                try:
                    await broadcast_to_all_users(
                        db, notif.title, notif.body,
                        {"type": "scheduled_reminder", "trip_id": str(notif.trip_id)},
                    )
                    sent_ids.append(notif.id)
                    logger.info("[SCHEDULER] Sent notif id=%d trip_id=%d", notif.id, notif.trip_id)
                except Exception as exc:
                    logger.error("[SCHEDULER] Failed notif id=%d: %s", notif.id, exc)

            if sent_ids:
                await db.execute(
                    update(ScheduledNotification)
                    .where(ScheduledNotification.id.in_(sent_ids))
                    .values(sent=True)
                )
                await db.commit()
        except Exception as exc:
            logger.error("[SCHEDULER] Dispatch error: %s", exc)


async def notification_scheduler_loop():
    logger.info("[SCHEDULER] Notification scheduler started (interval: 60s)")
    while True:
        try:
            await _dispatch_due_notifications()
        except Exception as exc:
            logger.error("[SCHEDULER] Unhandled error: %s", exc)
        await asyncio.sleep(60)
