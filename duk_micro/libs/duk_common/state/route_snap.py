# libs/duk_common/state/route_snap.py
"""
Route-constrained snapping (linear referencing) for a fixed bus route.

Industry approach used by transit AVL / GTFS-RT producers: instead of snapping
every GPS fix independently to the nearest road (which jumps between parallel
carriageways and the out/return arms of a loop), we project the fix onto the
*known* route polyline and track a single 1-D progress value ``s`` = metres
travelled along the route. ``s`` is only allowed to move forward within a search
window, which makes backtracking/looping impossible.

OSRM is NOT needed while the bus is on its route — the snapped point is computed
analytically ON the polyline, so it is always exactly on the road. OSRM is only
consulted (by the caller) when the bus is declared off-route after sustained
deviation beyond the corridor.

Pure functions only — no Redis, no module globals — mirroring the project's
other ``apply_*_stateless`` helpers so they stay unit-testable.
"""
import math
from typing import Optional, Tuple, List, Dict, Any

_R = 6_371_000.0  # Earth radius, metres


def _haversine_m(la1: float, lo1: float, la2: float, lo2: float) -> float:
    dlat = math.radians(la2 - la1)
    dlon = math.radians(lo2 - lo1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlon / 2) ** 2
    )
    return _R * 2 * math.asin(math.sqrt(min(1.0, a)))


def build_route_index(coords: List) -> Dict[str, Any]:
    """
    Precompute a linear-reference index for a planned route.

    Args:
        coords: planned-route polyline as ``[[lon, lat], ...]`` (the format stored
                in Redis ``cache:route_geometry:{direction}``).

    Returns:
        dict with:
          - ``pts``:  ``[(lat, lon), ...]`` vertices in travel order
          - ``cum``:  ``[0.0, d1, d1+d2, ...]`` cumulative metres at each vertex
          - ``total``: total route length in metres
        Returns an empty index (``pts=[]``) when fewer than 2 valid vertices.
    """
    pts: List[Tuple[float, float]] = []
    for p in coords or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            lon, lat = float(p[0]), float(p[1])
            if math.isfinite(lat) and math.isfinite(lon):
                pts.append((lat, lon))
    if len(pts) < 2:
        return {"pts": [], "cum": [], "total": 0.0}

    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + _haversine_m(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]))
    return {"pts": pts, "cum": cum, "total": cum[-1]}


