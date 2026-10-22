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

# Nellimoodu corridor
BBOX_NELLIMOODU = "77.040,8.370,77.060,8.400"
OSM_API_URL_NELLIMOODU = f"https://api.openstreetmap.org/api/0.6/map?bbox={BBOX_NELLIMOODU}"

# Thampanoor / East Fort corridor — covers the bus route from Chandrasekharan Nair
# Stadium down to Thampanoor and the approach to Trivandrum Central railway station.
# This area has complex one-ways, the KSRTC bus bay, and divided roads that are
# frequently re-tagged in OSM. The static kerala.osm.pbf misses these updates and
# causes OSRM to return NoSegment / fall back to straight-line Haversine for the
# last leg into Thampanoor, making the route line "snap" incorrectly near Central.
# Split into two tiles — the full bbox exceeds OSM's 50k-node limit.
BBOX_THAMPANOOR_W = "76.940,8.480,76.952,8.510"  # Thampanoor station / KSRTC bay
BBOX_THAMPANOOR_E = "76.952,8.480,76.965,8.510"  # East Fort / Central
OSM_API_URL_THAMPANOOR_W = f"https://api.openstreetmap.org/api/0.6/map?bbox={BBOX_THAMPANOOR_W}"
OSM_API_URL_THAMPANOOR_E = f"https://api.openstreetmap.org/api/0.6/map?bbox={BBOX_THAMPANOOR_E}"

# Restrict flyover decoupling to the NH66 flyover corridor (Kazhakuttam) so it can
# never touch bridges elsewhere in the district. Format "minlon,minlat,maxlon,maxlat".
# Set FLYOVER_BBOX="" to allow decoupling everywhere (still interior-crossings only).
_fb = os.environ.get("FLYOVER_BBOX", "76.84,8.54,76.90,8.60")
FLYOVER_BBOX = tuple(float(x) for x in _fb.split(",")) if _fb.strip() else None



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
thampanoor_w_xml_data = fetch_osm(OSM_API_URL_THAMPANOOR_W, "Thampanoor/West tile")
thampanoor_e_xml_data = fetch_osm(OSM_API_URL_THAMPANOOR_E, "Thampanoor/East tile")

if not live_xml_data:
    print(" FATAL: Technopark corridor OSM fetch failed. This corridor is on the bus "
          "route, so patching without it would ship a stale/incomplete graph. "
          "Aborting so the build fails loudly instead of continuing on partial data.")
    sys.exit(1)
if not nellimoodu_xml_data:
    print(" WARNING: Nellimoodu corridor fetch failed — continuing without it (secondary).")
if not thampanoor_w_xml_data or not thampanoor_e_xml_data:
    print(" WARNING: Thampanoor corridor partial fetch — OSRM may miss the "
          "last leg into Thampanoor and fall back to straight-line Haversine near Central.")

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
merge_live_osm(thampanoor_w_xml_data, base_root)
merge_live_osm(thampanoor_e_xml_data, base_root)


