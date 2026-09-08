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
from dataclasses import dataclass

logger = logging.getLogger(__name__)

OSRM_BASE_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5001")

_client: Optional[httpx.AsyncClient] = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=OSRM_BASE_URL,
            trust_env=False,
            timeout=httpx.Timeout(connect=1.0, read=2.5, write=1.0, pool=1.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


REGION_BOUNDS = {"min_lat": 8.2, "max_lat": 9.0, "min_lon": 76.5, "max_lon": 77.5}
_SNAP_RADII_M = [30, 60, 120]
BUS_FACTOR = 1.35


def is_within_extract(lat: float, lon: float) -> bool:
    return (
        REGION_BOUNDS["min_lat"] <= lat <= REGION_BOUNDS["max_lat"]
        and REGION_BOUNDS["min_lon"] <= lon <= REGION_BOUNDS["max_lon"]
    )


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


async def snap_to_road(lat: float, lon: float, bearing: Optional[float] = None) -> Tuple[float, float]:
    if not is_within_extract(lat, lon):
        return lat, lon
    bearing_param = f"&bearings={int(bearing)},45" if bearing is not None else ""
    for radius in _SNAP_RADII_M:
        url = f"/nearest/v1/driving/{lon:.6f},{lat:.6f}?number=1&radiuses={radius}{bearing_param}"
        try:
            resp = await _get_client().get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("waypoints"):
                    pt = data["waypoints"][0]["location"]
                    return float(pt[1]), float(pt[0])
        except Exception as e:
            logger.debug("[OSRM] Snap error r=%dm: %s", radius, e)
            break
    return lat, lon


async def get_osrm_street_name(lat: float, lon: float) -> Optional[str]:
    """Fetch the nearest street name from local OSRM for instant fallback."""
    if not is_within_extract(lat, lon):
        return None
    url = f"/nearest/v1/driving/{lon:.6f},{lat:.6f}?number=1"
    try:
        resp = await _get_client().get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and data.get("waypoints"):
                return data["waypoints"][0].get("name")
    except Exception as e:
        logger.debug("[OSRM] Street name error: %s", e)
    return None



async def get_osrm_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return haversine_m(lat1, lon1, lat2, lon2)
    url = f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=false"
    try:
        resp = await _get_client().get(url)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            return float(data["routes"][0]["distance"])
    except Exception as e:
        logger.debug("[OSRM] Distance error: %s", e)
    return haversine_m(lat1, lon1, lat2, lon2)


async def get_osrm_duration_s(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    if not (is_within_extract(lat1, lon1) and is_within_extract(lat2, lon2)):
        return (haversine_m(lat1, lon1, lat2, lon2) / 1000.0 / 30.0) * 3600
    url = f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?overview=false"
    try:
        resp = await _get_client().get(url)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            return float(data["routes"][0]["duration"])
    except Exception as e:
        logger.debug("[OSRM] Duration error: %s", e)
    return (haversine_m(lat1, lon1, lat2, lon2) / 1000.0 / 30.0) * 3600


async def get_osrm_distance_matrix_m(
    origin_lat: float,
    origin_lon: float,
    destinations: List[Tuple[float, float]],
) -> List[float]:
    if not destinations:
        return []
    coords_str = f"{origin_lon:.6f},{origin_lat:.6f}"
    for dlat, dlon in destinations:
        coords_str += f";{dlon:.6f},{dlat:.6f}"
    dst_indices = ";".join(str(i + 1) for i in range(len(destinations)))
    url = f"/table/v1/driving/{coords_str}?sources=0&destinations={dst_indices}&annotations=distance"
    try:
        resp = await _get_client().get(url)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("distances"):
            row = data["distances"][0]
            return [
                float(d) if d is not None else haversine_m(origin_lat, origin_lon, dlat, dlon)
                for (dlat, dlon), d in zip(destinations, row)
            ]
    except Exception as e:
        logger.debug("[OSRM] Distance matrix error: %s", e)
    return [haversine_m(origin_lat, origin_lon, dlat, dlon) for dlat, dlon in destinations]


async def get_osrm_route_geometry(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> Optional[List[Tuple[float, float]]]:
    url = (
        f"/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
        f"?overview=full&geometries=geojson"
    )
    try:
        resp = await _get_client().get(url)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            coords = data["routes"][0]["geometry"]["coordinates"]
            return [(lat, lon) for lon, lat in coords]
    except Exception as e:
        logger.debug("[OSRM] Route geometry error: %s", e)
    return None

async def get_osrm_route_geometry_multi(
    waypoints: List[Tuple[float, float]],
) -> List[List[float]]:
    """
    Multi-waypoint route geometry (route line through N stops).
    waypoints: list of (lat, lon).
    Returns list of [lon, lat] pairs (GeoJSON order) — matches frontend map format.
    Falls back to straight-line waypoints (still [lon, lat]) on OSRM failure.
    Uses the shared pooled client — NOT a new httpx.AsyncClient per call.
    """
    if len(waypoints) < 2:
        return [[w[1], w[0]] for w in waypoints]
    coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
    url = f"/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    try:
        resp = await _get_client().get(url)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("routes"):
            return [[lon, lat] for lon, lat in data["routes"][0]["geometry"]["coordinates"]]
    except Exception as e:
        logger.debug("[OSRM] Multi-route geometry error: %s", e)
    return [[w[1], w[0]] for w in waypoints]

async def get_osrm_match_geometry(
    waypoints: List[Tuple[float, float]],
) -> List[List[float]]:
    """
    Map matching (HMM) for a GPS trace.
    waypoints: list of (lat, lon).
    Returns list of [lon, lat] pairs (GeoJSON order) snapped to the road network.
    Uses the OSRM /match endpoint instead of /route to avoid detours and false routing.
    """
    if len(waypoints) < 2:
        return [[w[1], w[0]] for w in waypoints]
    coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
    radiuses_str = ";".join(["35"] * len(waypoints))
    url = f"/match/v1/driving/{coords_str}?overview=full&geometries=geojson&tidy=true&gaps=ignore&radiuses={radiuses_str}"
    try:
        resp = await _get_client().get(url)
        data = resp.json()
        if data.get("code") == "Ok" and data.get("matchings"):
            coords = []
            for match in data["matchings"]:
                coords.extend([[lon, lat] for lon, lat in match["geometry"]["coordinates"]])
            return coords
    except Exception as e:
        logger.debug("[OSRM] Match geometry error: %s", e)
    return [[w[1], w[0]] for w in waypoints]

async def snap_points_individually(
    waypoints: List[Tuple[float, float]],
) -> List[List[float]]:
    """
    Snaps each GPS point independently to the closest road segment using OSRM's /nearest endpoint.
    This completely bypasses OSRM's routing engine, avoiding false route zig-zags caused by broken OSM connectivity data.
    """
    if not waypoints:
        return []
    
    # Process concurrently using the existing snap_to_road function
    # snap_to_road returns (lat, lon), but GeoJSON expects [lon, lat]
    tasks = [snap_to_road(lat, lon) for lat, lon in waypoints]
    snapped = await asyncio.gather(*tasks)
    
    return [[lon, lat] for lat, lon in snapped]

async def close_client() -> None:
    """Call on service shutdown to release the pooled connection."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
