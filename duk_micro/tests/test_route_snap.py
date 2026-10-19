"""
Standalone test for route-constrained snapping (no pytest dependency).
Run: python3 -I duk_micro/tests/test_route_snap.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "libs"))

from duk_common.state.route_snap import (  # noqa: E402
    build_route_index,
    snap_to_route,
    apply_route_snap_stateless,
    route_slice,
    _point_at_s,
)


def _offset(lat, lon, dnorth_m, deast_m):
    """Add a metre offset to a coordinate (small-angle)."""
    dlat = dnorth_m / 111_320.0
    dlon = deast_m / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def make_hairpin_route():
    """
    Out-and-back ('hairpin') route: north up a street, U-turn, south down a
    PARALLEL street 25 m to the east. Nearest-vertex snapping jumps between the
    two arms near the turn — this is the loop bug we must not reproduce.
    Returns [[lon, lat], ...].
    """
    base_lat, base_lon = 8.5241, 76.9366
    coords = []
    # Outbound: 0 → 500 m north, one vertex every 20 m.
    for d in range(0, 520, 20):
        lat, lon = _offset(base_lat, base_lon, d, 0.0)
        coords.append([lon, lat])
    # Return: 500 m south, parallel street 25 m east.
    for d in range(500, -20, -20):
        lat, lon = _offset(base_lat, base_lon, d, 25.0)
        coords.append([lon, lat])
    return coords


def test_build_index():
    idx = build_route_index(make_hairpin_route())
    assert idx["pts"], "index should not be empty"
    assert idx["cum"][0] == 0.0
    assert idx["total"] > 900.0, f"hairpin should be ~1000 m, got {idx['total']:.0f}"
    # cum must be strictly non-decreasing
    assert all(b >= a for a, b in zip(idx["cum"], idx["cum"][1:]))
    print(f"  build_index OK (total={idx['total']:.0f} m, {len(idx['pts'])} vertices)")


def test_monotonic_progress_through_hairpin():
    """
    Simulate a bus driving the whole hairpin with ±8 m GPS jitter. Progress s
    must be monotonic non-decreasing the entire time — proving the dot never
    jumps to the return arm at the turn.
    """
    idx = build_route_index(make_hairpin_route())
    base_lat, base_lon = 8.5241, 76.9366
    state = {}
    last_s = -1.0
    backtracks = 0

    # Walk the true path (outbound then return) and perturb each fix.
    truth = []
    for d in range(0, 500, 10):
        truth.append(_offset(base_lat, base_lon, d, 0.0))
    for d in range(500, 0, -10):
        truth.append(_offset(base_lat, base_lon, d, 25.0))

    jitter = [(-8, 3), (5, -6), (2, 7), (-4, -2), (6, 1)]  # repeating pattern, metres
    for k, (tlat, tlon) in enumerate(truth):
        jn, je = jitter[k % len(jitter)]
        glat, glon = _offset(tlat, tlon, jn, je)
        out_lat, out_lon, off_route, state = apply_route_snap_stateless(
            glat, glon, 30.0, state, idx
        )
        assert not off_route, f"fix {k} wrongly flagged off-route"
        s = state["s"]
        if s < last_s - 1e-6:
            backtracks += 1
        last_s = s

    assert backtracks == 0, f"progress went backwards {backtracks} times"
    # Should have advanced most of the way around the hairpin.
    assert last_s > 850.0, f"expected to finish near end, s={last_s:.0f}"
    print(f"  monotonic_progress OK (final s={last_s:.0f} m, 0 backtracks)")


def test_snapped_point_is_on_route():
    """Snapped output must sit on the polyline, not at the jittered fix."""
    idx = build_route_index(make_hairpin_route())
    base_lat, base_lon = 8.5241, 76.9366
    tlat, tlon = _offset(base_lat, base_lon, 200.0, 0.0)
    glat, glon = _offset(tlat, tlon, 7, -9)  # 7 m north, 9 m west of truth
    res = snap_to_route(glat, glon, idx, s_last=180.0)
    assert res is not None
    s_lat, s_lon, s_new, perp = res
    # Perpendicular distance from the on-route point back to the true line is ~0.
    on_lat, on_lon = _point_at_s(idx, s_new)
    err = math.hypot((on_lat - s_lat) * 111320, (on_lon - s_lon) * 111320 * math.cos(math.radians(s_lat)))
    assert err < 1.0, f"snapped point not on route (err={err:.2f} m)"
    assert 150 < s_new < 250, f"s_new {s_new:.0f} outside expected band"
    print(f"  on_route OK (s={s_new:.0f} m, perp={perp:.1f} m)")


def test_off_route_hysteresis():
    """
    A single stray fix must NOT flip off-route; three in a row must. Then a
    return to the corridor must re-acquire and clear the flag.
    """
    idx = build_route_index(make_hairpin_route())
    base_lat, base_lon = 8.5241, 76.9366
    # Seed on-route.
    glat, glon = _offset(base_lat, base_lon, 100.0, 0.0)
    _, _, off, state = apply_route_snap_stateless(glat, glon, 30.0, {}, idx)
    assert not off

    # One stray fix 120 m east — beyond corridor but should hold (hysteresis).
    far_lat, far_lon = _offset(base_lat, base_lon, 110.0, 120.0)
    _, _, off, state = apply_route_snap_stateless(far_lat, far_lon, 30.0, state, idx)
    assert not off, "single stray fix should not flip off-route"
    assert state["off_route_hits"] == 1

    # Two more → confirmed off-route.
    for _ in range(2):
        _, _, off, state = apply_route_snap_stateless(far_lat, far_lon, 30.0, state, idx)
    assert off, "three consecutive strays should flip off-route"

    # Return to the corridor → back on route, counter cleared.
    back_lat, back_lon = _offset(base_lat, base_lon, 130.0, 0.0)
    _, _, off, state = apply_route_snap_stateless(back_lat, back_lon, 30.0, state, idx)
    assert not off, "return to corridor should clear off-route"
    assert state["off_route_hits"] == 0
    print("  off_route_hysteresis OK (1 stray holds, 3 flip, return clears)")


def make_straight_route():
    """Straight 2 km route north, one vertex every 20 m. Returns [[lon, lat], ...]."""
    base_lat, base_lon = 8.5241, 76.9366
    coords = []
    for d in range(0, 2020, 20):
        lat, lon = _offset(base_lat, base_lon, d, 0.0)
        coords.append([lon, lat])
    return coords


def test_reacquires_after_gps_gap():
    """
    After a gap longer than the forward window (bus moved ~450 m between fixes
    while still on the route), the snap must re-acquire instead of staying
    off-route for the rest of the trip.
    """
    idx = build_route_index(make_straight_route())
    base_lat, base_lon = 8.5241, 76.9366
    state = {}
    for d in (100.0, 120.0, 140.0):
        lat, lon = _offset(base_lat, base_lon, d, 0.0)
        _, _, off, state = apply_route_snap_stateless(lat, lon, 30.0, state, idx)
    assert not off
    s_before = state["s"]

    offs = []
    for d in (600.0, 620.0, 640.0, 660.0):
        lat, lon = _offset(base_lat, base_lon, d, 0.0)
        _, _, off, state = apply_route_snap_stateless(lat, lon, 30.0, state, idx)
        offs.append(off)
    assert not any(offs), f"on-route fixes after a gap must not go off-route: {offs}"
    assert state["s"] > s_before + 400, f"s should jump forward, got {state['s']:.0f}"
    assert state["off_route_hits"] == 0
    print(f"  reacquire_after_gap OK (s {s_before:.0f} -> {state['s']:.0f})")


def test_single_wild_fix_does_not_teleport():
    """One far-ahead fix on the route must not move s; it needs a confirming fix."""
    idx = build_route_index(make_straight_route())
    base_lat, base_lon = 8.5241, 76.9366
    lat, lon = _offset(base_lat, base_lon, 100.0, 0.0)
    _, _, _, state = apply_route_snap_stateless(lat, lon, 30.0, {}, idx)
    s0 = state["s"]
    wild_lat, wild_lon = _offset(base_lat, base_lon, 1500.0, 0.0)
    _, _, off, state = apply_route_snap_stateless(wild_lat, wild_lon, 30.0, state, idx)
    assert state["s"] == s0, "a single unconfirmed far fix must not move s"
    back_lat, back_lon = _offset(base_lat, base_lon, 110.0, 0.0)
    _, _, off, state = apply_route_snap_stateless(back_lat, back_lon, 30.0, state, idx)
    assert not off and "reacq_s" not in state
    print("  single_wild_fix OK (needs confirmation before re-acquire)")


def test_route_slice_monotonic():
    idx = build_route_index(make_hairpin_route())
    seg = route_slice(idx, 100.0, 300.0)
    assert len(seg) >= 2
    # Endpoints interpolated at the right places.
    p0, p1 = _point_at_s(idx, 100.0), _point_at_s(idx, 300.0)
    assert abs(seg[0][1] - p0[0]) < 1e-6 and abs(seg[0][0] - p0[1]) < 1e-6
    assert abs(seg[-1][1] - p1[0]) < 1e-6 and abs(seg[-1][0] - p1[1]) < 1e-6
    print(f"  route_slice OK ({len(seg)} points for 200 m leg)")


def test_empty_route_falls_back():
    idx = build_route_index([])
    out_lat, out_lon, off, state = apply_route_snap_stateless(8.52, 76.93, 30.0, {}, idx)
    assert off, "empty route must signal off-route so caller uses OSRM"
    assert (out_lat, out_lon) == (8.52, 76.93)
    print("  empty_route OK (falls back to OSRM path)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} route_snap tests...\n")
    for t in tests:
        t()
    print(f"\nAll {len(tests)} tests passed.")
