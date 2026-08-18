FROM python:3.11-slim
WORKDIR /app

COPY services/notification-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY libs/ /app/libs/
ENV PYTHONPATH="${PYTHONPATH}:/app/libs"

COPY services/notification-service/ .

# We need a way to run both celery and consumer. For simplicity in docker-compose,
# you could define two containers sharing the same image, one running celery worker, 
# one running consumer.py. 
CMD ["celery", "-A", "celery_app", "worker", "--beat", "--loglevel=info"]
