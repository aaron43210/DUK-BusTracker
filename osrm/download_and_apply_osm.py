"""
download_and_apply_osm.py
Downloads live OpenStreetMap data for Technopark and automatically decouples/disconnects
the underpass from the elevated NH66 flyover so traffic is forced to use the service road!
"""
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

DATA_DIR = "/Users/aaronr/osrm_data"
OSM_FILE = os.path.join(DATA_DIR, "trivandrum.osm")
PATCHED_FILE = os.path.join(DATA_DIR, "trivandrum_patched.osm")
PBF_FILE = os.path.join(DATA_DIR, "trivandrum.osm.pbf")
LIVE_FILE = os.path.join(DATA_DIR, "technopark_live.osm")

BBOX = "76.872,8.554,76.882,8.564"
OSM_API_URL = f"https://api.openstreetmap.org/api/0.6/map?bbox={BBOX}"

print("🌐 Fetching live OSM data from OpenStreetMap API...")
try:
    req = urllib.request.Request(
        OSM_API_URL,
        headers={"User-Agent": "BusTracker-OSRM-Updater/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        live_xml_data = response.read().decode("utf-8")
        with open(LIVE_FILE, "w", encoding="utf-8") as f:
            f.write(live_xml_data)
        print(f"✅ Successfully fetched {len(live_xml_data):,} bytes of live OSM data!")
except Exception as e:
    print(f"⚠️ Live download fallback: {e}")
    if os.path.exists(LIVE_FILE):
        with open(LIVE_FILE, "r", encoding="utf-8") as f:
            live_xml_data = f.read()
    else:
        live_xml_data = None

if not live_xml_data:
    print("❌ No OSM data available.")
    sys.exit(1)

print("🌲 Parsing base OSM and live OSM trees...")
if not os.path.exists(OSM_FILE):
    os.system(f"/opt/homebrew/bin/osmium cat {PBF_FILE} -o {OSM_FILE} --overwrite")

live_root = ET.fromstring(live_xml_data)
live_nodes = {n.attrib["id"]: n for n in live_root.findall("node")}
live_ways = {w.attrib["id"]: w for w in live_root.findall("way")}

base_tree = ET.parse(OSM_FILE)
base_root = base_tree.getroot()

# Track and replace elements
replaced_nodes = set()
replaced_ways = set()

for i, child in enumerate(base_root):
    cid = child.attrib.get("id")
    if child.tag == "node" and cid in live_nodes:
        base_root[i] = live_nodes[cid]
        replaced_nodes.add(cid)
    elif child.tag == "way" and cid in live_ways:
        base_root[i] = live_ways[cid]
        replaced_ways.add(cid)

for nid, node in live_nodes.items():
    if nid not in replaced_nodes:
        base_root.append(node)

for wid, way in live_ways.items():
    if wid not in replaced_ways:
        base_root.append(way)

# ---------------------------------------------------------------------------
# AUTOMATIC FLYOVER DECOUPLING:
# Disconnect any shared nodes between the elevated trunk flyover and the underpass road
# ---------------------------------------------------------------------------
print("🌉 Decoupling elevated NH66 flyover from ground underpass...")

# Find all trunk (flyover) ways in the Technopark bounding box
trunk_node_ids = set()
for way in base_root.findall("way"):
    tags = {t.attrib.get("k"): t.attrib.get("v") for t in way.findall("tag")}
    if tags.get("highway") in ["trunk", "motorway", "trunk_link"]:
        for nd in way.findall("nd"):
            trunk_node_ids.add(nd.attrib.get("ref"))

# Find underpass / cross-connector ways (horizontal lines crossing longitude 76.874 to 76.878)
all_nodes_dict = {n.attrib["id"]: n for n in base_root.findall("node")}
decoupled_count = 0
next_custom_node_id = 9999900000

for way in base_root.findall("way"):
    tags = {t.attrib.get("k"): t.attrib.get("v") for t in way.findall("tag")}
    h_type = tags.get("highway")
    if h_type and h_type not in ["trunk", "motorway", "trunk_link", "motorway_link"]:
        nds = way.findall("nd")
        if len(nds) >= 2:
            refs = [nd.attrib.get("ref") for nd in nds]
            lats = [float(all_nodes_dict[r].attrib["lat"]) for r in refs if r in all_nodes_dict]
            lons = [float(all_nodes_dict[r].attrib["lon"]) for r in refs if r in all_nodes_dict]
            if lats and lons:
                # If within Kazhakuttam - Technopark corridor (lat 8.550 to 8.570, lon 76.870 to 76.885)
                if min(lats) > 8.550 and max(lats) < 8.570 and min(lons) > 76.870 and max(lons) < 76.885:
                    # Allow two-way bus transit on ground service roads (remove any one-way restrictions)
                    has_oneway = False
                    for t in way.findall("tag"):
                        if t.attrib.get("k") == "oneway":
                            t.attrib["v"] = "no"
                            has_oneway = True
                    if not has_oneway:
                        way.append(ET.Element("tag", {"k": "oneway", "v": "no"}))

                    for nd in nds:
                        ref = nd.attrib.get("ref")
                        if ref in trunk_node_ids and ref in all_nodes_dict:
                            orig_node = all_nodes_dict[ref]
                            # Create a decoupled clone node so ground way does not touch flyover
                            new_node_id = str(next_custom_node_id)
                            next_custom_node_id += 1
                            cloned_node = ET.Element("node", {
                                "id": new_node_id,
                                "lat": orig_node.attrib["lat"],
                                "lon": orig_node.attrib["lon"],
                                "version": "1"
                            })
                            base_root.append(cloned_node)
                            all_nodes_dict[new_node_id] = cloned_node
                            nd.attrib["ref"] = new_node_id
                            decoupled_count += 1

print(f"🎉 Decoupled {decoupled_count} shared nodes between flyover and underpass!")
print(f"💾 Writing final patched OSM to {PATCHED_FILE}...")
base_tree.write(PATCHED_FILE, encoding="utf-8", xml_declaration=True)
print("🚀 Ready for fresh OSRM graph compilation!")
