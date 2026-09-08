#!/bin/bash
set -e

echo "============================================="
echo "   DUK Bus Tracker - 1-Click Startup Script"
echo "============================================="
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "[!] Docker is not installed. Please install Docker from https://www.docker.com/"
    exit 1
fi

echo "[1/3] Starting backend services with Docker Compose..."
docker compose up -d --build

echo "[2/3] Starting Admin Dashboard (http://localhost:5173)..."
(cd admin-dashboard && [ ! -d "node_modules" ] && npm install --silent; npm run dev) &
ADMIN_PID=$!

echo "[3/3] Starting Passenger PWA (http://localhost:5174)..."
(cd duk_pwa && [ ! -d "node_modules" ] && npm install --silent; npm run dev) &
PWA_PID=$!

echo ""
echo "============================================="
echo "  All services running!"
echo "  - Admin Dashboard: http://localhost:5173"
echo "  - Passenger PWA:   http://localhost:5174"
echo "  - Backend API:     http://localhost/api/v1/stops"
echo "  - Login:           admin / admin"
echo "============================================="
echo "Press Ctrl+C to stop frontends."

trap "kill $ADMIN_PID $PWA_PID 2>/dev/null; exit" SIGINT SIGTERM
wait
