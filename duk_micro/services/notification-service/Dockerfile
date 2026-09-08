# services/notification-service/Dockerfile
# FIXED: Separate worker and beat into separate containers
FROM python:3.11-slim

RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

COPY services/notification-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY libs/ /app/libs/
ENV PYTHONPATH="/app/libs"

COPY services/notification-service/ .

# Default CMD is the Celery worker (NOT beat)
# Beat scheduler runs as a separate deployment (see k8s/notification-worker.yaml)
CMD ["celery", "-A", "celery_app", "worker", \
     "--loglevel=info", \
     "--queues=fcm_notifications,celery", \
     "--concurrency=4"]

USER appuser
