#!/bin/bash
# run_local_simulation.sh
# This script spins up the complete production architecture on your Mac.

echo "🚀 Starting DUK Production Simulation..."
cd "$(dirname "$0")/.."

# 1. Start all services except backend first to allow DB to boot
echo "📦 Spinning up Postgres, Redis, Nginx, and OSRM in Docker..."
docker compose -f docker-compose.prod-mirror.yml up -d postgres redis osrm nginx

echo "⏳ Waiting 10 seconds for Postgres to be fully ready..."
sleep 10

# 2. Start the Backend API
echo "⚙️ Building and starting the Python Backend..."
docker compose -f docker-compose.prod-mirror.yml up -d backend --build

echo "✅ Production simulation is running!"
echo "   App is available at: http://localhost"
echo "   Backend API is at:   http://localhost/api/v1/docs"

echo ""
echo "🧠 To import historical data and train the ML model on this simulated database, run:"
echo "   export DATABASE_URL='postgresql://duk_user:password123@localhost:5432/duk_bus'"
echo "   python3 scripts/import_and_train_historical.py"
echo ""
echo "To stop the simulation: docker compose -f docker-compose.prod-mirror.yml down"
