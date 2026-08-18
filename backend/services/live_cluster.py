"""
Server-side live clustering — prevents stationary jitter from ever
reaching the client, so the frontend animator only receives points
that represent real movement.
"""
from services.osrm_client import haversine_m_math

class LiveClusterState:
    def __init__(self, radius_m: float = 15.0):
        self.radius_m = radius_m
        self.anchor_lat = None
        self.anchor_lon = None
        self.sample_count = 0

    def process(self, lat: float, lon: float) -> tuple[float, float, bool]:
        """
        Returns (output_lat, output_lon, should_broadcast).
        should_broadcast=False means this point is redundant jitter
        and the frontend doesn't need to see it at all.
        """
        if self.anchor_lat is None:
            self.anchor_lat, self.anchor_lon = lat, lon
            self.sample_count = 1
            return lat, lon, True

        dist = haversine_m_math(self.anchor_lat, self.anchor_lon, lat, lon)

        if dist < self.radius_m:
            # Update centroid with running average, but don't force a
            # new broadcast unless the centroid moved meaningfully
            self.sample_count += 1
            alpha = 1.0 / self.sample_count
            new_lat = (1 - alpha) * self.anchor_lat + alpha * lat
            new_lon = (1 - alpha) * self.anchor_lon + alpha * lon
            moved = haversine_m_math(self.anchor_lat, self.anchor_lon, new_lat, new_lon)
            self.anchor_lat, self.anchor_lon = new_lat, new_lon
            # Only rebroadcast if centroid drifted > 3m (avoids spamming identical points)
            return new_lat, new_lon, moved > 3.0
        else:
            # Real movement — reset anchor
            self.anchor_lat, self.anchor_lon = lat, lon
            self.sample_count = 1
            return lat, lon, True


_LIVE_CLUSTER = LiveClusterState(radius_m=15.0)


def apply_live_clustering(lat: float, lon: float) -> tuple[float, float, bool]:
    return _LIVE_CLUSTER.process(lat, lon)
