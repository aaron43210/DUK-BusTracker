FROM python:3.11-slim

# Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

COPY services/eta-ml-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/eta-ml-service/ /app/

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]

USER appuser
