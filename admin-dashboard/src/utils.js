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

// ── Date helpers ────────────────────────────────────────────────────────────

/** Returns today's date as a YYYY-MM-DD string. */
export function todayStr() {
  return new Date().toISOString().split('T')[0];
}
