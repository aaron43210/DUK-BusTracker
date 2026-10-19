#!/bin/bash
# 
# DUK Bus Tracker — OSRM Setup (Custom Pipeline)
#
# This script prepares the map for the DUK Bus Tracker. It goes beyond standard
# OSRM setup by running a custom patching script (patch_osm.py) that:
#   1. Fetches live map data for the DUK campus and Technopark
#   2. Makes major roads two-way for bus routing
#   3. Decouples the elevated NH66 flyover from the ground underpass so the
#      router doesn't jump the bus off the flyover.
#
# Run this ONCE on the server after setup.sh completes.
# The processing step takes ~10-15 minutes.
#
# Usage:
#   chmod +x osrm-setup.sh
#   sudo ./osrm-setup.sh
# 
set -e

OSRM_DIR="/opt/osrm"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$OSRM_DIR"

echo ""
echo "  DUK Advanced OSRM Setup — Trivandrum District"
echo ""

# Stop existing container if it's running
docker rm -f osrm-trivandrum 2>/dev/null || true

# Copy our custom config files to the OSRM data directory
cp "$DEPLOY_DIR/bus.lua" "$OSRM_DIR/bus.lua"
cp "$DEPLOY_DIR/patch_osm.py" "$OSRM_DIR/patch_osm.py"

cd "$OSRM_DIR"

#  Step 1: Download Kerala map 
echo ""
echo "[1/5] Downloading Kerala OSM map (~100 MB)..."
if [ ! -f "kerala-latest.osm.pbf" ]; then
    wget -q --show-progress \
        https://download.openstreetmap.fr/extracts/asia/india/kerala-latest.osm.pbf
    echo "   Download complete"
else
    echo "   Kerala map already downloaded, skipping"
fi

#  Step 2: Crop to Trivandrum & Convert to XML 
echo ""
echo "[2/5] Cropping to Trivandrum district and extracting XML..."
# Bounding box: 8.2°N to 8.7°N, 76.5°E to 77.3°E
# This covers: Trivandrum city + suburbs + the DUK campus route
# Note: Output is uncompressed XML (.osm) because our Python patch script needs XML
osmosis \
    --read-pbf /opt/osrm/kerala-latest.osm.pbf \
    --bounding-box top=8.70 left=76.50 bottom=8.20 right=77.30 \
    --write-xml /opt/osrm/trivandrum.osm
echo "   Trivandrum XML extract ready"

#  Step 3: Patch OSM (Flyover decoupling & Live Data) 
echo ""
echo "[3/5] Running custom DUK map patcher..."
# Requires Python 3 (installed by setup.sh)
python3 "$OSRM_DIR/patch_osm.py"
echo "   Map patched successfully -> trivandrum_patched.osm"

#  Step 4: Process map for OSRM 
echo ""
echo "[4/5] Processing map for OSRM (~10 minutes)..."
if [ ! -d "$OSRM_DIR/lib" ]; then
    echo "   Downloading OSRM Lua profile dependencies from GitHub main..."
    # GitHub main has resolve_access and all modern helpers our bus.lua needs.
    git clone --depth 1 https://github.com/Project-OSRM/osrm-backend.git /tmp/osrm-backend-repo
    cp -r /tmp/osrm-backend-repo/profiles/lib "$OSRM_DIR/lib"
    rm -rf /tmp/osrm-backend-repo

    # Force-overwrite obstacles.lua with our bundled stub.
    # GitHub main's obstacles.lua uses a newer API where obstacle_type is NOT a global,
    # but our car.lua was written for the older style where it IS a global.
    # Our stub bridges this gap without changing car.lua.
    cp "$DEPLOY_DIR/lib/obstacles.lua" "$OSRM_DIR/lib/obstacles.lua"

    # Force-overwrite access.lua with our bundled version.
    # v5.27.1 removed resolve_access() from access.lua (logic moved inline in car.lua).
    # Our bus.lua still calls resolve_access(), so we inject a compatibility shim.
    cp "$DEPLOY_DIR/lib/access.lua" "$OSRM_DIR/lib/access.lua"
    echo "   Lua lib ready — $(ls $OSRM_DIR/lib | wc -l | tr -d ' ') files (obstacles.lua + access.lua overridden with bundled stubs)"
fi

# Pin to the same version used in docker-compose.yml (v5.27.1) so the .osrm
# file format written during processing matches the binary that serves requests.
# Using `latest` for extract/partition and a pinned version for the server would
# cause "incompatible file format" crashes on startup.
OSRM_IMAGE="osrm/osrm-backend:v5.27.1"

# Extract road network using our custom bus.lua profile
docker run --rm -t -v "$OSRM_DIR:/data" $OSRM_IMAGE \
    osrm-extract -p /data/bus.lua /data/trivandrum_patched.osm

# Partition the road network for multi-level Dijkstra
docker run --rm -t -v "$OSRM_DIR:/data" $OSRM_IMAGE \
    osrm-partition /data/trivandrum_patched.osrm

# Customize edge weights
docker run --rm -t -v "$OSRM_DIR:/data" $OSRM_IMAGE \
    osrm-customize /data/trivandrum_patched.osrm

echo "   OSRM processing complete"

#  Step 5: Start OSRM server 
echo ""
echo "[5/5] Starting OSRM server on port 5001..."
docker run -d \
    --name osrm-trivandrum \
    --restart=always \
    -p 127.0.0.1:5001:5000 \
    -v "$OSRM_DIR:/data" \
    $OSRM_IMAGE \
    osrm-routed --algorithm mld /data/trivandrum_patched.osrm
echo "   OSRM running on http://127.0.0.1:5001"

#  Verify 
echo ""
echo "Testing OSRM..."
sleep 3
RESPONSE=$(curl -s "http://127.0.0.1:5001/nearest/v1/driving/76.9061,8.5581?number=1")
if echo "$RESPONSE" | grep -q '"code":"Ok"'; then
    STREET=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['waypoints'][0].get('name','(unnamed road)'))")
    echo "   OSRM working! Test point resolved to: $STREET"
else
    echo "   OSRM response unexpected: $RESPONSE"
fi

echo ""
echo ""
echo "  Advanced OSRM setup complete!"
echo "  RAM usage: ~150 MB (Trivandrum only)"
echo "  To check status: docker ps | grep osrm"
echo ""
