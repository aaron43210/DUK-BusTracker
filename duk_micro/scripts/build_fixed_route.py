#!/usr/bin/env python3
"""
build_fixed_route.py — DUK Bus Tracker

Builds the *fixed* morning (forward) and evening (reverse) route polylines that
Phase 2 of the audit calls for, from real recorded GPS traces instead of letting
OSRM invent a car route between the stops.

Two modes:

  extract  (runs anywhere, stdlib only)
      Pick the cleanest real trip per direction out of a GPS-log store, de-noise
      it, and write a reviewable GeoJSON LineString per direction:
          deploy/fixed_routes/forward.geojson
          deploy/fixed_routes/reverse.geojson
      These double as the static asset the public website draws (audit W1).

  pin      (run on the server, needs OSRM + DB/Redis)
      Take those reviewed GeoJSON lines, map-match them onto the bus OSRM graph
      (/match, which snaps to the real road shape), and store the result as the
      PINNED route geometry in Postgres + Redis so the auto-recompute can never
      overwrite it. See store_pinned_route() — wired to duk_common lazily.

Usage:
    python3 scripts/build_fixed_route.py extract --sqlite gps_tracker.db
    python3 scripts/build_fixed_route.py pin --osrm http://127.0.0.1:5001

The extract step is deliberately dependency-free so it can run on a laptop from
the dev SQLite; only `pin` imports the backend.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sqlite3
from collections import defaultdict

# ── Route endpoints (lat, lon) — from src/constants.js STOPS ────────────────────
FORWARD_START = (8.5350, 76.9908)   # Central Polytechnic
FORWARD_END   = (8.6158, 76.8527)   # Digital University Kerala
# Evening is the same corridor travelled the other way.
REVERSE_START = FORWARD_END
REVERSE_END   = FORWARD_START

# Trip time windows in IST (server_time is already IST in the logs).
WINDOWS = {
    "forward": range(6, 11),    # morning 06:00–10:59
    "reverse": range(17, 21),   # evening 17:00–20:59
}
ENDPOINTS = {
    "forward": (FORWARD_START, FORWARD_END),
    "reverse": (REVERSE_START, REVERSE_END),
}

# De-noise tuning.
MIN_SEP_M      = 8.0      # drop a point closer than this to the previous kept one
MAX_JUMP_M     = 400.0    # drop a single point that jumps further than this (GPS spike)
ENDPOINT_TOL_M = 300.0    # a usable trip must pass within this of both endpoints
MIN_POINTS     = 60       # and have at least this many raw points

# PLACEHOLDER_APPEND
def _haversine_m(a, b):
    R = 6371000.0
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1])
    x = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(x))


def _load_sqlite_points(path):
    """Return [(server_time, lat, lon), ...] ordered by time from a gps_logs store."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT server_time, lat, lon FROM gps_logs "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL ORDER BY server_time"
    ).fetchall()
    con.close()
    return [(r["server_time"], float(r["lat"]), float(r["lon"])) for r in rows]


def _pick_best_trip(points, direction):
    """Group points by date within the direction's time window; return the single
    cleanest day's trip (most points that also touches both endpoints)."""
    hours = WINDOWS[direction]
    (start, end) = ENDPOINTS[direction]
    by_day = defaultdict(list)
    for server_time, lat, lon in points:
        date, clock = server_time.split(" ")
        if int(clock[:2]) in hours:
            by_day[date].append((lat, lon))

    best_day, best_pts, best_score = None, None, -1
    for date, pts in by_day.items():
        if len(pts) < MIN_POINTS:
            continue
        near_start = min(_haversine_m(p, start) for p in pts)
        near_end = min(_haversine_m(p, end) for p in pts)
        if near_start > ENDPOINT_TOL_M or near_end > ENDPOINT_TOL_M:
            continue
        if len(pts) > best_score:
            best_day, best_pts, best_score = date, pts, len(pts)

    if not best_pts:
        return None, None
    # Trim to the single directional leg: from the closest approach to the start
    # up to the closest approach to the end. The time window can also contain the
    # bus repositioning or the return leg, which would otherwise inflate the line.
    i_start = min(range(len(best_pts)), key=lambda i: _haversine_m(best_pts[i], start))
    i_end = min(range(len(best_pts)), key=lambda i: _haversine_m(best_pts[i], end))
    if i_start < i_end:
        best_pts = best_pts[i_start:i_end + 1]
    return best_day, best_pts


def _denoise(pts):
    """Drop near-duplicate (dwell/jitter) points and isolated GPS spikes, order kept.

    A spike is a single point that darts far away and comes straight back
    (prev→p and p→next both large while prev→next is small). We must NOT drop a
    point just because it is far from the previous one — legitimate gaps between
    sparse pings are normal and dropping them would truncate the whole trip.
    """
    # 1) de-duplicate stationary jitter / stop dwell
    dedup = []
    for p in pts:
        if not dedup or _haversine_m(dedup[-1], p) >= MIN_SEP_M:
            dedup.append(p)
    # 2) remove isolated out-and-back spikes using a 3-point window
    cleaned = []
    for i, p in enumerate(dedup):
        if 0 < i < len(dedup) - 1:
            prev, nxt = dedup[i - 1], dedup[i + 1]
            if (_haversine_m(prev, p) > MAX_JUMP_M
                    and _haversine_m(p, nxt) > MAX_JUMP_M
                    and _haversine_m(prev, nxt) < MAX_JUMP_M):
                continue  # lone spike — skip, prev→next stays continuous
        cleaned.append(p)
    return cleaned


