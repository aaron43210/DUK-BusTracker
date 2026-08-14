import { getTripTrace, getRouteSegment } from '../api';

class TrailManager {
  constructor(setTrailCoords) {
    this.setTrailCoords = setTrailCoords;
    this.trailCoords = [];
    this.lastPoint = null;
    this.fetchingSegment = false;
  }

  async init(tripId) {
    if (!tripId) return;
    this.tripId = tripId;

    try {
      const data = await getTripTrace(tripId);
      if (!data) return;

      if (data.coordinates && data.coordinates.length >= 2) {
        this.trailCoords = data.coordinates; // [[lon, lat], ...]
        this._redrawPolyline();

        const last = data.coordinates[data.coordinates.length - 1];
        this.lastPoint = { lat: last[1], lon: last[0] };
      }
    } catch (e) {
      console.warn("[TrailManager] Failed to load trip history:", e);
    }
  }

  async addLivePoint(lat, lon) {
    if (this.fetchingSegment) {
      this._pendingPoint = { lat, lon };
      return;
    }

    if (!this.lastPoint) {
      this.lastPoint = { lat, lon };
      this.trailCoords.push([lon, lat]);
      this._redrawPolyline();
      return;
    }

    const distM = this._haversineM(this.lastPoint.lat, this.lastPoint.lon, lat, lon);
    if (distM < 15) return;

    this.fetchingSegment = true;
    try {
      const seg = await getRouteSegment(this.lastPoint.lat, this.lastPoint.lon, lat, lon);
      
      if (seg && seg.coordinates && seg.coordinates.length >= 2) {
        this.trailCoords.push(...seg.coordinates.slice(1));
      } else {
        this.trailCoords.push([lon, lat]);
      }

      this._redrawPolyline();
      this.lastPoint = { lat, lon };

    } catch (e) {
      console.warn("[TrailManager] Segment fetch failed:", e);
      this.trailCoords.push([lon, lat]);
      this._redrawPolyline();
      this.lastPoint = { lat, lon };
    } finally {
      this.fetchingSegment = false;
      if (this._pendingPoint) {
        const pending = this._pendingPoint;
        this._pendingPoint = null;
        this.addLivePoint(pending.lat, pending.lon);
      }
    }
  }

  clear() {
    this.trailCoords = [];
    this.lastPoint = null;
    this._redrawPolyline();
  }

  _redrawPolyline() {
    if (this.trailCoords.length < 2) {
      this.setTrailCoords([]);
      return;
    }
    this.setTrailCoords([...this.trailCoords]);
  }

  _haversineM(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(lat1 * Math.PI / 180) *
      Math.cos(lat2 * Math.PI / 180) *
      Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.asin(Math.sqrt(a));
  }
}

export default TrailManager;
