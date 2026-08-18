FROM python:3.11-slim
WORKDIR /app

COPY services/auth-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY libs/ /app/libs/
ENV PYTHONPATH="${PYTHONPATH}:/app/libs"

COPY services/auth-service/ .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
