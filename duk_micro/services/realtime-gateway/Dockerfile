FROM python:3.11-slim
WORKDIR /app
COPY services/realtime-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs/duk_common /app/libs/duk_common
COPY services/realtime-gateway /app/services/realtime-gateway
CMD ["uvicorn", "services.realtime-gateway.main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
