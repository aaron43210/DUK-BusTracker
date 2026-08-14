"""
services/geofence.py — Stop proximity & nearest-stop calculations.
Uses Haversine distance (more accurate than the old Euclidean approach).
"""
import math
from typing import Optional
from services.valhalla_client import get_valhalla_distance_matrix_m


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



def find_nearest_stop_math(lat: float, lon: float, stops: list[dict], threshold_km: float = 0.4) -> Optional[dict]:
    """
    Synchronous Haversine-based nearest stop lookup. Used for bulk historical logs.
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


def cluster_gps_points(
    points: list,
    radius_km: float = 0.030,
    key_lat: str | None = None,
    key_lon: str | None = None,
) -> list:
    """
    Group consecutive GPS points within `radius_km` of each other into clusters
    and return a list of centroid points.

    Accepts two formats:
      - list of (lat, lon) tuples  →  key_lat/key_lon must be None
      - list of dicts              →  key_lat/key_lon are the dict keys, e.g. 'lat'/'lon'

    Returns the same format as the input (tuples → tuples, dicts → dicts).
    The centroid dict preserves all keys from the last point in the cluster.
    """
    if not points:
        return []

    def _get(pt):
        if key_lat:
            return float(pt[key_lat]), float(pt[key_lon])
        return float(pt[0]), float(pt[1])

    clusters: list[list] = []
    current_cluster = [points[0]]
    anchor_lat, anchor_lon = _get(points[0])

    for pt in points[1:]:
        pt_lat, pt_lon = _get(pt)
        if haversine_km(anchor_lat, anchor_lon, pt_lat, pt_lon) < radius_km:
            current_cluster.append(pt)
        else:
            clusters.append(current_cluster)
            current_cluster = [pt]
            anchor_lat, anchor_lon = pt_lat, pt_lon

    clusters.append(current_cluster)

    centroids = []
    for cluster in clusters:
        lats = [_get(p)[0] for p in cluster]
        lons = [_get(p)[1] for p in cluster]
        c_lat = sum(lats) / len(lats)
        c_lon = sum(lons) / len(lons)
        if key_lat:
            # Merge the last item's fields and overwrite lat/lon with centroid
            merged = {**cluster[-1], key_lat: c_lat, key_lon: c_lon}
            centroids.append(merged)
        else:
            centroids.append((c_lat, c_lon))

    # Deduplicate: drop consecutive centroids still within radius
    deduped = [centroids[0]]
    for pt in centroids[1:]:
        pt_lat, pt_lon = _get(pt)
        last_lat, last_lon = _get(deduped[-1])
        if haversine_km(last_lat, last_lon, pt_lat, pt_lon) >= radius_km:
            deduped.append(pt)

    return deduped


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
    distances_m = await get_valhalla_distance_matrix_m(bus_lat, bus_lon, destinations)

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
