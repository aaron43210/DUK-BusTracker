"""
services/osrm_client.py
Asynchronous client for interacting with the local OSRM routing server.
Falls back to Haversine straight-line distance if the server is unreachable.
"""
import httpx
import logging
import math
import os
from typing import List, Tuple

logger = logging.getLogger(__name__)

OSRM_BASE_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5001")


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


OSRM_LIVE_SNAP_RADIUS_M = 40   # 40m snap radius: allows bus bays and campus stops to snap cleanly to the road network


async def snap_live_gps(lat: float, lon: float) -> tuple[float, float]:
    """Snap a live raw GPS coordinate to the nearest road within OSRM_LIVE_SNAP_RADIUS_M."""
    url = f"{OSRM_BASE_URL}/nearest/v1/driving/{lon:.6f},{lat:.6f}?number=1&radiuses={OSRM_LIVE_SNAP_RADIUS_M}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("waypoints"):
                    pt = data["waypoints"][0]["location"]
                    return float(pt[1]), float(pt[0])  # returns lat, lon
    except Exception as e:
        logger.debug("[OSRM] Failed to snap live GPS: %s", e)
    return lat, lon


async def get_osrm_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Fetch the real driving distance in metres from the local OSRM server.
    If the server is down or returns an error, gracefully fallback to Haversine math.
    """
    url = f"{OSRM_BASE_URL}/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?radiuses={OSRM_LIVE_SNAP_RADIUS_M};{OSRM_LIVE_SNAP_RADIUS_M}&continue_straight=false&overview=false"
    
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2.5) as client:
            resp = await client.get(url)
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
        
    coords = [f"{src_lon:.6f},{src_lat:.6f}"]
    for (lat, lon) in destinations:
        coords.append(f"{lon:.6f},{lat:.6f}")
        
    coords_str = ";".join(coords)
    radiuses_str = ";".join([str(OSRM_LIVE_SNAP_RADIUS_M)] * (len(destinations) + 1))
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coords_str}?sources=0&annotations=distance&radiuses={radiuses_str}"
    
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=3.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("distances"):
                    dists = data["distances"][0][1:]
                    if len(dists) == len(destinations):
                        return [float(d) if d is not None else haversine_m_math(src_lat, src_lon, destinations[i][0], destinations[i][1]) for i, d in enumerate(dists)]
    except Exception as e:
        logger.debug("[OSRM] Failed to fetch distance matrix (fallback to Haversine): %s", e)
        
    return [haversine_m_math(src_lat, src_lon, d_lat, d_lon) for (d_lat, d_lon) in destinations]





async def get_osrm_segment_geometry(lat1: float, lon1: float, lat2: float, lon2: float) -> List[List[float]]:
    """
    Fetch the turn-by-turn road geometry coordinates [[lon, lat], ...] connecting two consecutive points.
    """
    url = f"{OSRM_BASE_URL}/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}?radiuses={OSRM_LIVE_SNAP_RADIUS_M};{OSRM_LIVE_SNAP_RADIUS_M}&snapping=any&continue_straight=false&overview=full&geometries=geojson"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2.5) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "Ok" and data.get("routes") and len(data["routes"]) > 0:
                    return data["routes"][0]["geometry"]["coordinates"]
                else:
                    logger.debug(f"[OSRM] Segment geometry code not Ok: {data.get('code')}")
            else:
                logger.debug(f"[OSRM] Segment geometry failed status {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.debug("[OSRM] Failed to fetch segment geometry: %s", e)

    return [[lon1, lat1], [lon2, lat2]]


async def get_osrm_route_geometry(coordinates: list[tuple[float, float]]) -> List[List[float]]:
    """
    Fetch road-snapped turn-by-turn geometry coordinates [[lon, lat], ...] from OSRM for a list of (lat, lon) waypoints.
    Routes point-by-point to guarantee high reliability even if some segments are unroutable.
    """
    if len(coordinates) < 2:
        return [[lon, lat] for lat, lon in coordinates]

    full_road_coords = []
    
    for i in range(len(coordinates) - 1):
        lat1, lon1 = coordinates[i]
        lat2, lon2 = coordinates[i + 1]
        
        # We reuse our robust segment fetcher
        seg_coords = await get_osrm_segment_geometry(lat1, lon1, lat2, lon2)
        
        if full_road_coords and seg_coords:
            # Avoid duplicate points at the stitched boundary
            full_road_coords.extend(seg_coords[1:])
        else:
            full_road_coords.extend(seg_coords)
            
    return full_road_coords if full_road_coords else [[lon, lat] for lat, lon in coordinates]

async def get_osrm_match_geometry(coordinates: list[tuple[float, float]]) -> List[List[float]]:
    """
    Fetch map-matched route geometry coordinates from OSRM for a GPS trace.
    OSRM match/v1 snaps noisy points to the logical driven path.
    """
    if len(coordinates) < 2:
        return [[lon, lat] for lat, lon in coordinates]

    full_road_coords = []
    chunk_size = 90  # OSRM match allows up to 100 points per request
    for i in range(0, len(coordinates) - 1, chunk_size - 1):
        chunk = coordinates[i:i + chunk_size]
        if len(chunk) < 2:
            continue
            
        coords_str = ";".join([f"{lon:.6f},{lat:.6f}" for lat, lon in chunk])
        url = f"{OSRM_BASE_URL}/match/v1/driving/{coords_str}?overview=full&geometries=geojson&tidy=true"
        
        chunk_handled = False
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "Ok" and data.get("matchings") and len(data["matchings"]) > 0:
                        # Find the longest matching or concatenate all matchings for this chunk
                        pts = []
                        for m in data["matchings"]:
                            pts.extend(m["geometry"]["coordinates"])
                        
                        if full_road_coords and pts:
                            full_road_coords.extend(pts[1:])
                        else:
                            full_road_coords.extend(pts)
                        chunk_handled = True
        except Exception as e:
            logger.debug("[OSRM] Match geometry error: %s", e)
            
        if not chunk_handled:
            # Fallback to route if match fails
            try:
                route_url = f"{OSRM_BASE_URL}/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
                async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                    resp = await client.get(route_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("code") == "Ok" and data.get("routes") and len(data["routes"]) > 0:
                            pts = data["routes"][0]["geometry"]["coordinates"]
                            if full_road_coords and pts:
                                full_road_coords.extend(pts[1:])
                            else:
                                full_road_coords.extend(pts)
                            chunk_handled = True
            except Exception:
                pass
                
        if not chunk_handled:
            # Final fallback: use our robust pairwise routing instead of straight lines
            try:
                pairwise_pts = await get_osrm_route_geometry(chunk)
                if full_road_coords and pairwise_pts:
                    full_road_coords.extend(pairwise_pts[1:])
                else:
                    full_road_coords.extend(pairwise_pts)
                chunk_handled = True
            except Exception:
                pass
                
        if not chunk_handled:
            # Ultimate fallback if everything fails
            pts = [[lon, lat] for lat, lon in chunk]
            if full_road_coords and pts:
                full_road_coords.extend(pts[1:])
            else:
                full_road_coords.extend(pts)

    return full_road_coords if full_road_coords else [[lon, lat] for lat, lon in coordinates]
