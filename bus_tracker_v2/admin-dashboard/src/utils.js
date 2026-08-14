/**
 * admin-dashboard/src/utils.js
 * Shared utilities extracted from Dashboard.jsx and RouteHistory.jsx.
 * Import from here instead of copy-pasting into each page.
 */

// ── MapLibre GL lazy loader ─────────────────────────────────────────────────
const MAPLIBRE_CSS = 'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css';
const MAPLIBRE_JS  = 'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js';

/** Map tile style URL used across all admin map pages. */
export const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';

/**
 * Lazily load MapLibre GL JS + CSS from CDN.
 * Safe to call multiple times — skips re-injection if already loaded.
 * Returns a Promise that resolves to window.maplibregl.
 */
export function loadMapLibre() {
  return new Promise((resolve) => {
    if (window.maplibregl) { resolve(window.maplibregl); return; }

    if (!document.querySelector(`link[href="${MAPLIBRE_CSS}"]`)) {
      const link = document.createElement('link');
      link.rel   = 'stylesheet';
      link.href  = MAPLIBRE_CSS;
      document.head.appendChild(link);
    }

    const script = document.createElement('script');
    script.src    = MAPLIBRE_JS;
    script.onload = () => resolve(window.maplibregl);
    document.head.appendChild(script);
  });
}

// ── Geospatial helpers ──────────────────────────────────────────────────────

/**
 * Haversine great-circle distance in kilometres between two (lon, lat) points.
 * Note: arguments are (lon1, lat1, lon2, lat2) to match MapLibre [lon, lat] convention.
 */
export function haversineDistKm(lon1, lat1, lon2, lat2) {
  const R    = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
    Math.cos((lat2 * Math.PI) / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Date helpers ────────────────────────────────────────────────────────────

/** Returns today's date as a YYYY-MM-DD string. */
export function todayStr() {
  return new Date().toISOString().split('T')[0];
}
