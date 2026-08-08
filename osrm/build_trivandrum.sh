#!/bin/bash
# osrm/build_trivandrum.sh
# 1. Stops any existing OSRM server to avoid file collision
# 2. Cleans old cache and extracts fresh Trivandrum slice
# 3. Fetches your live OpenStreetMap edits and merges cleanly
# 4. Compiles fresh OSRM graph with car.lua
# 5. Starts the OSRM server on port 5001

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="/Users/aaronr/osrm_data"
mkdir -p "$DATA_DIR"

SOURCE_PBF="$DATA_DIR/southern-zone-260729.osm.pbf"
TARGET_PBF="$DATA_DIR/trivandrum.osm.pbf"
TARGET_OSM="$DATA_DIR/trivandrum.osm"
PATCHED_OSM="$DATA_DIR/trivandrum_patched.osm"
TARGET_OSRM="$DATA_DIR/trivandrum_patched.osrm"

echo "============================================================"
echo "🛑 Step 1: Stopping existing OSRM instances..."
echo "============================================================"
pkill -f "osrm-routed" || true
sleep 1

echo "============================================================"
echo "🧹 Step 2: Cleaning old cache and extracting fresh Trivandrum..."
echo "============================================================"
rm -f "$DATA_DIR"/trivandrum* "$DATA_DIR"/technopark* "$DATA_DIR"/*.osrm* "$DATA_DIR"/patched* || true

if [ -f "$SOURCE_PBF" ]; then
    /opt/homebrew/bin/osmium extract -b 76.75,8.35,77.10,8.75 "$SOURCE_PBF" -o "$TARGET_PBF" --overwrite
    /opt/homebrew/bin/osmium cat "$TARGET_PBF" -o "$TARGET_OSM" --overwrite
fi

echo "============================================================"
echo "🌐 Step 3: Fetching Live OpenStreetMap Edits & Merging..."
echo "============================================================"
python3 "$DIR/download_and_apply_osm.py"

echo "============================================================"
echo "🔨 Step 4: Compiling Fresh OSRM Graph..."
echo "============================================================"
cd "$DIR/.."
osrm-extract -p osrm/car.lua "$PATCHED_OSM"
osrm-partition "$TARGET_OSRM"
osrm-customize "$TARGET_OSRM"

echo "============================================================"
echo "🚀 Step 5: Starting Fresh OSRM Server on Port 5001..."
echo "============================================================"
osrm-routed -p 5001 --algorithm mld "$TARGET_OSRM"
