# libs/duk_common/geocoding.py
"""
Reverse geocoding: converts GPS coordinates → human-readable location name.

Strategy (fast + no API rate limits):
  1. Check Redis cache first (instant, ~0ms)
     - Cache key: "geocode:{lat_rounded}:{lon_rounded}" at ~111m grid resolution
     - TTL: 24 hours (Nominatim names don't change often)

  2. If cache miss → spawn background Nominatim fetch (async, doesn't block the GPS pipeline)
     - Nominatim is OpenStreetMap's free geocoding API
     - A lock prevents 10 simultaneous services all calling Nominatim for the same point
     - Result is stored in Redis so future requests hit the cache

  3. While Nominatim is loading → instantly fall back to local OSRM
     - OSRM runs locally on the server, so it's always available (~1ms response)
     - Returns the road name (e.g., "NH66") instead of the area name (e.g., "Pongumoodu")
     - Less descriptive than Nominatim, but always instant

After the first Nominatim response is cached, all future lookups for the same
area are served from Redis (instant). Nominatim is only called once per grid cell.
"""
import logging
import asyncio
import atexit
import httpx
from redis.asyncio import Redis
from typing import Optional
from libs.duk_common.osrm_client import get_osrm_street_name

logger = logging.getLogger(__name__)

# Shared HTTP client for Nominatim — reuses connections instead of
# creating a new TCP connection for every geocoding request.
_NOMINATIM_CLIENT: Optional[httpx.AsyncClient] = None


def _get_nominatim_client() -> httpx.AsyncClient:
    global _NOMINATIM_CLIENT
    if _NOMINATIM_CLIENT is None or _NOMINATIM_CLIENT.is_closed:
        _NOMINATIM_CLIENT = httpx.AsyncClient(
            base_url="https://nominatim.openstreetmap.org",
            headers={"User-Agent": "DUK-Bus-Tracker/1.0 (internal-service)"},
            timeout=httpx.Timeout(connect=3.0, read=5.0, write=2.0, pool=2.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _NOMINATIM_CLIENT


def _close_nominatim_client():
    """Best-effort close on process exit — avoids unclosed-socket warnings."""
    import asyncio as _asyncio
    client = _NOMINATIM_CLIENT
    if client and not client.is_closed:
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(client.aclose())
            else:
                loop.run_until_complete(client.aclose())
        except Exception:
            pass


atexit.register(_close_nominatim_client)


async def fetch_nominatim_background(lat: float, lon: float, redis: Redis, cache_key: str) -> None:
    """
    Fetches a location name from OpenStreetMap Nominatim API and caches the result.

    Input:
        lat       - Latitude (e.g., 8.5581)
        lon       - Longitude (e.g., 76.9061)
        redis     - Active Redis connection for writing the cache
        cache_key - The Redis key to store the result under

    Output: nothing (stores result in Redis as a side effect)

    This runs as a background asyncio task so it never blocks the GPS data pipeline.
    The best available address field is selected in priority order:
      neighbourhood → suburb → village → road → town → city_district → display_name
    """
    try:
        client = _get_nominatim_client()
        resp = await client.get(
            "/reverse",
            params={"format": "json", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1},
        )
        if resp.status_code == 200:
            data = resp.json()
            address = data.get("address", {})

            # Pick the most human-readable name, in order of preference
            name = (
                address.get("neighbourhood") or
                address.get("suburb") or
                address.get("village") or
                address.get("road") or
                address.get("town") or
                address.get("city_district") or
                data.get("display_name", "").split(",")[0]
            )

            if name:
                # Cache for 24 hours — location names rarely change
                await redis.setex(cache_key, 86400, name)
                logger.debug(f"[GEOCODING] Nominatim result cached: {name}")
    except Exception as e:
        logger.warning(f"[GEOCODING] Nominatim request failed: {e}")


async def get_location_name(
    lat: float,
    lon: float,
    redis: Redis,
    wait_timeout: float = 0.0,
) -> Optional[str]:
    """
    Returns a human-readable location name for the given GPS coordinates.

    Input:
        lat          - Latitude (e.g., 8.5581)
        lon          - Longitude (e.g., 76.9061)
        redis        - Active Redis connection
        wait_timeout - Seconds to wait for a fresh Nominatim result before falling back.
                       0.0 (default) = return immediately with OSRM fallback if cache is empty.
                       1.5 = wait up to 1.5 seconds for Nominatim (used on first API call to /latest)

    Output:
        str  - Location name (e.g., "Pongumoodu", "NH66", "Kazhakuttam")
        None - If no name can be determined from any source

    How it works:
        Step 1: Check Redis cache → if found, return instantly
        Step 2: Spawn background Nominatim fetch (only if no other service already fetched it)
        Step 3: If wait_timeout > 0, wait briefly for Nominatim to finish
        Step 4: Fall back to local OSRM for an instant road name
    """
    # Round coordinates to 3 decimal places (~111m grid) for cache sharing
    cache_key = f"geocode:{round(lat, 3)}:{round(lon, 3)}"

    # Step 1: Check Redis cache
    cached = await redis.get(cache_key)
    if cached:
        return cached

    # Step 2: Use a distributed lock to prevent multiple services from all calling
    # Nominatim simultaneously for the same location (would waste API quota)
    lock_key = f"lock:{cache_key}"
    is_locked = await redis.setnx(lock_key, "1")   # Returns 1 only if key didn't exist

    task = None
    if is_locked:
        await redis.expire(lock_key, 10)   # Auto-release lock after 10 seconds
        task = asyncio.create_task(fetch_nominatim_background(lat, lon, redis, cache_key))

    # Step 3: Optionally wait for Nominatim result (used on fresh /latest API calls)
    if wait_timeout > 0.0:
        if task:
            try:
                # Wait for the Nominatim task, but don't cancel it if we time out
                await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
            except asyncio.TimeoutError:
                pass   # Nominatim was slow — continue with OSRM fallback
        else:
            # Another task is already fetching — poll Redis briefly
            for _ in range(int(wait_timeout * 10)):
                await asyncio.sleep(0.1)
                cached = await redis.get(cache_key)
                if cached:
                    return cached

        # Check one final time after the wait
        cached = await redis.get(cache_key)
        if cached:
            return cached

    # Step 4: Instant fallback — ask local OSRM for the nearest road name
    # This is always available (OSRM runs locally) but gives road names, not area names
    osrm_name = await get_osrm_street_name(lat, lon)
    if osrm_name and osrm_name.strip():
        return osrm_name

    return None