def _geojson_line(coords_lonlat, direction, source_day, n_raw):
    return {
        "type": "Feature",
        "properties": {
            "direction": direction,
            "pinned": True,
            "source": "real-gps-trace",
            "source_day": source_day,
            "raw_points": n_raw,
            "kept_points": len(coords_lonlat),
            "note": "First-cut from a real trip. REVIEW on a map, then run `pin` to "
                    "map-match onto the bus OSRM graph and store as the fixed route.",
        },
        "geometry": {"type": "LineString", "coordinates": coords_lonlat},
    }


def cmd_extract(args):
    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "fixed_routes"
    )
    os.makedirs(out_dir, exist_ok=True)
    points = _load_sqlite_points(args.sqlite)
    print(f"Loaded {len(points)} GPS points from {args.sqlite}")

    for direction in ("forward", "reverse"):
        day, pts = _pick_best_trip(points, direction)
        if not pts:
            print(f"  [{direction}] no clean trip found — skipped")
            continue
        cleaned = _denoise(pts)
        coords = [[lon, lat] for (lat, lon) in cleaned]   # GeoJSON = [lon, lat]
        feat = _geojson_line(coords, direction, day, len(pts))
        path = os.path.join(out_dir, f"{direction}.geojson")
        with open(path, "w") as fh:
            json.dump(feat, fh, indent=2)
        print(f"  [{direction}] {day}: {len(pts)} raw → {len(cleaned)} points  →  {path}")

    print("\nNext: open the two .geojson files on a map (geojson.io), fix any kink by "
          "hand, then run `pin` on the server to map-match + store them.")


def _osrm_match(coords_lonlat, osrm_base):
    """Map-match a [[lon,lat],...] line onto the OSRM graph via /match, chunked to
    50 points. Returns matched [[lon,lat],...]; falls back to the input on error.
    Uses urllib so this stays dependency-free."""
    import urllib.request
    out = []
    CHUNK = 50
    for i in range(0, len(coords_lonlat) - 1, CHUNK - 1):
        chunk = coords_lonlat[i:i + CHUNK]
        if len(chunk) < 2:
            continue
        coords_str = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in chunk)
        radiuses = ";".join(["25"] * len(chunk))
        url = (f"{osrm_base}/match/v1/driving/{coords_str}"
               f"?overview=full&geometries=geojson&tidy=true&gaps=ignore&radiuses={radiuses}")
        seg = chunk  # fallback
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.load(resp)
            if data.get("code") == "Ok" and data.get("matchings"):
                seg = []
                for m in data["matchings"]:
                    seg.extend(m.get("geometry", {}).get("coordinates", []))
        except Exception as exc:   # noqa: BLE001 — tool, log and fall back
            print(f"    match chunk @{i} failed ({exc}); keeping raw points")
        if i > 0 and seg:
            seg = seg[1:]
        out.extend(seg)
    return out


def _redis_client():
    """Connect to Redis from REDIS_URL / settings. Imported lazily (server only)."""
    url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_DSN")
    import redis  # redis-py, a backend dependency
    if url:
        return redis.Redis.from_url(url, decode_responses=True)
    return redis.Redis(host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                       port=int(os.environ.get("REDIS_PORT", "6379")),
                       decode_responses=True)


def cmd_pin(args):
    """Map-match the reviewed GeoJSON lines onto the bus OSRM graph and store them
    as the PINNED route geometry in Redis (fast path the backend reads first).

    Run on the server where OSRM + Redis are reachable. The pinned flag makes the
    backend's auto-recompute leave this route alone (audit Phase 2, steps 4–6).
    """
    in_dir = args.dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "fixed_routes"
    )
    r = _redis_client()
    for direction in ("forward", "reverse"):
        path = os.path.join(in_dir, f"{direction}.geojson")
        if not os.path.exists(path):
            print(f"[{direction}] {path} not found — skipped")
            continue
        with open(path) as fh:
            feat = json.load(fh)
        raw = feat["geometry"]["coordinates"]
        matched = _osrm_match(raw, args.osrm.rstrip("/"))
        print(f"[{direction}] {len(raw)} reviewed pts → {len(matched)} map-matched pts")
        # Store in the exact shape the backend expects (MultiLineString, one segment).
        payload = {"coordinates": [matched], "type": "MultiLineString"}
        r.set(f"cache:route_geometry:{direction}", json.dumps(payload))
        r.set(f"cache:route_geometry_pinned:{direction}", "1")
        r.incr(f"cache:route_geometry_version:{direction}")
        print(f"[{direction}] pinned → cache:route_geometry:{direction} "
              f"(flag set, version bumped). gps-ingestion will reload on next ping.")
    print("\nDone. To unpin and let OSRM auto-recompute again: "
          "DEL cache:route_geometry_pinned:{forward,reverse}.")


def main():
    ap = argparse.ArgumentParser(description="Build the fixed morning/evening bus route.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="extract reviewable GeoJSON from real GPS traces")
    ex.add_argument("--sqlite", required=True, help="path to a gps_logs SQLite store")
    ex.add_argument("--out", help="output dir (default: deploy/fixed_routes)")
    ex.set_defaults(func=cmd_extract)
    pn = sub.add_parser("pin", help="(server) map-match + store as the pinned route")
    pn.add_argument("--osrm", default="http://127.0.0.1:5001")
    pn.add_argument("--dir", help="dir with {forward,reverse}.geojson (default: deploy/fixed_routes)")
    pn.set_defaults(func=cmd_pin)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
