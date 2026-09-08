FROM python:3.11-slim
WORKDIR /app

# libgomp1 is required by lightgbm for OpenMP multi-threading support
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY services/ml-training-job/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY libs/ /app/libs/
ENV PYTHONPATH="${PYTHONPATH}:/app/libs"

COPY services/ml-training-job/ .

CMD ["python", "train_job.py"]
