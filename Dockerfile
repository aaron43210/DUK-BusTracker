FROM ubuntu:22.04

# Prevent tzdata and other interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Kolkata

# Install System Dependencies
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3-venv \
    postgresql \
    postgresql-contrib \
    redis-server \
    supervisor \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy all source code
COPY duk_micro /app/duk_micro
COPY start.sh /app/start.sh
COPY supervisord.conf /app/supervisord.conf

# Make start script executable
RUN chmod +x /app/start.sh

# Install all Python dependencies from every microservice
RUN find /app/duk_micro -name "requirements.txt" -exec pip3 install --no-cache-dir -r {} \;

# Also install uvicorn, fastapi, httpx (needed for proxy if any), starlette
RUN pip3 install --no-cache-dir uvicorn fastapi httpx starlette asyncpg

# Set Local Environment Variables
ENV DATABASE_URL="postgresql+asyncpg://duk:dukpassword@localhost/duk_bus"
ENV REDIS_URL="redis://localhost:6379/0"
ENV OSRM_URL="http://router.project-osrm.org"
ENV ETA_ML_URL="http://localhost:8000"
ENV PYTHONPATH="/app/duk_micro/libs:/app/duk_micro"

EXPOSE 8000

CMD ["/app/start.sh"]
