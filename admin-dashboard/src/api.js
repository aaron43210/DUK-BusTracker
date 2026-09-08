/*
  src/api.js — DUK Bus Tracker Admin Dashboard
  ─────────────────────────────────────────────
  ALL communication with the FastAPI backend lives here.
  No fetch() calls are scattered across components — they all import from this file.

  Base URL strategy:
    All admin API calls use /api/v1/admin/* directly — the same prefix as the
    rest of the application. No /admin/api/* rewrite layer is needed.
    Vite's dev proxy forwards /api/* to the backend during development.
*/

// ── Base URL ──────────────────────────────────────────────────────────────────
// Empty string means "same origin" — Vite's proxy forwards /api/* to the backend.
// In production, the build process injects VITE_API_URL.
const BASE = import.meta.env.VITE_API_URL || '';

export function getWsBusUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = BASE ? BASE.replace(/^https?:\/\//, '') : window.location.host;
  // Include the admin token as a query param so the realtime-gateway can
  // authenticate the WebSocket handshake before accepting the connection.
  const token = _token ? `?token=${encodeURIComponent(_token)}` : '';
  return `${protocol}//${host}/api/v1/ws/bus${token}`;
}

// ── Internal: shared fetch wrapper ───────────────────────────────────────────
// `_token` — the admin token returned by /api/v1/admin/login.
// Stored in module-level state so every apiFetch call can use it.
let _token = localStorage.getItem('admin_token') || '';

// setToken: called by App.jsx right after a successful login
export function setToken(t) {
  _token = t;
  localStorage.setItem('admin_token', t);
}

// getToken: lets components check if we are logged in
export function getToken() {
  return _token; // empty string = not logged in
}

// clearToken: called by App.jsx on logout — wipes the token from memory and localStorage
export function clearToken() {
  _token = '';
  localStorage.removeItem('admin_token');
}

// ── Core fetch helper ─────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Token': _token,
        ...(opts.headers || {}),
      },
      ...opts,
    });
  } catch (err) {
    window.dispatchEvent(new Event('server-error'));
    throw err;
  }

  // Auto-logout on auth failure — clears the stale token and redirects to login
  // so the admin doesn't remain in a misleadingly "logged in" state.
  if (res.status === 401 || res.status === 403) {
    clearToken();
    window.location.href = '/login';
    throw new Error('Session expired. Please log in again.');
  }

  if (!res.ok) {
    if (res.status >= 500) {
      window.dispatchEvent(new Event('server-error'));
    }
    const text = await res.text();
    let detail = text;
    try {
      detail = JSON.parse(text).detail || text;
    } catch (_) { }
    throw new Error(detail);
  }

  return res.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function login(username, password) {
  // POST /api/v1/admin/login — public endpoint, no X-Admin-Token needed
  const res = await fetch(BASE + '/api/v1/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });

  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail || 'Login failed');
  }

  const data = await res.json(); // { token: "..." }
  return data.token;
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export async function getLatestGps() {
  return apiFetch('/api/v1/latest');
}

export async function getTripState() {
  return apiFetch('/api/v1/trip_state');
}

export async function getTripTrace(tripId) {
  if (!tripId) return apiFetch('/api/v1/current_trace');
  return apiFetch(`/api/v1/trip_trace/${tripId}`);
}

export async function getStops() {
  return apiFetch('/api/v1/stops');
}

// ── Trips ─────────────────────────────────────────────────────────────────────

export async function getTrips() {
  return apiFetch('/api/v1/admin/trips');
}

