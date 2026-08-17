// src/utils/geo.js
const RAD = Math.PI / 180;
const R2  = 2 * 6371; // 2 × Earth radius in km

export function haversineKm(lat1, lon1, lat2, lon2) {
  const dLat = (lat2 - lat1) * RAD;
  const dLon = (lon2 - lon1) * RAD;
  const sinLat = Math.sin(dLat / 2);
  const sinLon = Math.sin(dLon / 2);
  const a =
    sinLat * sinLat +
    Math.cos(lat1 * RAD) * Math.cos(lat2 * RAD) * sinLon * sinLon;
  return R2 * Math.asin(Math.sqrt(a));
}

export function cumulativeKm(points) {
  const n    = points.length;
  const cum  = new Float64Array(n);   // typed array — O(n) space, cache-friendly
  const segs = new Float64Array(Math.max(0, n - 1));
  for (let i = 1; i < n; i++) {
    const d = haversineKm(
      points[i - 1].lat, points[i - 1].lon,
      points[i].lat,     points[i].lon
    );
    segs[i - 1] = d;
    cum[i]      = cum[i - 1] + d;
  }
  return { cum, segs, total: cum[n - 1] || 0 };
}

export function fmtKm(km) {
  if (km == null) return '—';
  return km < 10 ? `${km.toFixed(1)} km` : `${Math.round(km)} km`;
}
