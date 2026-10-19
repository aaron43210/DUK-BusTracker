#!/bin/bash
# Start and supervise all background workers in a single container

echo "[WORKER-NODE] Starting all background workers..."

start_consumer() {
    python services/notification-service/event_consumer.py &
    PID_CONSUMER=$!
}

start_scheduler() {
    python services/notification-service/scheduled_dispatcher.py &
    PID_SCHEDULER=$!
}

start_celery() {
    # Consume BOTH configured queues explicitly. celery_app routes
    # send_fcm_notification -> "fcm_notifications" and create_in_app_notification
    # -> "celery". Without -Q, a future config change could strand the FCM queue
    # and silently stop all push notifications.
    (cd services/notification-service && celery -A celery_app worker --loglevel=info -c 1 -Q fcm_notifications,celery) &
    PID_CELERY=$!
}

start_lifecycle() {
    python services/trip-lifecycle/consumer.py &
    PID_LIFECYCLE=$!
}

start_ml_trainer() {
    python services/ml-training-job/train_job.py &
    PID_ML=$!
}

start_consumer
start_scheduler
start_celery
start_lifecycle
start_ml_trainer

echo "[WORKER-NODE] All workers started! Entering supervisor monitor loop..."

# Trap termination signals
trap "kill $PID_CONSUMER $PID_SCHEDULER $PID_CELERY $PID_LIFECYCLE $PID_ML 2>/dev/null; exit 0" SIGINT SIGTERM

while true; do
    sleep 5
    if ! kill -0 $PID_CONSUMER 2>/dev/null; then
        echo "[SUPERVISOR] Notification Consumer exited, restarting..."
        start_consumer
    fi
    if ! kill -0 $PID_SCHEDULER 2>/dev/null; then
        echo "[SUPERVISOR] Notification Scheduler exited, restarting..."
        start_scheduler
    fi
    if ! kill -0 $PID_CELERY 2>/dev/null; then
        echo "[SUPERVISOR] Notification Celery Worker exited, restarting..."
        start_celery
    fi
    if ! kill -0 $PID_LIFECYCLE 2>/dev/null; then
        echo "[SUPERVISOR] Trip Lifecycle Consumer exited, restarting..."
        start_lifecycle
    fi
    if ! kill -0 $PID_ML 2>/dev/null; then
        echo "[SUPERVISOR] ML Training Supervisor exited, restarting..."
        start_ml_trainer
    fi
done
