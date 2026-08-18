FROM python:3.11-slim
WORKDIR /app
COPY services/eta-ml-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt psycopg2-binary
COPY ml-training-job /app/ml-training-job
CMD ["python", "ml-training-job/train_job.py"]