def decouple_flyover(base_root):
    """Separate elevated bridge ways (the NH66 flyover) from the ground road
    underneath so OSRM cannot "jump" the bus between levels at a shared node.

    Standard fix: where a bridge way and a lower-layer ground way share a node
    that is NOT a ramp junction (no *_link way uses it), the shared node is a
    tagging artifact of the flyover crossing over the underpass — they should
    not connect there. We give the upper (bridge) level its own copy of that
    node, reassigning it in every bridge way (so the flyover stays continuous
    with itself) while the ground ways keep the original node. Ramp junctions
    (any *_link way on the node) are left connected so on/off ramps still work.

    Conservative and idempotent: if OSM already models the levels correctly it
    finds nothing to split and is a no-op. Every split is logged for review.
    """
    ways = []
    max_id = 0
    for child in base_root:
        cid = child.attrib.get("id")
        if cid and cid.lstrip("-").isdigit():
            max_id = max(max_id, abs(int(cid)))
        if child.tag != "way":
            continue
        nds = [nd.attrib["ref"] for nd in child.findall("nd")]
        tags = {t.attrib["k"]: t.attrib["v"] for t in child.findall("tag")}
        highway = tags.get("highway", "")
        try:
            layer = int(tags.get("layer", "0"))
        except ValueError:
            layer = 0
        is_bridge = tags.get("bridge", "no") not in ("no", "", None)
        if is_bridge and layer <= 0:
            layer = 1  # a bridge with no explicit layer sits above ground
        ways.append({"elem": child, "nds": nds, "layer": layer,
                     "is_bridge": is_bridge, "is_link": highway.endswith("_link")})

    # Per node: bridge/ground layers, ramp flag, and whether it is interior to a
    # bridge vs one of its endpoints. We ONLY decouple interior crossings (a flyover
    # passing OVER a ground road mid-span). A bridge's END nodes are how you drive
    # on/off it — severing those disconnects the bridge from the network, which is
    # what the earlier over-aggressive ~3000-node split did and why routing/route
    # history broke. Endpoints are therefore never touched.
    bridge_layer, ground_layer, has_link = {}, {}, set()
    bridge_interior, bridge_endpoint = set(), set()
    for w in ways:
        nds = w["nds"]
        for ref in nds:
            if w["is_link"]:
                has_link.add(ref)
            if w["is_bridge"]:
                bridge_layer[ref] = max(bridge_layer.get(ref, w["layer"]), w["layer"])
            else:
                ground_layer[ref] = min(ground_layer.get(ref, w["layer"]), w["layer"])
        if w["is_bridge"] and nds:
            bridge_endpoint.add(nds[0])
            bridge_endpoint.add(nds[-1])
            for ref in nds[1:-1]:
                bridge_interior.add(ref)

    node_coords = {n.attrib["id"]: (n.attrib.get("lat"), n.attrib.get("lon"))
                   for n in base_root.findall("node")}

    def _in_scope(ref):
        if FLYOVER_BBOX is None:
            return True
        c = node_coords.get(ref)
        if not c or c[0] is None:
            return False
        lat, lon = float(c[0]), float(c[1])
        mnlon, mnlat, mxlon, mxlat = FLYOVER_BBOX
        return mnlat <= lat <= mxlat and mnlon <= lon <= mxlon

    to_split = {
        ref for ref in bridge_interior
        if ref not in bridge_endpoint          # never cut a bridge's on/off connection
        and ref in ground_layer and ref not in has_link
        and ground_layer[ref] < bridge_layer.get(ref, 1)
        and _in_scope(ref)
    }
    if not to_split:
        print("   ↳ Flyover decoupling: no interior cross-layer crossings in scope (no-op)")
        return

    new_ref = {}
    next_id = max_id + 1
    for ref in to_split:
        lat, lon = node_coords.get(ref, (None, None))
        if lat is None:
            continue
        nid = str(next_id); next_id += 1
        new_ref[ref] = nid
        base_root.append(ET.Element("node", {"id": nid, "lat": lat, "lon": lon,
                                              "version": "1", "visible": "true"}))

    reassigned = 0
    for w in ways:
        if not w["is_bridge"]:
            continue
        for nd in w["elem"].findall("nd"):
            if nd.attrib["ref"] in new_ref:
                nd.attrib["ref"] = new_ref[nd.attrib["ref"]]
                reassigned += 1
    print(f"   ↳ Flyover decoupling: split {len(new_ref)} interior crossing node(s), "
          f"reassigned {reassigned} bridge references (scope={'bbox' if FLYOVER_BBOX else 'all'})")


decouple_flyover(base_root)

# NOTE: We used to rewrite oneway=no on every primary/secondary/tertiary road here,
# on the theory that median dividers were blocking the bus. In practice it let the
# router send trails the wrong way down real one-way streets and cut through small
# roads. One-way tags are now respected as mapped (oneway_handling = true in car.lua),
# so this global mutation has been removed. Genuine mis-tagged roads should be fixed
# in OSM upstream, not blanket-overridden.

print(f" Writing final patched OSM to {PATCHED_FILE}...")
base_tree.write(PATCHED_FILE, encoding="utf-8", xml_declaration=True)
print(" Ready for fresh OSRM graph compilation!")
