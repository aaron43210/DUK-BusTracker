FROM python:3.11-slim
WORKDIR /app
COPY services/gps-ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs/duk_common /app/libs/duk_common
COPY services/gps-ingestion /app/services/gps-ingestion
CMD ["uvicorn", "services.gps-ingestion.main:app", "--host", "0.0.0.0", "--port", "8001"]
