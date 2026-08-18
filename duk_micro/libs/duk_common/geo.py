# libs/duk_common/geo.py
import math
from typing import Optional
from scipy.spatial import cKDTree
import numpy as np

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
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

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0

class StopIndex:
    """KD-tree backed nearest stop lookup. O(log n) vs O(n) linear scan."""
    def __init__(self, stops: list[dict]):
        self.stops = stops
        if stops:
            coords = np.array([[s["lat"], s["lon"]] for s in stops])
            self.tree = cKDTree(coords)
        else:
            self.tree = None

    def nearest(self, lat: float, lon: float, k: int = 1, threshold_km: float = 0.4) -> Optional[dict]:
        if not self.tree or not self.stops:
            return None
        dist, idx = self.tree.query([lat, lon], k=min(k, len(self.stops)))
        if k == 1:
            dist_km = haversine_km(lat, lon, self.stops[idx]["lat"], self.stops[idx]["lon"])
            if dist_km <= threshold_km:
                return {**self.stops[idx], "distance_km": round(dist_km, 4)}
            return None
        results = []
        for d, i in zip(dist, idx):
            s = self.stops[i]
            dist_km = haversine_km(lat, lon, s["lat"], s["lon"])
            if dist_km <= threshold_km:
                results.append({**s, "distance_km": round(dist_km, 4)})
        return results

    def nearest_raw(self, lat: float, lon: float) -> Optional[dict]:
        """Always returns nearest, no threshold."""
        if not self.tree or not self.stops:
            return None
        dist, idx = self.tree.query([lat, lon], k=1)
        return self.stops[idx]