def _project_to_segment(
    lat: float, lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float,
) -> Tuple[float, float, float, float]:
    """
    Perpendicular foot-point of (lat, lon) on segment a→b.

    Uses a local equirectangular projection (metres) centred on ``a`` — accurate
    and cheap at city scale. Returns ``(foot_lat, foot_lon, t, perp_m)`` where
    ``t`` in [0, 1] is the fractional position along the segment and ``perp_m`` is
    the perpendicular distance in metres.
    """
    coslat = math.cos(math.radians(a_lat))
    # Local metric plane: x = east, y = north, metres from `a`.
    mx = math.radians(1.0) * _R
    ax, ay = 0.0, 0.0
    bx = (b_lon - a_lon) * mx * coslat
    by = (b_lat - a_lat) * mx
    px = (lon - a_lon) * mx * coslat
    py = (lat - a_lat) * mx

    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 <= 1e-12:
        return a_lat, a_lon, 0.0, _haversine_m(lat, lon, a_lat, a_lon)

    t = (px * dx + py * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    foot_lat = a_lat + t * (b_lat - a_lat)
    foot_lon = a_lon + t * (b_lon - a_lon)
    perp_m = _haversine_m(lat, lon, foot_lat, foot_lon)
    return foot_lat, foot_lon, t, perp_m


def snap_to_route(
    lat: float, lon: float,
    index: Dict[str, Any],
    s_last: Optional[float],
    max_back_m: float = 40.0,
    max_fwd_m: float = 250.0,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Project a GPS fix onto the route, constrained to move forward in ``s``.

    Args:
        lat, lon:   filtered GPS fix.
        index:      output of ``build_route_index``.
        s_last:     last accepted progress in metres, or ``None`` to (re)acquire
                    by searching the whole route.
        max_back_m: how far behind ``s_last`` a fix may snap (small, absorbs jitter).
        max_fwd_m:  how far ahead ``s_last`` a fix may snap (one headway of travel).

    Returns:
        ``(snap_lat, snap_lon, s_new, perp_m)`` for the best in-window segment,
        or ``None`` if the index is empty.
    """
    pts = index.get("pts") or []
    cum = index.get("cum") or []
    if len(pts) < 2:
        return None

    lo_s = None if s_last is None else s_last - max_back_m
    hi_s = None if s_last is None else s_last + max_fwd_m

    best = None  # (perp_m, snap_lat, snap_lon, s_new)
    for i in range(len(pts) - 1):
        seg_start_s, seg_end_s = cum[i], cum[i + 1]
        # Skip segments entirely outside the forward window.
        if hi_s is not None and seg_start_s > hi_s:
            break
        if lo_s is not None and seg_end_s < lo_s:
            continue
        a_lat, a_lon = pts[i]
        b_lat, b_lon = pts[i + 1]
        f_lat, f_lon, t, perp_m = _project_to_segment(lat, lon, a_lat, a_lon, b_lat, b_lon)
        s_new = seg_start_s + t * (seg_end_s - seg_start_s)
        if lo_s is not None and s_new < lo_s:
            continue
        if hi_s is not None and s_new > hi_s:
            continue
        if best is None or perp_m < best[0]:
            best = (perp_m, f_lat, f_lon, s_new)

    if best is None:
        # Window excluded every segment (e.g. a long GPS gap). Re-acquire globally.
        if s_last is not None:
            return snap_to_route(lat, lon, index, None, max_back_m, max_fwd_m)
        return None

    perp_m, f_lat, f_lon, s_new = best
    return f_lat, f_lon, s_new, perp_m


def apply_route_snap_stateless(
    f_lat: float, f_lon: float,
    speed_kmh: Optional[float],
    state: dict,
    index: Dict[str, Any],
    corridor_m: float = 60.0,
    off_route_hits: int = 3,
    max_back_m: float = 40.0,
    max_fwd_m: float = 250.0,
    s_ema_alpha: float = 0.6,
) -> Tuple[float, float, bool, dict]:
    """
    Forward-only route snap with off-route hysteresis. Pure function.

    Args:
        f_lat, f_lon: GPS fix already passed through the EMA/outlier GPS filter.
        speed_kmh:    reported speed (currently informational; reserved for the
                      1-D Kalman upgrade).
        state:        prior state dict (``{}`` on first fix).
        index:        output of ``build_route_index``.
        corridor_m:   max perpendicular distance to still be considered on-route.
        off_route_hits: consecutive out-of-corridor fixes needed to flip off-route
                      (hysteresis — one stray fix never flips it).
        s_ema_alpha:  smoothing factor on progress ``s`` (higher = more responsive).

    Returns:
        ``(snap_lat, snap_lon, off_route, new_state)``.
        When ``off_route`` is True the caller should fall back to OSRM; the
        returned coords are then the raw filtered fix (unsnapped).
    """
    new_state = dict(state)
    s_last = state.get("s")
    hits = state.get("off_route_hits", 0)

    if not index.get("pts"):
        # No route loaded — behave as "off-route" so the caller uses OSRM.
        new_state["off_route_hits"] = 0
        return f_lat, f_lon, True, new_state

    result = snap_to_route(f_lat, f_lon, index, s_last, max_back_m, max_fwd_m)
    if result is None:
        new_state["off_route_hits"] = 0
        return f_lat, f_lon, True, new_state

    snap_lat, snap_lon, s_new, perp_m = result

    if perp_m > corridor_m and s_last is not None:
        # The forward window found nothing close, but the fix may still be ON the
        # route further along (e.g. after a GPS/network gap longer than max_fwd_m).
        # Without this the window keeps returning far-away segments, s never
        # advances, and the device stays off-route for the rest of the trip.
        # Re-acquire globally, but only once two consecutive fixes agree, so one
        # wild fix can't teleport s.
        g = snap_to_route(f_lat, f_lon, index, None, max_back_m, max_fwd_m)
        if g is not None and g[3] <= corridor_m:
            cand = state.get("reacq_s")
            if cand is not None and cand - max_back_m <= g[2] <= cand + max_fwd_m:
                new_state.pop("reacq_s", None)
                new_state["s"] = g[2]
                new_state["off_route_hits"] = 0
                pt = _point_at_s(index, g[2])
                out_lat, out_lon = pt if pt is not None else (g[0], g[1])
                return out_lat, out_lon, False, new_state
            new_state["reacq_s"] = g[2]
        else:
            new_state.pop("reacq_s", None)

    if perp_m > corridor_m:
        hits += 1
        new_state["off_route_hits"] = hits
        if hits >= off_route_hits:
            # Sustained deviation — declare off-route, let caller use OSRM.
            # Do NOT advance s; we re-acquire when the bus returns to the corridor.
            return f_lat, f_lon, True, new_state
        # Not yet confirmed off-route: hold on the route at last-known s.
        if s_last is not None:
            held = _point_at_s(index, s_last)
            if held is not None:
                return held[0], held[1], False, new_state
        return snap_lat, snap_lon, False, new_state

    # On-route: reset hysteresis, smooth and advance s (never backward).
    hits = 0
    if s_last is None:
        s_smoothed = s_new
    else:
        s_smoothed = s_ema_alpha * s_new + (1 - s_ema_alpha) * s_last
        s_smoothed = max(s_smoothed, s_last)  # monotonic non-decreasing on-route
    pt = _point_at_s(index, s_smoothed)
    out_lat, out_lon = (pt if pt is not None else (snap_lat, snap_lon))

    new_state["s"] = s_smoothed
    new_state["off_route_hits"] = 0
    new_state.pop("reacq_s", None)
    return out_lat, out_lon, False, new_state


def _point_at_s(index: Dict[str, Any], s: float) -> Optional[Tuple[float, float]]:
    """Interpolate the (lat, lon) point at distance ``s`` metres along the route."""
    pts = index.get("pts") or []
    cum = index.get("cum") or []
    if len(pts) < 2:
        return None
    s = max(0.0, min(s, index.get("total", cum[-1])))
    # Binary search for the segment containing s.
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] < s:
            lo = mid + 1
        else:
            hi = mid
    i = max(1, lo)
    seg_s0, seg_s1 = cum[i - 1], cum[i]
    seg_len = seg_s1 - seg_s0
    t = 0.0 if seg_len <= 1e-9 else (s - seg_s0) / seg_len
    a_lat, a_lon = pts[i - 1]
    b_lat, b_lon = pts[i]
    return a_lat + t * (b_lat - a_lat), a_lon + t * (b_lon - a_lon)


def route_slice(index: Dict[str, Any], s_from: float, s_to: float) -> List[List[float]]:
    """
    Return the polyline between two progress values as ``[[lon, lat], ...]`` in
    travel order. Monotonic in ``s`` so it can never produce the giant wrong
    slices that nearest-vertex slicing did. Endpoints are interpolated exactly.
    """
    pts = index.get("pts") or []
    cum = index.get("cum") or []
    if len(pts) < 2:
        return []
    if s_to < s_from:
        s_from, s_to = s_to, s_from
    p0 = _point_at_s(index, s_from)
    p1 = _point_at_s(index, s_to)
    if p0 is None or p1 is None:
        return []
    out: List[List[float]] = [[p0[1], p0[0]]]
    for i in range(len(pts)):
        if s_from < cum[i] < s_to:
            out.append([pts[i][1], pts[i][0]])
    out.append([p1[1], p1[0]])
    return out
