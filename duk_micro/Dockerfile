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
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source code
COPY . .

# Set PYTHONPATH so absolute imports work
ENV PYTHONPATH=/app

# Expose port (used by mega_gateway)
EXPOSE 8000

# Default command (can be overridden by docker-compose for workers)
CMD ["uvicorn", "mega_gateway:app", "--host", "0.0.0.0", "--port", "8000"]