// updateTripStatus: POST /api/v1/admin/trip/status
// body = { trip_id, status, late_by_minutes?, cancellation_reason? }
export async function updateTripStatus(body) {
  return apiFetch('/api/v1/admin/trip/status', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// createTrip: POST /api/v1/admin/trip/create — sends a JSON body (not query params)
export async function createTrip(direction, tripDate = '', createReturn = false) {
  return apiFetch('/api/v1/admin/trip/create', {
    method: 'POST',
    body: JSON.stringify({
      route_id: 1,
      direction,
      trip_date: tripDate || undefined,
      create_return: createReturn,
    }),
  });
}

export async function ensureTodayTrips() {
  return apiFetch('/api/v1/admin/trips/today', { method: 'POST' });
}

export async function cancelAdvanceTrip({ trip_date, direction, also_cancel_return = false, reason = '' }) {
  return apiFetch('/api/v1/admin/trip/cancel-advance', {
    method: 'POST',
    body: JSON.stringify({ trip_date, direction, also_cancel_return, reason: reason || undefined }),
  });
}

// revokeCancelTrip: POST /api/v1/admin/trip/restore (was /revoke-cancel)
export async function revokeCancelTrip({ trip_id, reason = '' }) {
  return apiFetch('/api/v1/admin/trip/restore', {
    method: 'POST',
    body: JSON.stringify({ trip_id, reason: reason || undefined }),
  });
}

// revokeRangeCancel: POST /api/v1/admin/trip/restore-range
export async function revokeRangeCancel({ trip_ids, reason = '' }) {
  return apiFetch('/api/v1/admin/trip/restore-range', {
    method: 'POST',
    body: JSON.stringify({ trip_ids, reason: reason || undefined }),
  });
}

// getRouteHistory: GET /api/v1/admin/gps/history (was /admin/api/route-history)
export async function getRouteHistory(fromDate, toDate) {
  return apiFetch(`/api/v1/admin/gps/history?from_date=${fromDate}&to_date=${toDate}`);
}

// ── Stops ─────────────────────────────────────────────────────────────────────

export async function getAdminStops() {
  return apiFetch('/api/v1/admin/stops');
}

export async function createStop(body) {
  return apiFetch('/api/v1/admin/stops', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function updateStop(id, body) {
  return apiFetch(`/api/v1/admin/stops/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export async function deleteStop(id) {
  return apiFetch(`/api/v1/admin/stops/${id}`, { method: 'DELETE' });
}

// setStopRole: PUT /api/v1/admin/stops/{id}/role
// role: "morning_origin" | "morning_destination" | "evening_origin" | "evening_destination"
export async function setStopRole(id, role) {
  return apiFetch(`/api/v1/admin/stops/${id}/role`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  });
}

// ── Broadcast ─────────────────────────────────────────────────────────────────

export async function sendBroadcast(title, body) {
  return apiFetch('/api/v1/admin/broadcast', {
    method: 'POST',
    body: JSON.stringify({ title, body }),
  });
}

// ── Suggestions ───────────────────────────────────────────────────────────────

export async function getSuggestions() {
  return apiFetch('/api/v1/admin/suggestions');
}

export async function deleteSuggestion(id) {
  return apiFetch(`/api/v1/admin/suggestions/${id}`, { method: 'DELETE' });
}

// updateSuggestion: maps to POST /api/v1/admin/suggestions/{id}/respond
// Uses the field name "response" that the backend SuggestionResponseRequest expects.
export async function updateSuggestion(id, data) {
  return apiFetch(`/api/v1/admin/suggestions/${id}/respond`, {
    method: 'POST',
    body: JSON.stringify({
      response: data.admin_response ?? data.response ?? '',
      status: data.status ?? 'resolved',
    }),
  });
}

// ── Routing & Geometry ────────────────────────────────────────────────────────
export async function getRouteGeometry() {
  return apiFetch('/api/v1/route_geometry');
}

export async function getRouteSegment(lat1, lon1, lat2, lon2) {
  return apiFetch(`/api/v1/route_segment?lat1=${lat1}&lon1=${lon1}&lat2=${lat2}&lon2=${lon2}`);
}

export async function snapRoute(points) {
  return apiFetch('/api/v1/snap_route', {
    method: 'POST',
    body: JSON.stringify({ points }),
  });
}
