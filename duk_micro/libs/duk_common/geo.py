# libs/duk_common/geo.py
import math
from typing import Optional

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    for val in (lat1, lon1, lat2, lon2):
        if not math.isfinite(val):
            raise ValueError("Coordinates must be finite numbers")
    if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
        raise ValueError("Latitude must be between -90 and 90")
    if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
        raise ValueError("Longitude must be between -180 and 180")
        
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    a = min(1.0, max(0.0, a))
    return R * 2 * math.asin(math.sqrt(a))

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0


def cluster_gps_points(points: list, radius_km: float = 0.030) -> list:
    """
    Reduce a list of (lat, lon, ...) tuples by dropping points that are less
    than `radius_km` from the previously accepted point.

    Preserves arbitrary extra tuple elements (e.g. timestamps).

    Args:
        points:    List of tuples where [0]=lat, [1]=lon.
        radius_km: Minimum distance between accepted points.

    Returns:
        Filtered list in the same format as the input.
    """
    if not points:
        return []
    clustered = [points[0]]
    for p in points[1:]:
        if haversine_km(clustered[-1][0], clustered[-1][1], p[0], p[1]) >= radius_km:
            clustered.append(p)
    return clustered

