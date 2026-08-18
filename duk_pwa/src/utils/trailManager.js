import { getTripTrace } from '../api';

class TrailManager {
  constructor(mapInstance = null) {
    this.map = mapInstance;
    this.trailCoords = []; // Base history trace
    this.tripId = null;
  }

  setMap(mapInstance) {
    this.map = mapInstance;
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
    this.drawLive([]);
  }

  /**
   * Called by GPSAnimator 60 times a second. Concatenates history + live animation path.
   * Modifies MapLibre WebGL source directly, bypassing React state entirely!
   */
  drawLive(animatingPath) {
    if (!this.map) return;
    const source = this.map.getSource('trail');
    if (!source) return;

    const fullPath = [...this.trailCoords];

    if (animatingPath && animatingPath.length > 0) {
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

    if (fullPath.length >= 2) {
      source.setData({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: fullPath }
      });
    } else {
      source.setData({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: [] }
      });
    }
  }

  clear() {
    this.trailCoords = [];
    this.tripId = null;
    this.drawLive([]);
  }
}

export default TrailManager;
