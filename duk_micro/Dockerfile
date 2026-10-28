FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (build-essential for numpy/pandas if needed, though wheels usually suffice)
ENV DEBIAN_FRONTEND=noninteractive
ENV TERM=xterm
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --default-timeout=1000 --retries 10 --no-cache-dir -r requirements.txt

# Copy all source code
COPY . .

# Set PYTHONPATH so absolute imports work
ENV PYTHONPATH=/app

# ── Run as a non-root user ──────────────────────────────────────────────────
# Create an unprivileged user and give it ownership of the writable paths the
# app uses at runtime: /app/logs (audit logs) and /app/models (ML models).
# Named volumes mounted here inherit this ownership on first creation; a bind
# mount for ./models must be made writable by uid 1000 on the host.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/logs /app/models \
    && chown -R appuser:appuser /app/logs /app/models
USER appuser

# Expose port (used by mega_gateway)
EXPOSE 8000

# Default command (can be overridden by docker-compose for workers)
CMD ["uvicorn", "mega_gateway:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "10800", "--ws-ping-timeout", "10800"]
