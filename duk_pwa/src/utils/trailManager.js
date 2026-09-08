import { getTripTrace } from '../api';

// Maximum number of trail coordinates to keep in memory.
// Beyond this, oldest points are dropped to avoid unbounded growth
// as a long route accumulates thousands of GPS pings.
const MAX_TRAIL_COORDS = 2000;

class TrailManager {
  constructor(mapInstance = null) {
    this.map = mapInstance;
    this.trailCoords = []; // Base history trace
    this.tripId = null;

    // Dirty flag: only push to MapLibre when the path actually changed.
    // drawLive() is called at 60 FPS by GPSAnimator even when the animating
    // segment hasn't changed — the dirty check keeps redundant setData() calls
    // from hammering the WebGL pipeline on every frame.
    this._lastDrawnLength = -1;
    this._lastAnimPathLength = -1;
  }

  setMap(mapInstance) {
    this.map = mapInstance;
    this._lastDrawnLength = -1;
    this._lastAnimPathLength = -1;
    this.drawLive([]);
  }

  async init(tripId) {
    if (!tripId) return;
    this.tripId = tripId;

    try {
      const data = await getTripTrace(tripId);
      if (!data) return;

      if (data.coordinates && data.coordinates.length >= 2) {
        this.trailCoords = data.coordinates; // [[lon, lat], ...]
        this._lastDrawnLength = -1;          // force redraw after load
        this.drawLive([]);
      }
    } catch (e) {
      console.warn("[TrailManager] Failed to load trip history:", e);
    }
  }

  /**
   * Called by GPSAnimator when a segment animation finishes completely.
   */
  commitSegment(path) {
    if (!path || path.length < 2) return;

    if (this.trailCoords.length > 0) {
      const last = this.trailCoords[this.trailCoords.length - 1];
      const first = path[0];
      if (Math.abs(last[0] - first[0]) < 0.0001 && Math.abs(last[1] - first[1]) < 0.0001) {
        this.trailCoords.push(...path.slice(1));
      } else {
        this.trailCoords.push(...path);
      }
    } else {
      this.trailCoords.push(...path);
    }

    // Trim oldest points so the array doesn't grow without bound
    if (this.trailCoords.length > MAX_TRAIL_COORDS) {
      this.trailCoords = this.trailCoords.slice(this.trailCoords.length - MAX_TRAIL_COORDS);
    }

    this._lastDrawnLength = -1; // force redraw — trail base has grown
    this.drawLive([]);
  }

  /**
   * Called by GPSAnimator on every animation frame (~60 FPS).
   * Concatenates history + live animation path and updates the MapLibre source.
   *
   * Performance: setData() triggers a WebGL upload. We avoid calling it when
   * nothing has actually changed by tracking the total coordinate count.
   * For a static animatingPath of N points, this reduces ~60 WebGL uploads/s
   * down to one upload per new GPS ping (typically 1 per 5–15 seconds).
   */
  drawLive(animatingPath) {
    if (!this.map) return;
    const source = this.map.getSource('trail');
    if (!source) return;

    const histLen = this.trailCoords.length;
    const animLen = animatingPath ? animatingPath.length : 0;

    // Skip setData when nothing changed
    if (histLen === this._lastDrawnLength && animLen === this._lastAnimPathLength) {
      return;
    }
    this._lastDrawnLength = histLen;
    this._lastAnimPathLength = animLen;

    const fullPath = this.trailCoords.slice(); // shallow copy

    if (animatingPath && animLen > 0) {
      if (fullPath.length > 0) {
        const last = fullPath[fullPath.length - 1];
        const first = animatingPath[0];
        if (Math.abs(last[0] - first[0]) < 0.0001 && Math.abs(last[1] - first[1]) < 0.0001) {
          fullPath.push(...animatingPath.slice(1));
        } else {
          fullPath.push(...animatingPath);
        }
      } else {
        fullPath.push(...animatingPath);
      }
    }

    source.setData({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: fullPath.length >= 2 ? fullPath : [],
      },
    });
  }

  clear() {
    this.trailCoords = [];
    this.tripId = null;
    this._lastDrawnLength = -1;
    this._lastAnimPathLength = -1;
    this.drawLive([]);
  }
}

export default TrailManager;
