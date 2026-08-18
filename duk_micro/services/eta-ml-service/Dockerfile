FROM python:3.11-slim
WORKDIR /app
COPY services/eta-ml-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/eta-ml-service /app/services/eta-ml-service
CMD ["uvicorn", "services.eta-ml-service.main:app", "--host", "0.0.0.0", "--port", "8003"]
