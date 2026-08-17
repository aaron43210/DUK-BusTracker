"""
Asynchronous client for interacting with the local OSRM routing server.
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

OSRM_BASE_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5001")

_osrm_client = httpx.AsyncClient(
    base_url=OSRM_BASE_URL,
    trust_env=False,
    timeout=httpx.Timeout(connect=1.0, read=2.5, write=1.0, pool=1.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)

# Trivandrum region bounding box — adjust to match your actual .osm.pbf extract
REGION_BOUNDS = {
    "min_lat": 8.2,
    "max_lat": 9.0, 
    "min_lon": 76.5,
    "max_lon": 77.5,
}

def is_within_extract(lat: float, lon: float) -> bool:
    """Check if a coordinate falls within our OSRM extract boundary."""
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
    logger.info("[OSRM] Snap state reset")


# Tiered snap radii: try tight first, progressively wider on failure.
_SNAP_RADII_M = [30, 60, 120]
OSRM_LIVE_SNAP_RADIUS_M = _SNAP_RADII_M[0]

async def snap_live_gps(lat: float, lon: float) -> tuple[float, float]:
    """
    Snap a live raw GPS coordinate to the nearest road node.
    Uses bearing from the previous point to maintain directional continuity
    and avoid flipping between parallel roads.
    Includes a release valve for real turns and gracefully handles out-of-bounds coordinates.
    """
    if not is_within_extract(lat, lon):
        logger.debug("[OSRM] Coordinate outside extract bounds, skipping snap: (%.5f, %.5f)", lat, lon)
        return lat, lon

    global _snap_state
    bearing_param = ""
    current_bearing = None

    if _snap_state.lat is not None and _snap_state.lon is not None:
        dist = haversine_m_math(_snap_state.lat, _snap_state.lon, lat, lon)
        if dist > 8.0:
            current_bearing = _bearing(_snap_state.lat, _snap_state.lon, lat, lon)
            
            if _snap_state.bearing is not None:
                # Check if bus made a sharp turn compared to locked bearing
                diff = _angle_diff(current_bearing, _snap_state.bearing)
                if diff > 45:
                    _snap_state.disagreements += 1
                else:
                    _snap_state.disagreements = 0
            
            # Release valve: if disagreed 3 times in a row, accept new bearing
            if _snap_state.disagreements >= 3:
                logger.debug("[OSRM] Bearing release valve triggered: %s -> %s", _snap_state.bearing, current_bearing)
                _snap_state.bearing = current_bearing
                _snap_state.disagreements = 0
            elif _snap_state.bearing is None:
                _snap_state.bearing = current_bearing

            # Apply locked bearing for snapping
            if _snap_state.bearing is not None:
                bearing_param = f"&bearings={int(_snap_state.bearing)},45"

    for radius in _SNAP_RADII_M:
        url = (
            f"/nearest/v1/driving/{lon:.6f},{lat:.6f}"
            f"?number=1&radiuses={radius}{bearing_param}"
        )
        try:
            resp = await _osrm_client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("waypoints"):
                    pt = data["waypoints"][0]["location"]
                    snapped_lat = float(pt[1])
                    snapped_lon = float(pt[0])
                    _snap_state.lat = snapped_lat
                    _snap_state.lon = snapped_lon
                    delta_m = haversine_m_math(lat, lon, snapped_lat, snapped_lon)
                    logger.debug(
                        "[OSRM] Snapped (%.5f,%.5f)→(%.5f,%.5f) Δ%.1fm r=%dm b=%s",
                        lat, lon, snapped_lat, snapped_lon, delta_m, radius, bearing_param,
                    )
                    return snapped_lat, snapped_lon
        except Exception as e:
            logger.debug("[OSRM] snap_live_gps network error r=%dm: %s", radius, e)
            break

    # If snap fails entirely (no roads or network error)
    _snap_state.lat = lat
    _snap_state.lon = lon
    logger.debug("[OSRM] All snap attempts failed, returning raw GPS (%.5f,%.5f)", lat, lon)
    return lat, lon


async def get_osrm_route(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    dist_m = haversine_m_math(lat1, lon1, lat2, lon2)
    fallback_result = {
        "distance_m": dist_m,
        "duration_s": (dist_m / 1000.0 / 25.0) * 3600.0,
        "from_osrm":  False,
    }

    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return fallback_result

    url = (
        f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
        f"?radiuses={OSRM_LIVE_SNAP_RADIUS_M};{OSRM_LIVE_SNAP_RADIUS_M}"
        f"&continue_straight=false&overview=false"
    )
    try:
        resp = await _osrm_client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                return {
                    "distance_m": float(route["distance"]),
                    "duration_s": float(route["duration"]),
                    "from_osrm":  True,
                }
    except Exception as e:
        logger.debug("[OSRM] get_osrm_route fallback: %s", e)

    return fallback_result


def get_osrm_route_sync(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    import httpx as _httpx
    dist_m = haversine_m_math(lat1, lon1, lat2, lon2)
    fallback_result = {
        "distance_m": dist_m,
        "duration_s": (dist_m / 1000.0 / 25.0) * 3600.0,
        "from_osrm":  False,
    }

    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return fallback_result

    url = (
        f"{OSRM_BASE_URL}/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
        f"?radiuses={OSRM_LIVE_SNAP_RADIUS_M};{OSRM_LIVE_SNAP_RADIUS_M}"
        f"&continue_straight=false&overview=false"
    )
    try:
        with _httpx.Client(trust_env=False, timeout=2.5) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    route = data["routes"][0]
                    return {
                        "distance_m": float(route["distance"]),
                        "duration_s": float(route["duration"]),
                        "from_osrm":  True,
                    }
    except Exception as e:
        logger.debug("[OSRM] get_osrm_route_sync fallback: %s", e)

    return fallback_result


async def get_osrm_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    info = await get_osrm_route(lat1, lon1, lat2, lon2)
    return info["distance_m"]


async def get_osrm_distance_matrix_m(src_lat: float, src_lon: float, destinations: list[tuple[float, float]]) -> list[float]:
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

    coords = [f"{src_lon:.6f},{src_lat:.6f}"]
    for lat, lon in valid_dests:
        coords.append(f"{lon:.6f},{lat:.6f}")

    coords_str    = ";".join(coords)
    n             = len(valid_dests) + 1
    radiuses_str  = ";".join([str(OSRM_LIVE_SNAP_RADIUS_M)] * n)
    url = (
        f"/table/v1/driving/{coords_str}"
        f"?sources=0&annotations=distance&radiuses={radiuses_str}"
    )
    try:
        resp = await _osrm_client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and data.get("distances"):
                dists = data["distances"][0][1:]
                if len(dists) == len(valid_dests):
                    for j, osrm_d in enumerate(dists):
                        if osrm_d is not None:
                            fallback_dists[valid_indices[j]] = float(osrm_d)
    except Exception as e:
        logger.debug("[OSRM] get_osrm_distance_matrix_m fallback: %s", e)

    return fallback_dists


async def get_osrm_segment_geometry(lat1: float, lon1: float, lat2: float, lon2: float) -> List[List[float]]:
    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return [[lon1, lat1], [lon2, lat2]]

    straight_dist = haversine_m_math(lat1, lon1, lat2, lon2)

    url = (
        f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
        f"?radiuses=15;15&snapping=any&continue_straight=true&overview=full&geometries=geojson"
    )
    try:
        resp = await _osrm_client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                route_dist = float(route.get("distance", 0))
                if straight_dist > 50 and route_dist > straight_dist * 3.0:
                    return [[lon1, lat1], [lon2, lat2]]
                
                geometry = route["geometry"]
                return geometry["coordinates"]
    except Exception as e:
        logger.debug("[OSRM] get_osrm_segment_geometry error: %s", e)
    
    return [[lon1, lat1], [lon2, lat2]]

async def get_osrm_route_geometry(coordinates: list[tuple[float, float]]) -> list[list[float]]:
    if len(coordinates) < 2:
        return [[lon, lat] for lat, lon in coordinates]
        
    valid = [c for c in coordinates if is_within_extract(c[0], c[1])]
    if len(valid) < 2:
        return [[lon, lat] for lat, lon in coordinates]

    full_coords = []
    
    for i in range(len(valid) - 1):
        lat1, lon1 = valid[i]
        lat2, lon2 = valid[i + 1]

        # Skip asking OSRM if points are essentially stationary
        dist = haversine_m_math(lat1, lon1, lat2, lon2)
        if dist < 5.0:
            continue

        seg = await get_osrm_segment_geometry(lat1, lon1, lat2, lon2)

        if full_coords and seg:
            # Skip the first point of the segment to avoid duplicating the join point
            full_coords.extend(seg[1:])
        else:
            full_coords.extend(seg)
            
    if not full_coords:
        return [[lon, lat] for lat, lon in coordinates]
        
    return full_coords
