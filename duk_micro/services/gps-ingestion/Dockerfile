# services/gps-ingestion/Dockerfile (FIXED)
FROM python:3.11-slim AS builder

# Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

COPY services/gps-ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY libs/ /app/libs/
COPY services/gps-ingestion/ /app/

# FIXED: Correct module path (no hyphens, working directory is /app)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", \
     "--no-server-header", "--workers", "1"]

USER appuser
