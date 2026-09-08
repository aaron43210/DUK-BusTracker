#!/usr/bin/env python3
"""
patch_osm.py
Downloads live OpenStreetMap data for Technopark and DUK Campus and automatically decouples
the underpass from the elevated NH66 flyover so traffic is forced to use the service road!

Runs inside the production server environment (paths assume /opt/osrm).
"""
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

DATA_DIR = "/opt/osrm"
OSM_FILE = os.path.join(DATA_DIR, "trivandrum.osm")
PATCHED_FILE = os.path.join(DATA_DIR, "trivandrum_patched.osm")
PBF_FILE = os.path.join(DATA_DIR, "trivandrum.osm.pbf")
LIVE_FILE = os.path.join(DATA_DIR, "technopark_live.osm")

# Technopark / Kazhakuttam corridor
BBOX_TECHNOPARK = "76.872,8.554,76.882,8.564"
OSM_API_URL_TECHNOPARK = f"https://api.openstreetmap.org/api/0.6/map?bbox={BBOX_TECHNOPARK}"



def fetch_osm(url, label):
    print(f" Fetching live OSM data for {label}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BusTracker-OSRM-Updater/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read().decode("utf-8")
            print(f" {label}: fetched {len(data):,} bytes")
            return data
    except Exception as e:
        print(f" {label} fetch failed: {e}")
        return None

live_xml_data       = fetch_osm(OSM_API_URL_TECHNOPARK, "Technopark corridor")
nellimoodu_xml_data = fetch_osm(OSM_API_URL_NELLIMOODU, "Nellimoodu corridor")

if not live_xml_data and not nellimoodu_xml_data:
    print(" No OSM data available.")
    sys.exit(1)
if not live_xml_data:
    live_xml_data = nellimoodu_xml_data

print(" Parsing base OSM and live OSM trees...")
if not os.path.exists(OSM_FILE):
    print(f" Base OSM file not found: {OSM_FILE}")
    sys.exit(1)

base_tree = ET.parse(OSM_FILE)
base_root = base_tree.getroot()

def merge_live_osm(xml_data, base_root):
    """Merge live OSM nodes/ways into the base tree, replacing stale entries."""
    if not xml_data:
        return
    root = ET.fromstring(xml_data)
    live_nodes = {n.attrib["id"]: n for n in root.findall("node")}
    live_ways  = {w.attrib["id"]: w for w in root.findall("way")}
    replaced_nodes, replaced_ways = set(), set()
    for i, child in enumerate(base_root):
        cid = child.attrib.get("id")
        if child.tag == "node" and cid in live_nodes:
            base_root[i] = live_nodes[cid]; replaced_nodes.add(cid)
        elif child.tag == "way" and cid in live_ways:
            base_root[i] = live_ways[cid]; replaced_ways.add(cid)
    for nid, node in live_nodes.items():
        if nid not in replaced_nodes: base_root.append(node)
    for wid, way in live_ways.items():
        if wid not in replaced_ways: base_root.append(way)
    print(f"   ↳ Merged {len(live_nodes)} nodes, {len(live_ways)} ways")

merge_live_osm(live_xml_data, base_root)
merge_live_osm(nellimoodu_xml_data, base_root)

# Global pass to remove one-way restrictions from all primary, secondary, and tertiary roads.
# This ensures the bus can travel both ways on major roads regardless of how OSM has tagged them.
# Trunk/motorway are intentionally left alone — those are real one-way expressways.
BUS_ROAD_TYPES = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}
oneway_fixed = 0
for way in base_root.findall("way"):
    tags = {t.attrib.get("k"): t.attrib.get("v") for t in way.findall("tag")}
    if tags.get("highway") in BUS_ROAD_TYPES and tags.get("oneway") in ("yes", "1", "true", "-1"):
        for t in way.findall("tag"):
            if t.attrib.get("k") == "oneway":
                t.attrib["v"] = "no"
                oneway_fixed += 1
print(f"  Made {oneway_fixed} primary/secondary/tertiary roads two-way!")

# ---------------------------------------------------------------------------
# AUTOMATIC FLYOVER DECOUPLING:
# Disconnect any shared nodes between the elevated trunk flyover and the underpass road
# ---------------------------------------------------------------------------
print(" Decoupling elevated NH66 flyover from ground underpass...")

# Find all trunk (flyover) ways
trunk_node_ids = set()
for way in base_root.findall("way"):
    tags = {t.attrib.get("k"): t.attrib.get("v") for t in way.findall("tag")}
    if tags.get("highway") in ["trunk", "motorway", "trunk_link"]:
        for nd in way.findall("nd"):
            trunk_node_ids.add(nd.attrib.get("ref"))

# Find underpass / cross-connector ways
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
                min_lat, max_lat = min(lats), max(lats)
                min_lon, max_lon = min(lons), max(lons)

                # Bus route corridors where two-way travel must be allowed:
                in_technopark = (min_lat > 8.550 and max_lat < 8.570 and min_lon > 76.870 and max_lon < 76.885)
                in_nellimoodu = (min_lat > 8.370 and max_lat < 8.400 and min_lon > 77.040 and max_lon < 77.060)

                if in_technopark or in_nellimoodu:
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

print(f" Decoupled {decoupled_count} shared nodes between flyover and underpass!")
print(f" Writing final patched OSM to {PATCHED_FILE}...")
base_tree.write(PATCHED_FILE, encoding="utf-8", xml_declaration=True)
print(" Ready for fresh OSRM graph compilation!")
