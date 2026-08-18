# services/notification-service/celery_app.py
import os
from celery import Celery

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "notification_tasks",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "tasks.send_fcm_notification": {"queue": "fcm_notifications"},
        "tasks.send_sms": {"queue": "sms_notifications"},
    }
)

celery_app.autodiscover_tasks(["tasks"])
