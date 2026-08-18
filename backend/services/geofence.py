"""
services/geofence.py — Stop proximity & nearest-stop calculations.
Uses Haversine distance (more accurate than the old Euclidean approach).
"""
import math
from typing import Optional
from services.osrm_client import get_osrm_distance_matrix_m


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
    key_lat: Optional[str] = None,
    key_lon: Optional[str] = None,
) -> list:
    """
    O(n) single-pass clustering with centroid averaging.
    Accepts list of (lat, lon) tuples or list of dicts.
    """
    if not points:
        return []

    is_dict = key_lat is not None

    def _get(pt) -> tuple[float, float]:
        if is_dict:
            return float(pt[key_lat]), float(pt[key_lon])
        return float(pt[0]), float(pt[1])

    # Pre-extract all coordinates once — avoids repeated _get() in inner loop
    coords = [_get(p) for p in points]

    clusters: list[list[int]] = []   # list of index groups
    current: list[int]        = [0]
    anchor_lat, anchor_lon    = coords[0]

    for i in range(1, len(coords)):
        pt_lat, pt_lon = coords[i]
        if haversine_km(anchor_lat, anchor_lon, pt_lat, pt_lon) < radius_km:
            current.append(i)
        else:
            clusters.append(current)
            current               = [i]
            anchor_lat, anchor_lon = pt_lat, pt_lon
    clusters.append(current)

    centroids: list = []
    for group in clusters:
        lats  = [coords[i][0] for i in group]
        lons  = [coords[i][1] for i in group]
        c_lat = sum(lats) / len(lats)
        c_lon = sum(lons) / len(lons)
        if is_dict:
            merged = {**points[group[-1]], key_lat: c_lat, key_lon: c_lon}
            centroids.append(merged)
        else:
            centroids.append((c_lat, c_lon))

    # Single-pass dedup — avoids second O(n) scan
    if not centroids:
        return centroids

    deduped  = [centroids[0]]
    prev_lat, prev_lon = _get(centroids[0])
    for pt in centroids[1:]:
        pt_lat, pt_lon = _get(pt)
        if haversine_km(prev_lat, prev_lon, pt_lat, pt_lon) >= radius_km:
            deduped.append(pt)
            prev_lat, prev_lon = pt_lat, pt_lon

    return deduped


async def get_stops_ahead(
    bus_lat: float,
    bus_lon: float,
    stops: list[dict],
    direction: str = "forward",
    visited_stops: list[int] = None,
) -> list[dict]:
    visited_stops = visited_stops or []
    visited_set   = set(visited_stops)  # O(1) lookup instead of O(n)

    ordered = sorted(stops, key=lambda s: s["order_index"],
                     reverse=(direction == "reverse"))
    if not ordered:
        return []

    if direction == "forward":
        most_advanced_visited = max(
            (s["order_index"] for s in ordered if s["id"] in visited_set),
            default=-1,
        )
    else:
        most_advanced_visited = min(
            (s["order_index"] for s in ordered if s["id"] in visited_set),
            default=9999,
        )

    destinations  = [(s["lat"], s["lon"]) for s in ordered]
    distances_m   = await get_osrm_distance_matrix_m(bus_lat, bus_lon, destinations)

    result = []
    for stop, dist_m in zip(ordered, distances_m):
        dist_km = dist_m / 1000.0

        if stop["id"] in visited_set:
            continue

        is_behind = False
        if direction == "forward":
            is_behind = stop["order_index"] < most_advanced_visited
        else:
            is_behind = stop["order_index"] > most_advanced_visited

        if is_behind:
            if dist_km >= 1.5:
                result.append({**stop, "distance_km": round(dist_km, 3), "deviated": True})
            else:
                continue
        else:
            result.append({**stop, "distance_km": round(dist_km, 3), "deviated": False})

    return result
