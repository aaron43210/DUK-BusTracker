"""
Asynchronous client for interacting with the local Valhalla routing server.
Falls back to Haversine straight-line distance if the server is unreachable 
or if coordinates are outside the Trivandrum region extract.
"""
import httpx
import logging
import math
import os
from typing import List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

VALHALLA_BASE_URL = os.environ.get("VALHALLA_URL", "http://127.0.0.1:8002")
COSTING_MODEL = "bus"

# Trivandrum region bounding box — adjust to match your actual .osm.pbf extract
REGION_BOUNDS = {
    "min_lat": 8.2,
    "max_lat": 9.0, 
    "min_lon": 76.5,
    "max_lon": 77.5,
}

def is_within_extract(lat: float, lon: float) -> bool:
    """Check if a coordinate falls within our map extract boundary."""
    return (
        REGION_BOUNDS["min_lat"] <= lat <= REGION_BOUNDS["max_lat"] and
        REGION_BOUNDS["min_lon"] <= lon <= REGION_BOUNDS["max_lon"]
    )

def haversine_m_math(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line fallback distance in metres."""
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))

def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlon_r = math.radians(lon2 - lon1)
    x = math.sin(dlon_r) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def _angle_diff(a1: float, a2: float) -> float:
    diff = abs(a1 - a2)
    return min(diff, 360 - diff)

def decode_polyline6(encoded: str, precision: int = 6) -> List[List[float]]:
    """Decode Valhalla Polyline6 string into a list of [lon, lat] coordinates."""
    index, lat, lng = 0, 0, 0
    coordinates = []
    factor = 10 ** precision

    while index < len(encoded):
        shift, result = 0, 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1f) << shift
            shift += 5
            if byte < 0x20:
                break
        latitude_change = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += latitude_change

        shift, result = 0, 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1f) << shift
            shift += 5
            if byte < 0x20:
                break
        longitude_change = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += longitude_change

        coordinates.append([lng / factor, lat / factor])

    return coordinates


@dataclass
class _SnapState:
    lat: Optional[float] = None
    lon: Optional[float] = None
    bearing: Optional[float] = None
    disagreements: int = 0

_snap_state = _SnapState()

def reset_snap_state():
    """Reset snap state, e.g., when a new trip starts or power restores."""
    global _snap_state
    _snap_state = _SnapState()
    logger.info("[Valhalla] Snap state reset")


async def snap_live_gps(lat: float, lon: float) -> tuple[float, float]:
    """
    Snap a live raw GPS coordinate to the nearest road node using Valhalla.
    Uses bearing from the previous point to maintain directional continuity.
    """
    if not is_within_extract(lat, lon):
        logger.debug("[Valhalla] Coordinate outside extract bounds, skipping snap: (%.5f, %.5f)", lat, lon)
        return lat, lon

    global _snap_state
    current_bearing = None

    if _snap_state.lat is not None and _snap_state.lon is not None:
        dist = haversine_m_math(_snap_state.lat, _snap_state.lon, lat, lon)
        if dist > 8.0:
            current_bearing = _bearing(_snap_state.lat, _snap_state.lon, lat, lon)
            
            if _snap_state.bearing is not None:
                diff = _angle_diff(current_bearing, _snap_state.bearing)
                if diff > 45:
                    _snap_state.disagreements += 1
                else:
                    _snap_state.disagreements = 0
            
            if _snap_state.disagreements >= 3:
                logger.debug("[Valhalla] Bearing release valve triggered: %s -> %s", _snap_state.bearing, current_bearing)
                _snap_state.bearing = current_bearing
                _snap_state.disagreements = 0
            elif _snap_state.bearing is None:
                _snap_state.bearing = current_bearing

    location = {"lat": lat, "lon": lon}
    if _snap_state.bearing is not None:
        location["heading"] = int(_snap_state.bearing)

    req_body = {
        "locations": [location],
        "costing": COSTING_MODEL
    }

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1.0) as client:
            resp = await client.post(f"{VALHALLA_BASE_URL}/locate", json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                # Valhalla locate returns array of arrays if multiple locations given, we gave one
                if isinstance(data, list) and len(data) > 0:
                    node = data[0]
                    if node.get("edges") and len(node["edges"]) > 0:
                        # Edge 0 is typically the most probable nearest edge
                        pt = node["edges"][0]["correlated_lat"], node["edges"][0]["correlated_lon"]
                        snapped_lat = float(pt[0])
                        snapped_lon = float(pt[1])
                        
                        _snap_state.lat = snapped_lat
                        _snap_state.lon = snapped_lon
                        delta_m = haversine_m_math(lat, lon, snapped_lat, snapped_lon)
                        logger.debug(
                            "[Valhalla] Snapped (%.5f,%.5f)→(%.5f,%.5f) Δ%.1fm b=%s",
                            lat, lon, snapped_lat, snapped_lon, delta_m, location.get("heading")
                        )
                        return snapped_lat, snapped_lon
    except Exception as e:
        logger.debug("[Valhalla] snap_live_gps network error: %s", e)

    # If snap fails entirely
    _snap_state.lat = lat
    _snap_state.lon = lon
    logger.debug("[Valhalla] All snap attempts failed, returning raw GPS (%.5f,%.5f)", lat, lon)
    return lat, lon


async def get_valhalla_route(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    dist_m = haversine_m_math(lat1, lon1, lat2, lon2)
    fallback_result = {
        "distance_m": dist_m,
        "duration_s": (dist_m / 1000.0 / 25.0) * 3600.0,
        "from_valhalla":  False,
    }

    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return fallback_result

    req_body = {
        "locations": [
            {"lat": lat1, "lon": lon1},
            {"lat": lat2, "lon": lon2}
        ],
        "costing": COSTING_MODEL,
        "directions_options": {"units": "kilometers"}
    }
    
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2.5) as client:
            resp = await client.post(f"{VALHALLA_BASE_URL}/route", json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("trip") and data["trip"].get("summary"):
                    summary = data["trip"]["summary"]
                    return {
                        "distance_m": float(summary["length"] * 1000.0),
                        "duration_s": float(summary["time"]),
                        "from_valhalla":  True,
                    }
    except Exception as e:
        logger.debug("[Valhalla] get_valhalla_route fallback: %s", e)

    return fallback_result


def get_valhalla_route_sync(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    import httpx as _httpx
    dist_m = haversine_m_math(lat1, lon1, lat2, lon2)
    fallback_result = {
        "distance_m": dist_m,
        "duration_s": (dist_m / 1000.0 / 25.0) * 3600.0,
        "from_valhalla":  False,
    }

    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return fallback_result

    req_body = {
        "locations": [
            {"lat": lat1, "lon": lon1},
            {"lat": lat2, "lon": lon2}
        ],
        "costing": COSTING_MODEL,
        "directions_options": {"units": "kilometers"}
    }

    try:
        with _httpx.Client(trust_env=False, timeout=2.5) as client:
            resp = client.post(f"{VALHALLA_BASE_URL}/route", json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("trip") and data["trip"].get("summary"):
                    summary = data["trip"]["summary"]
                    return {
                        "distance_m": float(summary["length"] * 1000.0),
                        "duration_s": float(summary["time"]),
                        "from_valhalla":  True,
                    }
    except Exception as e:
        logger.debug("[Valhalla] get_valhalla_route_sync fallback: %s", e)

    return fallback_result


async def get_valhalla_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    info = await get_valhalla_route(lat1, lon1, lat2, lon2)
    return info["distance_m"]


async def get_valhalla_distance_matrix_m(src_lat: float, src_lon: float, destinations: list[tuple[float, float]]) -> list[float]:
    fallback_dists = [haversine_m_math(src_lat, src_lon, d_lat, d_lon) for d_lat, d_lon in destinations]
    if not destinations:
        return []

    if not is_within_extract(src_lat, src_lon):
        return fallback_dists

    valid_dests = []
    valid_indices = []
    for i, (d_lat, d_lon) in enumerate(destinations):
        if is_within_extract(d_lat, d_lon):
            valid_dests.append((d_lat, d_lon))
            valid_indices.append(i)

    if not valid_dests:
        return fallback_dists

    req_body = {
        "sources": [{"lat": src_lat, "lon": src_lon}],
        "targets": [{"lat": lat, "lon": lon} for lat, lon in valid_dests],
        "costing": COSTING_MODEL,
        "units": "kilometers"
    }

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=3.0) as client:
            resp = await client.post(f"{VALHALLA_BASE_URL}/sources_to_targets", json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("sources_to_targets") and len(data["sources_to_targets"]) > 0:
                    dists = data["sources_to_targets"][0]
                    if len(dists) == len(valid_dests):
                        for j, valhalla_target in enumerate(dists):
                            orig_idx = valid_indices[j]
                            if valhalla_target is not None and "distance" in valhalla_target:
                                fallback_dists[orig_idx] = float(valhalla_target["distance"] * 1000.0)
    except Exception as e:
        logger.debug("[Valhalla] distance matrix fallback: %s", e)

    return fallback_dists


async def get_valhalla_segment_geometry(lat1: float, lon1: float, lat2: float, lon2: float) -> List[List[float]]:
    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return [[lon1, lat1], [lon2, lat2]]

    straight_dist = haversine_m_math(lat1, lon1, lat2, lon2)

    req_body = {
        "locations": [
            {"lat": lat1, "lon": lon1},
            {"lat": lat2, "lon": lon2}
        ],
        "costing": COSTING_MODEL,
        "directions_options": {"units": "kilometers"}
    }

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2.5) as client:
            resp = await client.post(f"{VALHALLA_BASE_URL}/route", json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("trip") and data["trip"].get("summary"):
                    route_dist = float(data["trip"]["summary"]["length"] * 1000.0)
                    # Prevent massive detours
                    if straight_dist > 50 and route_dist > (straight_dist * 3.0):
                        logger.warning(
                            "[Valhalla] Segment detour detected: route %.1fm > 3x straight %.1fm. Falling back to straight line.",
                            route_dist, straight_dist
                        )
                        return [[lon1, lat1], [lon2, lat2]]
                    
                    if data["trip"].get("legs") and len(data["trip"]["legs"]) > 0:
                        return decode_polyline6(data["trip"]["legs"][0]["shape"])
    except Exception as e:
        logger.debug("[Valhalla] get_valhalla_segment_geometry error: %s", e)
    
    return [[lon1, lat1], [lon2, lat2]]


async def get_valhalla_route_geometry(coordinates: list[tuple[float, float]]) -> list[list[float]]:
    if len(coordinates) < 2:
        return [[lon, lat] for lat, lon in coordinates]
        
    valid = [c for c in coordinates if is_within_extract(c[0], c[1])]
    if len(valid) < 2:
        return [[lon, lat] for lat, lon in coordinates]

    req_body = {
        "locations": [{"lat": lat, "lon": lon} for lat, lon in valid],
        "costing": COSTING_MODEL,
        "directions_options": {"units": "kilometers"}
    }

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2.5) as client:
            resp = await client.post(f"{VALHALLA_BASE_URL}/route", json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                full_coords = []
                if data.get("trip") and data["trip"].get("legs"):
                    for leg in data["trip"]["legs"]:
                        if "shape" in leg:
                            full_coords.extend(decode_polyline6(leg["shape"]))
                
                if full_coords:
                    return full_coords
    except Exception as e:
        logger.debug("[Valhalla] get_valhalla_route_geometry error: %s", e)
        
    return [[lon, lat] for lat, lon in coordinates]


async def trace_valhalla_path(coordinates: list[tuple[float, float]]) -> list[list[float]]:
    """Map matching (Meili) using Valhalla's /trace_route endpoint."""
    if len(coordinates) < 2:
        return [[lon, lat] for lat, lon in coordinates]
        
    valid = [c for c in coordinates if is_within_extract(c[0], c[1])]
    if len(valid) < 2:
        return [[lon, lat] for lat, lon in coordinates]

    req_body = {
        "shape": [{"lat": lat, "lon": lon} for lat, lon in valid],
        "costing": COSTING_MODEL,
        "shape_match": "map_snap",
        "directions_options": {"units": "kilometers"}
    }

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
            resp = await client.post(f"{VALHALLA_BASE_URL}/trace_route", json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                full_coords = []
                if data.get("trip") and data["trip"].get("legs"):
                    for leg in data["trip"]["legs"]:
                        if "shape" in leg:
                            full_coords.extend(decode_polyline6(leg["shape"]))
                
                if full_coords:
                    return full_coords
            else:
                logger.debug(f"[Valhalla] trace_route failed with status {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.debug("[Valhalla] trace_valhalla_path error: %s", e)
        
    return [[lon, lat] for lat, lon in coordinates]
