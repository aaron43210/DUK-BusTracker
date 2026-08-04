#!/bin/bash
# simulation/start.sh — One-click launcher for the standalone GPS Simulator
cd "$(dirname "$0")"

if [ -f "/Users/aaronr/py311/bin/activate" ]; then
  source /Users/aaronr/py311/bin/activate
fi

echo "============================================================"
echo "⚡ DUK Bus GPS Simulator Web Control starting on Port 8050"
echo "👉 Open in your browser: http://localhost:8050"
echo "👉 Open Admin Dashboard: http://localhost:5173"
echo "============================================================"

python3 server.py
