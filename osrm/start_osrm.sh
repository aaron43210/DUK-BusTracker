#!/bin/bash
# osrm/start_osrm.sh — Quick launcher for OSRM routing daemon
cd "$(dirname "$0")"

DATA_DIR="/Users/aaronr/osrm_data"
OSRM_DATA_PATH="${1:-$DATA_DIR/trivandrum_patched.osrm}"

if [ ! -f "$OSRM_DATA_PATH" ] && [ -f "$DATA_DIR/trivandrum.osrm" ]; then
    OSRM_DATA_PATH="$DATA_DIR/trivandrum.osrm"
fi

echo "============================================================"
echo "🚌 Starting OSRM Bus Routing Server on Port 5001..."
echo "Using map: $OSRM_DATA_PATH"
echo "============================================================"

osrm-routed --algorithm mld "$OSRM_DATA_PATH" --port 5001


