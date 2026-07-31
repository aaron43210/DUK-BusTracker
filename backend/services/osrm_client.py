"""
services/osrm_client.py
Asynchronous client for interacting with the local OSRM routing server.
Falls back to Haversine straight-line distance if the server is unreachable.
"""
import httpx
import logging
import math

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "http://localhost:5001"

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

async def get_osrm_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Fetch the real driving distance in metres from the local OSRM server.
    If the server is down or returns an error, gracefully fallback to Haversine math.
    """
    # OSRM expects coordinates in lon,lat format
    url = f"{OSRM_BASE_URL}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    return float(data["routes"][0]["distance"])
    except Exception as e:
        logger.debug("[OSRM] Failed to fetch distance (fallback to Haversine): %s", e)
        
    return haversine_m_math(lat1, lon1, lat2, lon2)

async def get_osrm_distance_matrix_m(src_lat: float, src_lon: float, destinations: list[tuple[float, float]]) -> list[float]:
    """
    Fetch the real driving distances from a single source to multiple destinations using the OSRM /table API.
    Returns a list of distances in metres, in the exact order of the provided destinations.
    """
    if not destinations:
        return []
        
    coords = [f"{src_lon},{src_lat}"]
    for (lat, lon) in destinations:
        coords.append(f"{lon},{lat}")
        
    coords_str = ";".join(coords)
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coords_str}?sources=0&annotations=distance"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("distances"):
                    # distances[0] is the array of distances from source (0) to all points (including itself at index 0)
                    # We slice from [1:] to skip the distance to itself.
                    dists = data["distances"][0][1:]
                    if len(dists) == len(destinations):
                        return [float(d) for d in dists]
    except Exception as e:
        logger.debug("[OSRM] Failed to fetch distance matrix (fallback to Haversine): %s", e)
        
    # Fallback
    return [haversine_m_math(src_lat, src_lon, d_lat, d_lon) for (d_lat, d_lon) in destinations]

# Synchronous version for the ML training script
def get_osrm_distance_m_sync(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    url = f"{OSRM_BASE_URL}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        with httpx.Client() as client:
            resp = client.get(url, timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    return float(data["routes"][0]["distance"])
    except Exception:
        pass
    return haversine_m_math(lat1, lon1, lat2, lon2)
