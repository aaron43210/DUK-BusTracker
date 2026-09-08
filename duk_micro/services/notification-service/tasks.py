# services/notification-service/tasks.py
"""
Celery tasks — use a module-level engine (created once per worker process).
"""
import logging
import os
import uuid

from celery_app import celery_app

logger = logging.getLogger(__name__)

#  Module-level DB setup (created once per Celery worker process) 
_engine = None
_Session = None


def _get_session():
    """Lazy-initialize SQLAlchemy synchronous engine (once per worker)."""
    global _engine, _Session
    if _Session is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        db_url = os.environ.get("DATABASE_URL", "").replace(
            "postgresql+asyncpg", "postgresql"
        )
        if not db_url:
            raise RuntimeError("DATABASE_URL not set")

        _engine = create_engine(
            db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        _Session = sessionmaker(bind=_engine)

    return _Session()


@celery_app.task(
    name="tasks.send_fcm_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    acks_late=True,  # Only ack after successful processing
)
def send_fcm_notification_task(
    self,
    tokens: list[str],
    title: str,
    body: str,
    data: dict | None = None,
):
    """Send FCM multicast push notification with retry."""
    try:
        if not tokens:
            return {"success": 0, "failure": 0}

        from firebase_helper import send_multicast_message

        response = send_multicast_message(tokens, title, body, data)
        logger.info(
            "[TASK] FCM: %d success, %d failure",
            response.success_count,
            response.failure_count,
        )
        return {
            "success": response.success_count,
            "failure": response.failure_count,
        }
    except Exception as exc:
        logger.error("[TASK] FCM task failed: %s", exc)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


@celery_app.task(
    name="tasks.create_in_app_notification",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def create_in_app_notification_task(
    self,
    user_id: str | None,
    title: str,
    body: str,
    notif_type: str = "general",
):
    """Write an InAppNotification row using the shared module-level session."""
    from models_local import InAppNotification

    try:
        with _get_session() as session:
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
        raise self.retry(exc=exc, countdown=10)
