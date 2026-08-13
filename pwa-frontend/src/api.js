/**
 * api.js — DUK Bus Tracker PWA
 * All communication with the FastAPI backend lives here.
 * Pattern mirrors admin-dashboard/src/api.js for consistency.
 *
 * Dev: Vite proxy forwards /api/* and /auth/* to http://127.0.0.1:5004
 * Prod: Set VITE_API_URL env var to the backend's public URL
 */

const BASE = import.meta.env.VITE_API_URL || '';

export function getWsBusUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = BASE ? BASE.replace(/^https?:\/\//, '') : window.location.host;
  return `${protocol}//${host}/api/v1/ws/bus`;
}

// JWT token — loaded from localStorage on module init
let _token = localStorage.getItem('duk_jwt_token') || '';

export function setApiToken(t) {
  _token = t;
  localStorage.setItem('duk_jwt_token', t);
}

export function getApiToken() {
  return _token;
}

export function clearApiToken() {
  _token = '';
  localStorage.removeItem('duk_jwt_token');
}

// ── Core fetch helper ─────────────────────────────────────────────────────

async function apiFetch(path, opts = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      headers: {
        'Content-Type': 'application/json',
        ...(  _token ? { Authorization: `Bearer ${_token}` } : {}),
        ...(opts.headers || {}),
      },
      ...opts,
    });
  } catch (err) {
    window.dispatchEvent(new CustomEvent('api:offline'));
    throw err;
  }

  if (!res.ok) {
    if (res.status >= 500) {
      window.dispatchEvent(new CustomEvent('api:server-error'));
    }
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent('api:unauthorized'));
    }
    const text = await res.text();
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch (_) {}
    throw new Error(detail);
  }

  // 204 No Content
  if (res.status === 204) return null;
  return res.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────

/**
 * Step 1 — register: POST /auth/register
 * Sends OTP to university email.
 */
export async function register(name, email, boarding_stop_id) {
  return apiFetch('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, email, boarding_stop_id }),
  });
}

/**
 * Step 2 — verify OTP: POST /auth/verify
 * Returns { access_token, token_type, user }
 */
export async function verify(email, otp) {
  return apiFetch('/auth/verify', {
    method: 'POST',
    body: JSON.stringify({ email, otp }),
  });
}

/**
 * Update proximity / notification preferences: PATCH /auth/preferences
 */
export async function updatePreferences(prefs) {
  return apiFetch('/auth/preferences', {
    method: 'PATCH',
    body: JSON.stringify(prefs),
  });
}

// ── Tracking ──────────────────────────────────────────────────────────────

/** All bus stops from the DB, ordered by route + index. */
export async function getStops() {
  return apiFetch('/api/v1/stops');
}


/** Latest GPS ping — { lat, lon, speed_kmh, is_live, recorded_at } */
export async function getLatestGps() {
  return apiFetch('/api/v1/latest');
}

/** Current trip state — { trip, status, next_trip_time? } */
export async function getTripState() {
  return apiFetch('/api/v1/trip_state');
}

/** Today's visited stops + arrival times for the current session or a specific trip */
export async function getRouteHistory(trip_id = null) {
  const url = trip_id ? `/api/v1/route_history?trip_id=${trip_id}` : '/api/v1/route_history';
  return apiFetch(url);
}

/** ML-predicted ETA to a specific bus stop */
export async function getEta(stop_id) {
  return apiFetch(`/api/v1/eta?stop_id=${stop_id}`);
}


/** Road-snapped complete route geometry */
export async function getRouteGeometry() {
  return apiFetch('/api/v1/route_geometry');
}

/** Road geometry between two GPS coordinates */
export async function getRouteSegment(lat1, lon1, lat2, lon2) {
  return apiFetch(`/api/v1/route_segment?lat1=${lat1}&lon1=${lon1}&lat2=${lat2}&lon2=${lon2}`);
}



/** Complete historical snapped route trace for a specific trip ID */
export async function getTripTrace(tripId) {
  if (!tripId) return apiFetch('/api/v1/current_trace');
  return apiFetch(`/api/v1/trip_trace/${tripId}`);
}

// ── Suggestions ───────────────────────────────────────────────────────────

export async function submitSuggestion(suggestion, trip = '', location = '') {
  return apiFetch('/api/v1/suggestion', {
    method: 'POST',
    body: JSON.stringify({ suggestion, trip, location }),
  });
}

export async function getMyNotifications() {
  return apiFetch('/api/v1/auth/me/notifications');
}
