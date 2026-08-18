# services/gps-ingestion/osrm_snap.py
"""OSRM road-snapping for gps-ingestion-service."""
import httpx
import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)
OSRM_BASE_URL = os.environ.get("OSRM_URL", "http://127.0.0.1:5001")
_SNAP_RADII_M = [30, 60, 120]


def _is_within_extract(lat: float, lon: float) -> bool:
    return 8.2 <= lat <= 9.0 and 76.5 <= lon <= 77.5


def _haversine_m(la1, lo1, la2, lo2) -> float:
    R = 6_371_000.0
    dlat = math.radians(la2 - la1)
    dlon = math.radians(lo2 - lo1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


async def snap_to_road(lat: float, lon: float, bearing: Optional[str] = None) -> tuple[float, float]:
    if not _is_within_extract(lat, lon):
        return lat, lon

    bearing_param = f"&bearings={bearing},45" if bearing else ""

    for radius in _SNAP_RADII_M:
        url = (
            f"{OSRM_BASE_URL}/nearest/v1/driving/{lon:.6f},{lat:.6f}"
            f"?number=1&radiuses={radius}{bearing_param}"
        )
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "Ok" and data.get("waypoints"):
                        pt = data["waypoints"][0]["location"]
                        return float(pt[1]), float(pt[0])
        except Exception as e:
            logger.debug("[OSRM-SNAP] Error r=%dm: %s", radius, e)
            break

    return lat, lon
