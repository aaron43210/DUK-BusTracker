FROM python:3.11-slim
WORKDIR /app

COPY services/trip-lifecycle/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared library
COPY libs/ /app/libs/
ENV PYTHONPATH="${PYTHONPATH}:/app/libs"

COPY services/trip-lifecycle/ .

RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["python", "consumer.py"]
