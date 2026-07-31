"""
services/geofence.py — Stop proximity & nearest-stop calculations.
Uses Haversine distance (more accurate than the old Euclidean approach).
"""
import math
from typing import Optional
from services.osrm_client import get_osrm_distance_m, get_osrm_distance_matrix_m

# Keep haversine_km for legacy synchronous calls if any still exist outside this module
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the Haversine great-circle distance in kilometres."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


async def find_nearest_stop(lat: float, lon: float, stops: list[dict], threshold_km: float = 0.4) -> Optional[dict]:
    """
    Find the nearest bus stop within the threshold (driving distance).
    stops: list of dicts with keys {id, name, lat, lon, order_index}
    Returns the stop dict (with added 'distance_km') or None if nothing is close.
    """
    if not stops:
        return None
        
    destinations = [(s["lat"], s["lon"]) for s in stops]
    distances_m = await get_osrm_distance_matrix_m(lat, lon, destinations)
    
    best_stop = None
    best_dist = float("inf")

    for stop, dist_m in zip(stops, distances_m):
        dist_km = dist_m / 1000.0
        if dist_km < best_dist:
            best_dist = dist_km
            best_stop = stop

    if best_stop and best_dist <= threshold_km:
        return {**best_stop, "distance_km": round(best_dist, 4)}
    return None

def find_nearest_stop_math(lat: float, lon: float, stops: list[dict], threshold_km: float = 0.4) -> Optional[dict]:
    """
    Synchronous mathematical fallback for bulk historical logs.
    """
    best_stop = None
    best_dist = float("inf")

    for stop in stops:
        dist = haversine_km(lat, lon, stop["lat"], stop["lon"])
        if dist < best_dist:
            best_dist = dist
            best_stop = stop

    if best_stop and best_dist <= threshold_km:
        return {**best_stop, "distance_km": round(best_dist, 4)}
    return None


async def get_stops_ahead(
    bus_lat: float,
    bus_lon: float,
    stops: list[dict],
    direction: str = "forward",
    visited_stops: list[int] = None,
) -> list[dict]:
    """
    Return stops that the bus has NOT yet reached, ordered by route sequence, applying soft-skip logic.
    direction='forward': ascending order_index
    direction='reverse': descending order_index
    """
    visited_stops = visited_stops or []
    ordered = sorted(stops, key=lambda s: s["order_index"], reverse=(direction == "reverse"))
    if not ordered:
        return []

    # Max sequence of visited stops to detect skipped/missed stops
    max_visited_order = -1
    for s in ordered:
        if s["id"] in visited_stops and s["order_index"] > max_visited_order:
            max_visited_order = s["order_index"]

    destinations = [(s["lat"], s["lon"]) for s in ordered]
    distances_m = await get_osrm_distance_matrix_m(bus_lat, bus_lon, destinations)

    result = []
    for stop, dist_m in zip(ordered, distances_m):
        dist_km = dist_m / 1000.0
        
        # 1. Hard skipped: literally visited it already
        if stop["id"] in visited_stops:
            continue
            
        # 2. Soft skip: skipped it in sequence, and drove > 1.5km away
        if stop["order_index"] < max_visited_order:
            if dist_km >= 1.5:
                # Mark as deviated so frontend knows, but we still return it in the list 
                # so the ETA engine can process it if requested, OR we can suppress it.
                # Actually, the API returns it with a 'deviated' flag.
                result.append({**stop, "distance_km": round(dist_km, 3), "deviated": True})
                continue
                
        # Normal unvisited stop OR a missed stop being recovered (dist < 1.5)
        result.append({**stop, "distance_km": round(dist_km, 3), "deviated": False})

    return result
