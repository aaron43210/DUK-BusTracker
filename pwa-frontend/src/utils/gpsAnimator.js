// gpsAnimator.js
// Smooth marker animation controller with buffering + clustering + road-following.

class GPSAnimator {
  constructor(options = {}) {
    // We don't take the map or marker in constructor anymore because React controls the marker
    // Instead we will call a callback function with the current coordinate to animate to
    
    this.onPositionUpdate = options.onPositionUpdate; // Callback to set marker pos
    this.onSegmentFinished = options.onSegmentFinished; // Callback to commit trail

    // ── Tuning ──────────────────────────────────────────────────────────────
    this.CLUSTER_RADIUS_M   = options.clusterRadiusM   ?? 15;   // treat as "same spot" below this
    this.MAX_ANIM_DURATION  = options.maxAnimDuration  ?? 8000; // cap animation length (ms) 
    this.MIN_ANIM_DURATION  = options.minAnimDuration  ?? 800;  // floor, avoid instant snaps
    this.FETCH_ROAD_SEGMENT = options.fetchRoadSegment ?? true; // follow real road shape
    this.segmentFetcher     = options.segmentFetcher;           // async (lat1,lon1,lat2,lon2) => [[lon,lat],...] -> we need to handle format

    // ── State ───────────────────────────────────────────────────────────────
    this.queue = [];                 // buffered incoming points: {lat, lon, timestamp}
    this.currentPos = null;          // where the marker visually is right now
    this.animating = false;
    this.animFrame = null;
    this.lastPointTime = null;
  }

  /**
   * Call this every time a new GPS point arrives from the WebSocket.
   * Does NOT move the marker immediately — just queues it.
   */
  pushPoint(lat, lon, serverTimeIso) {
    const timestamp = serverTimeIso ? new Date(serverTimeIso).getTime() : Date.now();

    // First point ever — place marker directly, nothing to animate from
    if (this.currentPos === null) {
      this.currentPos = { lat, lon };
      this.onPositionUpdate([lon, lat]);
      this.lastPointTime = timestamp;
      return;
    }

    this.queue.push({ lat, lon, timestamp });

    if (!this.animating) {
      this._processQueue();
    }
  }

  async _processQueue() {
    if (this.queue.length === 0) {
      this.animating = false;
      return;
    }
    this.animating = true;

    const next = this.queue.shift();
    const distM = this._haversineM(
      this.currentPos.lat, this.currentPos.lon, next.lat, next.lon
    );

    // ── Clustering: if the bus barely moved, don't animate — just settle ──
    if (distM < this.CLUSTER_RADIUS_M) {
      // Nudge slightly toward it (in case of long-term drift) but no travel animation
      this.currentPos = { lat: next.lat, lon: next.lon };
      this.onPositionUpdate([next.lon, next.lat]);
      this.lastPointTime = next.timestamp;
      // Immediately process any further queued points (they're likely also clustered)
      requestAnimationFrame(() => this._processQueue());
      return;
    }

    // ── Compute animation duration from actual time gap between pings ──────
    let duration = next.timestamp - (this.lastPointTime || (next.timestamp - 5000));
    duration = Math.max(this.MIN_ANIM_DURATION, Math.min(this.MAX_ANIM_DURATION, duration));
    this.lastPointTime = next.timestamp;

    // ── Optionally fetch the real road geometry for this leg ───────────────
    let path = [
      [this.currentPos.lon, this.currentPos.lat],
      [next.lon, next.lat],
    ];
    if (this.FETCH_ROAD_SEGMENT && this.segmentFetcher && distM > 20) {
      try {
        const roadPath = await this.segmentFetcher(
          this.currentPos.lat, this.currentPos.lon, next.lat, next.lon
        );
        // segmentFetcher returns GeoJSON coords like [[lon, lat], [lon, lat]]
        if (roadPath && roadPath.length >= 2) {
          path = roadPath;
        }
      } catch (e) {
        // Fall back to straight line silently
      }
    }

    this._animateAlongPath(path, duration, next);
  }

  _animateAlongPath(path, duration, targetPoint) {
    // Precompute cumulative distances along the path for even-speed interpolation
    const segLengths = [];
    let totalLength = 0;
    for (let i = 0; i < path.length - 1; i++) {
      // Note: path points are [lon, lat], _haversineM expects (lat1, lon1, lat2, lon2)
      const d = this._haversineM(path[i][1], path[i][0], path[i + 1][1], path[i + 1][0]);
      segLengths.push(d);
      totalLength += d;
    }
    if (totalLength === 0) totalLength = 1; // avoid div by zero

    const startTime = performance.now();

    const step = (now) => {
      const elapsed = now - startTime;
      const t = Math.min(1, elapsed / duration); // 0 → 1 progress

      const targetDist = t * totalLength;
      let accum = 0;
      let point = path[path.length - 1]; // default to end
      let currentSegIndex = path.length - 2;

      for (let i = 0; i < segLengths.length; i++) {
        if (accum + segLengths[i] >= targetDist) {
          const segT = segLengths[i] === 0 ? 0 : (targetDist - accum) / segLengths[i];
          const [lon1, lat1] = path[i];
          const [lon2, lat2] = path[i + 1];
          point = [
            lon1 + (lon2 - lon1) * segT,
            lat1 + (lat2 - lat1) * segT,
          ];
          currentSegIndex = i;
          break;
        }
        accum += segLengths[i];
      }

      // ── Build the exact sliced path up to the bus's front bumper ──
      const drawnPath = currentSegIndex >= 0 ? path.slice(0, currentSegIndex + 1) : [];
      drawnPath.push(point);

      this.onPositionUpdate(point, drawnPath);

      if (t < 1) {
        this.animFrame = requestAnimationFrame(step);
      } else {
        this.currentPos = { lat: targetPoint.lat, lon: targetPoint.lon };
        this.onPositionUpdate([targetPoint.lon, targetPoint.lat], path);
        if (this.onSegmentFinished) this.onSegmentFinished(path);
        // Move on to the next queued point, if any
        this._processQueue();
      }
    };

    this.animFrame = requestAnimationFrame(step);
  }

  _haversineM(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
      Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.asin(Math.sqrt(a));
  }

  destroy() {
    if (this.animFrame) cancelAnimationFrame(this.animFrame);
    this.queue = [];
  }
}

export default GPSAnimator;
