"""
libs/duk_common/geocoding.py

Shared geocoding utility for converting GPS coordinates into human-readable locations.

Architecture:
  1. Check Redis cache first (instant, ~0ms)
     - Cache key: "geocode:{lat_rounded}:{lon_rounded}" at ~111m grid resolution
     - TTL: 24 hours (Nominatim names don't change often)

  2. If cache miss → spawn background Nominatim fetch (async, doesn't block the GPS pipeline)
     - Nominatim is OpenStreetMap's free geocoding API
     - A distributed lock prevents 10 simultaneous services all calling Nominatim for the same point
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
import httpx
import math
import uuid
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
            trust_env=False,  # ignore ambient HTTP(S)_PROXY / env — no request redirection
        )
    return _NOMINATIM_CLIENT


async def close_geocoding_client():
    """Explicitly closes the shared Nominatim HTTP client.
    Should be called during graceful application shutdown (e.g., FastAPI lifespan).
    """
    global _NOMINATIM_CLIENT
    client = _NOMINATIM_CLIENT
    if client and not client.is_closed:
        try:
            await client.aclose()
        except Exception as e:
            logger.warning("[GEOCODING] Error closing Nominatim client: %s", e)
        finally:
            _NOMINATIM_CLIENT = None


async def fetch_nominatim_background(
    lat: float, lon: float, redis: Redis, cache_key: str, lock_key: str, lock_token: str
) -> None:
    """
    Fetches a location name from OpenStreetMap Nominatim API and caches the result.
    Uses an outer try/finally to safely release the unique distributed lock upon completion.
    """
    try:
        # Enforce Nominatim 1 req/s global rate limit to comply with TOS
        acquired = await redis.set("nominatim:rate_limit", "1", nx=True, px=1000)
        if not acquired:
            logger.debug("[GEOCODING] Nominatim rate limit exceeded (1 req/s), skipping.")
            return

        client = _get_nominatim_client()
        resp = await client.get(
            "/reverse",
            params={"format": "json", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1},
        )
        if resp.status_code == 200:
            data = resp.json()
            address = data.get("address", {})

            # Pick the most human-readable name, in order of preference
            display_name = data.get("display_name", "")

            # 1. Custom Overrides for Important Campuses
            if "Digital University" in display_name or "DUK" in display_name:
                name = "Digital University Kerala"
            elif "CPTC" in display_name or "Central Polytechnic" in display_name:
                name = "CPTC College"
            else:
                # 2. General Grid Location Name (Removed roads and residential)
                name = (
                    address.get("neighbourhood") or
                    address.get("suburb") or
                    address.get("village") or
                    address.get("town") or
                    address.get("city_district") or
                    display_name.split(",")[0]
                )

            if name:
                # Cache for 24 hours — location names rarely change
                await redis.setex(cache_key, 86400, name)
                logger.debug("[GEOCODING] Nominatim result cached: %s", name)
        else:
            # Do not log the response body: Nominatim error bodies can contain
            # address/location detail tied to a live GPS coordinate (PII).
            logger.warning(
                "[GEOCODING] Nominatim returned HTTP %s",
                resp.status_code,
            )
    except Exception as e:
        logger.warning("[GEOCODING] Nominatim request failed: %s", e)
    finally:
        # Release the lock safely using Lua to ensure we only delete our own lock
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await redis.eval(lua_script, 1, lock_key, lock_token)
        except Exception as e:
            logger.warning("[GEOCODING] Failed to release Redis lock: %s", e)


async def get_location_name(
    lat: float,
    lon: float,
    redis: Redis,
    wait_timeout: float = 0.0,
) -> Optional[str]:
    """
    Returns a human-readable location name for the given GPS coordinates.
    """
    if wait_timeout < 0:
        raise ValueError("wait_timeout must be >= 0")
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("Invalid GPS coordinates")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("Invalid GPS coordinates")

    # Round coordinates to 3 decimal places (~111m grid) for cache sharing
    cache_key = f"geocode:{round(lat, 3)}:{round(lon, 3)}"

    # Step 1: Check Redis cache
    cached = await redis.get(cache_key)
    if cached:
        # Ensure we decode bytes if Redis client doesn't use decode_responses
        return cached.decode("utf-8") if isinstance(cached, bytes) else cached

    # Step 2: Use a distributed lock to prevent multiple services from all calling
    # Nominatim simultaneously for the same location (would waste API quota)
    lock_key = f"lock:{cache_key}"
    lock_token = uuid.uuid4().hex
    
    # Atomic SET NX EX
    is_locked = await redis.set(lock_key, lock_token, nx=True, ex=10)

    task = None
    if is_locked:
        task = asyncio.create_task(
            fetch_nominatim_background(lat, lon, redis, cache_key, lock_key, lock_token)
        )

    # Step 3: Optionally wait for Nominatim result (used on fresh /latest API calls)
    if wait_timeout > 0.0:
        if task:
            try:
                # Wait for the Nominatim task, but don't cancel it if we time out
                await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
            except asyncio.TimeoutError:
                pass   # Nominatim was slow — continue with OSRM fallback
        else:
            # Another task is already fetching — poll Redis precisely
            loop = asyncio.get_running_loop()
            deadline = loop.time() + wait_timeout
            while loop.time() < deadline:
                await asyncio.sleep(0.1)
                cached = await redis.get(cache_key)
                if cached:
                    return cached.decode("utf-8") if isinstance(cached, bytes) else cached

        # Check one final time after the wait
        cached = await redis.get(cache_key)
        if cached:
            return cached.decode("utf-8") if isinstance(cached, bytes) else cached

    # Step 4: Instant fallback — ask local OSRM for the nearest road name
    # This is always available (OSRM runs locally) but gives road names, not area names
    osrm_name = await get_osrm_street_name(lat, lon)
    if osrm_name and osrm_name.strip():
        return osrm_name

    return None
