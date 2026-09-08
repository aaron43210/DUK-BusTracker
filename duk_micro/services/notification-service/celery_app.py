# services/notification-service/celery_app.py
import os
from celery import Celery
from kombu import Queue

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

if not redis_url:
    raise RuntimeError("REDIS_URL must be set")

celery_app = Celery(
    "notification_tasks",
    broker=redis_url,
    backend=redis_url,
    include=["tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Worker configuration
    worker_prefetch_multiplier=1,  # Fair task distribution
    task_acks_late=True,           # Only ack after success
    task_reject_on_worker_lost=True,
    worker_max_tasks_per_child=1000,  # Prevent memory leaks
    # Result expiry
    result_expires=3600,
    # Queues
    task_queues=(
        Queue("fcm_notifications", routing_key="fcm"),
        Queue("celery", routing_key="default"),
    ),
    task_routes={
        "tasks.send_fcm_notification": {"queue": "fcm_notifications"},
        "tasks.create_in_app_notification": {"queue": "celery"},
    },
    # Retry configuration
    task_max_retries=3,
    broker_connection_retry_on_startup=True,
    # Security
    task_always_eager=False,
)
