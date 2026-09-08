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
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from fastapi import Request
import httpx

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)
LOG_FILE_PATH = os.environ.get("AUDIT_LOG_FILE", "/app/logs/admin_audit.jsonl")

# In-memory IP Geo cache to avoid redundant external lookups
_GEO_CACHE: Dict[str, Dict[str, Any]] = {}


def extract_client_ip(request: Request) -> str:
    """Extract real client IP considering reverse proxies (Traefik/Cloudflare)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First IP in the list is the original client IP
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    
    if request.client and request.client.host:
        return request.client.host
    
    return "Unknown IP"


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


async def resolve_ip_location(ip: str) -> Dict[str, Any]:
    """Resolve approximate geolocation from IP with in-memory caching."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost") or ip.startswith(("10.", "192.168.", "172.")):
        return {
            "city": "Campus / Local Network",
            "region": "Kerala",
            "country": "India",
            "is_local": True
        }
    
    if ip in _GEO_CACHE:
        return _GEO_CACHE[ip]

    # Asynchronous fallback GeoIP lookup (ip-api.com) with fast timeout
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,lat,lon,isp")
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
                    _GEO_CACHE[ip] = loc
                    return loc
    except Exception as e:
        logger.debug("[AUDIT] GeoIP lookup skipped for %s: %s", ip, e)

    fallback = {"city": "Unknown", "region": "", "country": "", "is_local": False}
    _GEO_CACHE[ip] = fallback
    return fallback


def write_to_disk_log(entry: Dict[str, Any]):
    """Append audit record to immutable disk JSONL file."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
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
    location = await resolve_ip_location(ip)

    # 1. Write to immutable disk JSONL
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
        "location": location,
        "changes": changes or {},
        "status_code": status_code,
        "success": success,
    }
    write_to_disk_log(disk_payload)

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
                "admin_username": admin_username,
                "action": action,
                "endpoint": endpoint,
                "method": method,
                "ip_address": ip,
                "device_os": ua_parsed["device_os"],
                "browser": ua_parsed["browser"],
                "user_agent": ua_raw,
                "location_info": json.dumps(location),
                "changes": json.dumps(changes or {}),
                "status_code": status_code,
                "success": success,
            })
            await db.commit()
        except Exception as e:
            logger.error("[AUDIT] Failed to save audit log to DB: %s", e)
