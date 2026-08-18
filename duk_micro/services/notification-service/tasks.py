# services/notification-service/tasks.py
import logging
import os
from celery_app import celery_app
from firebase_helper import send_multicast_message

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.send_fcm_notification", bind=True, max_retries=3)
def send_fcm_notification_task(
    self, tokens: list, title: str, body: str, data: dict = None
):
    """Send an FCM multicast push notification. Retries up to 3 times on failure."""
    try:
        if not tokens:
            return "No tokens provided"
        # Firebase allows max 500 tokens per send_each_for_multicast call
        response = send_multicast_message(tokens, title, body, data)
        logger.info(
            "FCM Sent: %d success, %d failures",
            response.success_count,
            response.failure_count,
        )
        return {"success": response.success_count, "failure": response.failure_count}
    except Exception as exc:
        logger.error("FCM Task failed: %s", exc)
        self.retry(exc=exc, countdown=2 ** self.request.retries)


@celery_app.task(name="tasks.create_in_app_notification", bind=True, max_retries=2)
def create_in_app_notification_task(
    self, user_id, title: str, body: str, notif_type: str = "general"
):
    """
    Write an InAppNotification row to the DB synchronously (Celery task).
    user_id=None creates a broadcast notification visible to all users.
    """
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models_local import InAppNotification, Base

    db_url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg", "postgresql")
    if not db_url:
        logger.warning("[TASK] DATABASE_URL not set, skipping in-app notification creation")
        return

    try:
        engine  = create_engine(db_url, pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        with Session() as session:
            uid = uuid.UUID(user_id) if user_id else None
            notif = InAppNotification(
                user_id=uid,
                title=title,
                body=body,
                type=notif_type,
                is_read=False,
            )
            session.add(notif)
            session.commit()
            logger.info("[TASK] Created in-app notification: %s", notif_type)
    except Exception as exc:
        logger.error("[TASK] create_in_app_notification failed: %s", exc)
        self.retry(exc=exc, countdown=5)

