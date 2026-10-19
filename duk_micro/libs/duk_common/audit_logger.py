# libs/duk_common/audit_logger.py
"""
Permanent Audit & Security Logging System for DUK Bus Tracker.
Records all admin activities, logins, logouts, and data mutations.
Logs to both PostgreSQL (immutable via DB triggers) and append-only disk JSONL file.
"""
import os
import re
import json
import logging
import asyncio
import ipaddress
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from collections import OrderedDict
from fastapi import Request
import httpx
import aiofiles

logger = logging.getLogger(__name__)

from libs.duk_common.constants import IST_OFFSET
LOG_FILE_PATH = os.environ.get("AUDIT_LOG_FILE", "/app/logs/admin_audit.jsonl")

# In-memory IP Geo cache to avoid redundant external lookups
class LRUCache:
    """Thread-safe bounded LRU cache."""
    def __init__(self, maxsize: int = 500):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = asyncio.Lock()
    
    async def get(self, key: str):
        #    Retrieves an item while maintaining LRU ordering.
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None
    
    async def set(self, key: str, value):
        # Adds/updates a cache item and removes the least recently used item when capacity is exceeded.
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

_GEO_CACHE = LRUCache(maxsize=500)

_GEO_CLIENT: httpx.AsyncClient | None = None

def _get_geo_client() -> httpx.AsyncClient:
    global _GEO_CLIENT
    if _GEO_CLIENT is None or _GEO_CLIENT.is_closed:
        _GEO_CLIENT = httpx.AsyncClient(
            base_url="https://ip-api.com",
            timeout=2.0,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _GEO_CLIENT

# Trusted proxies (IPs or CIDR ranges) come from the same TRUSTED_PROXY_IPS
# parser as the rate limiter, so both always agree on who may set X-Forwarded-For.
from libs.duk_common.rate_limit_deps import is_trusted_proxy


def _parse_ip(raw: str) -> Optional[str]:
    """Normalize an IP string (strips an IPv6 zone/`[..]`), or None if invalid."""
    if not raw:
        return None
    raw = raw.strip().strip("[]")
    if "%" in raw:  # drop IPv6 zone id (e.g. fe80::1%eth0)
        raw = raw.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def extract_client_ip(request: Request) -> str:
    """Extract the real client IP, honouring X-Forwarded-For ONLY when the
    direct TCP peer is a known trusted proxy. X-Forwarded-For is parsed
    right-to-left, skipping trusted-proxy hops, so a spoofed left-most entry
    cannot forge the attributed IP (same logic as rate_limit_deps)."""
    direct_peer = request.client.host if request.client else None
    normalized_peer = _parse_ip(direct_peer) if direct_peer else None
    if normalized_peer is None:
        return direct_peer or "unknown"

    if not is_trusted_proxy(ipaddress.ip_address(normalized_peer)):
        return normalized_peer

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        for raw_hop in reversed(hops):
            hop_ip = _parse_ip(raw_hop)
            if hop_ip is None:
                break  # malformed hop: stop trusting anything further left
            if is_trusted_proxy(ipaddress.ip_address(hop_ip)):
                continue
            return hop_ip

    real_ip = request.headers.get("x-real-ip")
    real_norm = _parse_ip(real_ip) if real_ip else None
    if real_norm:
        return real_norm

    return normalized_peer


def parse_user_agent(ua_string: Optional[str]) -> Dict[str, str]:
    """Parse User-Agent string to human-readable Device/OS and Browser."""
    if not ua_string:
        return {"device_os": "Unknown Device", "browser": "Unknown Browser"}
    
    ua = ua_string
    
    # Detect OS / Platform
    if "iPhone" in ua:
        device_os = "Apple iPhone (iOS)"
    elif "iPad" in ua:
        device_os = "Apple iPad (iPadOS)"
    elif "Android" in ua:
        match = re.search(r"Android\s+([\d\.]+)", ua)
        device_os = f"Android {match.group(1)}" if match else "Android"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        match = re.search(r"Mac OS X\s+([\d_]+)", ua)
        ver = match.group(1).replace("_", ".") if match else ""
        device_os = f"macOS {ver}".strip()
    elif "Windows NT 10.0" in ua:
        device_os = "Windows 10/11"
    elif "Windows" in ua:
        device_os = "Windows"
    elif "Linux" in ua:
        device_os = "Linux"
    else:
        device_os = "Other OS"

    # Detect Browser
    if "Edg/" in ua:
        match = re.search(r"Edg/([\d\.]+)", ua)
        browser = f"Microsoft Edge {match.group(1).split('.')[0]}" if match else "Edge"
    elif "Chrome/" in ua and "Chromium/" not in ua:
        match = re.search(r"Chrome/([\d\.]+)", ua)
        browser = f"Google Chrome {match.group(1).split('.')[0]}" if match else "Chrome"
    elif "Safari/" in ua and "Chrome" not in ua:
        match = re.search(r"Version/([\d\.]+)", ua)
        browser = f"Apple Safari {match.group(1).split('.')[0]}" if match else "Safari"
    elif "Firefox/" in ua:
        match = re.search(r"Firefox/([\d\.]+)", ua)
        browser = f"Mozilla Firefox {match.group(1).split('.')[0]}" if match else "Firefox"
    elif "curl" in ua.lower():
        browser = "cURL / Terminal CLI"
    elif "postman" in ua.lower():
        browser = "Postman API Client"
    else:
        browser = "Other / Unknown"

    return {"device_os": device_os, "browser": browser}


_CIRCUIT_OPEN_UNTIL = 0.0

async def resolve_ip_location(ip: str) -> Dict[str, Any]:
    """Resolve approximate geolocation from IP with in-memory caching and circuit breaker."""
    global _CIRCUIT_OPEN_UNTIL

    # Validate and classify the IP with the stdlib rather than string prefixes
    # (the old `startswith("172.")` check wrongly flagged all of 172.0.0.0/8 as
    # private, and an unvalidated value was interpolated into the ip-api URL).
    norm = _parse_ip(ip)
    if norm is None:
        return {"city": "Unknown", "region": "", "country": "", "is_local": False}

    ip_obj = ipaddress.ip_address(norm)
    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
        return {
            "city": "Campus / Local Network",
            "region": "Kerala",
            "country": "India",
            "is_local": True
        }

    cached = await _GEO_CACHE.get(norm)
    if cached:
        return cached

    now = asyncio.get_running_loop().time()
    if now < _CIRCUIT_OPEN_UNTIL:
        fallback = {"city": "Unknown (Circuit Open)", "region": "", "country": "", "is_local": False}
        await _GEO_CACHE.set(norm, fallback)
        return fallback

    # Asynchronous fallback GeoIP lookup (ip-api.com) with fast timeout
    try:
        resp = await _get_geo_client().get(
            f"/json/{norm}",
            params={"fields": "status,country,regionName,city,lat,lon,isp"},
        )
        if resp.status_code == 429:
            logger.warning("[AUDIT] ip-api.com rate limit exceeded (429). Opening circuit breaker for 60s.")
            _CIRCUIT_OPEN_UNTIL = now + 60.0
            raise Exception("Rate limited")

        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                loc = {
                    "city": data.get("city", "Unknown City"),
                    "region": data.get("regionName", ""),
                    "country": data.get("country", ""),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                    "isp": data.get("isp", ""),
                    "is_local": False
                }
                await _GEO_CACHE.set(norm, loc)
                return loc
    except Exception as e:
        if "Rate limited" not in str(e):
            logger.debug("[AUDIT] GeoIP lookup skipped for %s: %s", norm, e)
        if isinstance(e, (httpx.TimeoutException, httpx.ConnectError)):
            logger.warning("[AUDIT] ip-api.com timeout/connect error. Opening circuit breaker for 10s.")
            _CIRCUIT_OPEN_UNTIL = now + 10.0

    fallback = {"city": "Unknown", "region": "", "country": "", "is_local": False}
    await _GEO_CACHE.set(norm, fallback)
    return fallback


async def write_to_disk_log_async(entry: Dict[str, Any]) -> None:
    """Async append audit record to immutable disk JSONL file."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
        async with aiofiles.open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            await f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("[AUDIT] Failed to append to audit log file: %s", e)


async def record_audit_event(
    db: Any,
    request: Optional[Request],
    action: str,
    admin_username: str = "admin",
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    changes: Optional[Dict[str, Any]] = None,
    status_code: int = 200,
    success: bool = True,
    ip_override: Optional[str] = None,
    user_agent_override: Optional[str] = None,
):
    """
    Core function to record an immutable audit log entry into PostgreSQL and the disk JSONL file.
    """
    now_utc = datetime.now(timezone.utc)
    now_ist = (now_utc + IST_OFFSET).replace(tzinfo=None)
    
    # Extract client metadata
    if request:
        ip = extract_client_ip(request)
        ua_raw = request.headers.get("user-agent", "")
        method = method or request.method
        endpoint = endpoint or request.url.path
    else:
        ip = ip_override or "127.0.0.1"
        ua_raw = user_agent_override or ""
        method = method or "INTERNAL"
        endpoint = endpoint or "/internal"

    ua_parsed = parse_user_agent(ua_raw)

    # Write audit record immediately (without blocking on GeoIP)
    disk_payload = {
        "timestamp_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_utc": now_utc.isoformat(),
        "admin_username": admin_username,
        "action": action,
        "endpoint": endpoint,
        "method": method,
        "ip_address": ip,
        "device_os": ua_parsed["device_os"],
        "browser": ua_parsed["browser"],
        "user_agent": ua_raw,
        "location": None,  # filled in async below
        "changes": changes or {},
        "status_code": status_code,
        "success": success,
    }
    await write_to_disk_log_async(disk_payload)

    # Resolve location and write to DB synchronously so we don't share the DB session with a background task
    await _write_db_audit(db, disk_payload, ip, now_utc, now_ist, ua_parsed, changes, status_code, success)

async def _write_db_audit(db, disk_payload, ip, now_utc, now_ist, ua_parsed, changes, status_code, success):
    """Writes the audit record to PostgreSQL after resolving IP geolocation (non-blocking)."""
    location = await resolve_ip_location(ip)

    # 2. Write to PostgreSQL table
    if db is not None:
        try:
            from sqlalchemy import text
            query = text("""
                INSERT INTO admin_audit_logs 
                (created_at, ist_time, admin_username, action, endpoint, method, ip_address, device_os, browser, user_agent, location_info, changes, status_code, success)
                VALUES 
                (:created_at, :ist_time, :admin_username, :action, :endpoint, :method, :ip_address, :device_os, :browser, :user_agent, :location_info, :changes, :status_code, :success)
            """)
            await db.execute(query, {
                "created_at": now_utc,
                "ist_time": now_ist,
                "admin_username": disk_payload["admin_username"],
                "action": disk_payload["action"],
                "endpoint": disk_payload["endpoint"],
                "method": disk_payload["method"],
                "ip_address": ip,
                "device_os": ua_parsed["device_os"],
                "browser": ua_parsed["browser"],
                "user_agent": disk_payload["user_agent"],
                "location_info": json.dumps(location),
                "changes": json.dumps(changes or {}),
                "status_code": status_code,
                "success": success,
            })
            await db.commit()
        except Exception as e:
            logger.error("[AUDIT] Failed to save audit log to DB: %s", e)
