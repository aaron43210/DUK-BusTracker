# libs/duk_common/osrm_client.py
"""
Shared async OSRM client — ported from backend/services/osrm_client.py.
Used by: gps-ingestion (snap), trip-lifecycle (distances + ETA), tracking-service (route geometry).
Falls back to Haversine straight-line distance when OSRM is unreachable.
"""
import httpx
import logging
import math
import os
import asyncio
from typing import List, Optional, Tuple
from libs.duk_common.geo import haversine_m

logger = logging.getLogger(__name__)
# Suppress expected 400 Bad Request logs from httpx when OSRM snap-to-road retries with wider radii
logging.getLogger("httpx").setLevel(logging.WARNING)

OSRM_BASE_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5001").rstrip("/")
if not OSRM_BASE_URL.startswith(("http://", "https://")):
    raise ValueError("OSRM_URL must use http:// or https://")

_client: Optional[httpx.AsyncClient] = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=OSRM_BASE_URL,
            trust_env=False,
            timeout=httpx.Timeout(connect=2.0, read=60.0, write=5.0, pool=2.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client

async def close_client() -> None:
    """Close the shared OSRM HTTP client during service shutdown."""
    global _client
    client = _client
    _client = None
    if client is not None and not client.is_closed:
        await client.aclose()


# Must match the osmosis crop box in deploy/osrm-setup.sh exactly
# (top=8.70 left=76.50 bottom=8.20 right=77.30). Advertising a larger area than
# the graph actually covers makes OSRM get queried for roads it doesn't have →
# NoSegment/empty → wasted retries then Haversine fallback.
REGION_BOUNDS = {"min_lat": 8.2, "max_lat": 8.70, "min_lon": 76.5, "max_lon": 77.30}
_SNAP_RADII_M = [30, 60, 120]
BUS_FACTOR = 1.35


def _validate_coordinate(lat: float, lon: float) -> None:
    if not (
        math.isfinite(lat)
        and math.isfinite(lon)
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon <= 180.0
    ):
        raise ValueError(f"Invalid coordinates: lat={lat}, lon={lon}")

def _validate_waypoints(waypoints: List[Tuple[float, float]]) -> None:
    for lat, lon in waypoints:
        _validate_coordinate(lat, lon)


def is_within_extract(lat: float, lon: float) -> bool:
    if not (
        math.isfinite(lat)
        and math.isfinite(lon)
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon <= 180.0
    ):
        return False
    return (
        REGION_BOUNDS["min_lat"] <= lat <= REGION_BOUNDS["max_lat"]
        and REGION_BOUNDS["min_lon"] <= lon <= REGION_BOUNDS["max_lon"]
    )


async def snap_to_road(lat: float, lon: float, bearing: Optional[float] = None) -> Tuple[float, float]:
    _validate_coordinate(lat, lon)
    if not is_within_extract(lat, lon):
        return lat, lon
        
    if bearing is not None:
        if not math.isfinite(bearing) or not 0 <= bearing <= 360:
            raise ValueError("bearing must be between 0 and 360 degrees")
            
    bearing_param = f"&bearings={int(bearing)},45" if bearing is not None else ""
    for radius in _SNAP_RADII_M:
        url = f"/nearest/v1/driving/{lon:.6f},{lat:.6f}?number=1&radiuses={radius}{bearing_param}"
        try:
            resp = await _get_client().get(url)
            if resp.status_code == 200:
                data = resp.json()
                waypoints = data.get("waypoints", [])
                if data.get("code") == "Ok" and waypoints and isinstance(waypoints, list):
                    pt = waypoints[0].get("location")
                    if pt and isinstance(pt, list) and len(pt) >= 2:
                        return float(pt[1]), float(pt[0])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("[OSRM] Snap failed at radius=%dm: %s", radius, exc)
            continue
    return lat, lon


async def get_osrm_street_name(lat: float, lon: float) -> Optional[str]:
    """Fetch the nearest street name from local OSRM for instant fallback."""
    _validate_coordinate(lat, lon)
    if not is_within_extract(lat, lon):
        return None
    url = f"/nearest/v1/driving/{lon:.6f},{lat:.6f}?number=1"
    try:
        resp = await _get_client().get(url)
        if resp.status_code == 200:
            data = resp.json()
            waypoints = data.get("waypoints", [])
            if data.get("code") == "Ok" and waypoints and isinstance(waypoints, list):
                return waypoints[0].get("name")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[OSRM] Street name request failed: %s", exc)
    return None


async def get_osrm_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    _validate_coordinate(lat1, lon1)
    _validate_coordinate(lat2, lon2)
    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return haversine_m(lat1, lon1, lat2, lon2)
    url = f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=false"
    try:
        resp = await _get_client().get(url)
        resp.raise_for_status()
        data = resp.json()
        routes = data.get("routes", [])
        if data.get("code") == "Ok" and routes and isinstance(routes, list):
            return float(routes[0].get("distance", 0.0))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[OSRM] Distance request failed: %s", exc)
    return haversine_m(lat1, lon1, lat2, lon2)


async def get_osrm_duration_s(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    _validate_coordinate(lat1, lon1)
    _validate_coordinate(lat2, lon2)
    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return (haversine_m(lat1, lon1, lat2, lon2) / 1000.0 / 30.0) * 3600
    url = f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=false"
    try:
        resp = await _get_client().get(url)
        resp.raise_for_status()
        data = resp.json()
        routes = data.get("routes", [])
        if data.get("code") == "Ok" and routes and isinstance(routes, list):
            return float(routes[0].get("duration", 0.0))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[OSRM] Duration request failed: %s", exc)
    return (haversine_m(lat1, lon1, lat2, lon2) / 1000.0 / 30.0) * 3600


async def get_osrm_distance_matrix_m(
    origin_lat: float,
    origin_lon: float,
    destinations: List[Tuple[float, float]],
) -> List[float]:
    if not destinations:
        return []
    _validate_coordinate(origin_lat, origin_lon)
    _validate_waypoints(destinations)
    
    coords_str = f"{origin_lon:.6f},{origin_lat:.6f}"
    for dlat, dlon in destinations:
        coords_str += f";{dlon:.6f},{dlat:.6f}"
    dst_indices = ";".join(str(i + 1) for i in range(len(destinations)))
    url = f"/table/v1/driving/{coords_str}?sources=0&destinations={dst_indices}&annotations=distance"
    try:
        resp = await _get_client().get(url)
        resp.raise_for_status()
        data = resp.json()
        distances = data.get("distances", [])
        if data.get("code") == "Ok" and distances and isinstance(distances, list):
            row = distances[0]
            if isinstance(row, list):
                return [
                    float(d) if d is not None else haversine_m(origin_lat, origin_lon, dlat, dlon)
                    for (dlat, dlon), d in zip(destinations, row)
                ]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[OSRM] Distance matrix request failed: %s", exc)
    return [haversine_m(origin_lat, origin_lon, dlat, dlon) for dlat, dlon in destinations]


async def get_osrm_route_geometry(
    lat1: float, lon1: float, lat2: float, lon2: float, bearing: Optional[float] = None
) -> Optional[List[Tuple[float, float]]]:
    _validate_coordinate(lat1, lon1)
    _validate_coordinate(lat2, lon2)
    bearing_param = f"&bearings={int(bearing)},45;{int(bearing)},45" if bearing is not None else ""
    url = (
        f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
        f"?overview=full&geometries=geojson{bearing_param}"
    )
    try:
        resp = await _get_client().get(url)
        resp.raise_for_status()
        data = resp.json()
        routes = data.get("routes", [])
        if data.get("code") == "Ok" and routes and isinstance(routes, list):
            geometry = routes[0].get("geometry", {})
            if isinstance(geometry, dict):
                coords = geometry.get("coordinates", [])
                if isinstance(coords, list):
                    return [(lat, lon) for lon, lat in coords]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[OSRM] Route geometry request failed: %s", exc)
    return None

async def _fetch_geometry_chunk(url_path: str) -> Optional[dict]:
    """Fetch from local OSRM (respects our custom car.lua and patch_osm.py)."""
    try:
        resp = await _get_client().get(url_path)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == "Ok":
            return data
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[OSRM] Local API geometry chunk request failed: %s", exc)
    return None

async def get_osrm_route_geometry_multi(
    waypoints: List[Tuple[float, float]],
) -> List[List[float]]:
    """
    Multi-waypoint route geometry (route line through N stops).
    Chunks waypoints into batches of 50 to avoid public OSRM limits & local OSRM timeouts.
    """
    if len(waypoints) < 2:
        return [[w[1], w[0]] for w in waypoints]
    _validate_waypoints(waypoints)
        
    all_coords = []
    chunk_size = 50
    for i in range(0, len(waypoints) - 1, chunk_size - 1):
        chunk = waypoints[i:i + chunk_size]
        if len(chunk) < 2:
            continue
            
        coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in chunk)
        url = f"/route/v1/driving/{coords_str}?overview=full&geometries=geojson&continue_straight=true"
        
        data = await _fetch_geometry_chunk(url)
        routes = data.get("routes", []) if data else []
        if data and routes and isinstance(routes, list):
            geometry = routes[0].get("geometry", {})
            if isinstance(geometry, dict):
                coords = geometry.get("coordinates", [])
                if isinstance(coords, list):
                    chunk_coords = [[lon, lat] for lon, lat in coords]
                    # avoid duplicating the connecting point
                    if i > 0 and chunk_coords:
                        chunk_coords = chunk_coords[1:]
                    all_coords.extend(chunk_coords)
                    continue
        
        # Fallback to straight lines for this chunk
        chunk_coords = [[w[1], w[0]] for w in chunk]
        if i > 0 and chunk_coords:
            chunk_coords = chunk_coords[1:]
        all_coords.extend(chunk_coords)
            
    return all_coords

async def get_osrm_match_geometry(
    waypoints: List[Tuple[float, float]]
) -> List[List[float]]:
    """
    Map matching (HMM) for a GPS trace.
    Chunks into batches of 50 to avoid limits.
    """
    if len(waypoints) < 2:
        return [[w[1], w[0]] for w in waypoints]
    _validate_waypoints(waypoints)
        
    all_coords = []
    chunk_size = 50
    for i in range(0, len(waypoints) - 1, chunk_size - 1):
        chunk = waypoints[i:i + chunk_size]
        if len(chunk) < 2:
            continue
            
        coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in chunk)
        radiuses_str = ";".join(["20"] * len(chunk))
        url = f"/match/v1/driving/{coords_str}?overview=full&geometries=geojson&tidy=true&gaps=ignore&radiuses={radiuses_str}"
        
        data = await _fetch_geometry_chunk(url)
        matchings = data.get("matchings", []) if data else []
        if data and matchings and isinstance(matchings, list):
            chunk_coords = []
            for match in matchings:
                geometry = match.get("geometry", {})
                if isinstance(geometry, dict):
                    coords = geometry.get("coordinates", [])
                    if isinstance(coords, list):
                        chunk_coords.extend([[lon, lat] for lon, lat in coords])
            if i > 0 and chunk_coords:
                chunk_coords = chunk_coords[1:]
            all_coords.extend(chunk_coords)
        else:
            fallback_coords = await get_osrm_route_geometry_multi(chunk)
            if i > 0 and fallback_coords:
                fallback_coords = fallback_coords[1:]
            all_coords.extend(fallback_coords)

    return all_coords
