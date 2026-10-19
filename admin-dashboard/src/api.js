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

// ── Admin base path ───────────────────────────────────────────────────────────
// The URL to redirect to after session expiry. Matches the Nginx location block
// that serves this admin panel. Change VITE_ADMIN_BASE_PATH in .env to override.
const ADMIN_BASE_PATH = import.meta.env.VITE_ADMIN_BASE_PATH || '/duk/admin2026/';

export function getWsBusUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = BASE ? BASE.replace(/^https?:\/\//, '') : window.location.host;
  // No token in the URL: the realtime-gateway authenticates the handshake from
  // the HttpOnly `admin_session` cookie, which the browser sends automatically
  // on the same-site WebSocket upgrade. Keeps the JWT out of logs/history.
  return `${protocol}//${host}/api/v1/ws/bus`;
}

// ── Internal: auth state ──────────────────────────────────────────────────────
// The admin JWT now lives ONLY in an HttpOnly cookie set by /api/v1/admin/login,
// so JavaScript can neither read nor exfiltrate it (XSS can't steal the session).
// We keep a non-sensitive boolean in sessionStorage purely for client-side route
// gating; the cookie is the real credential and the server enforces it on every
// request (a 401/403 triggers the redirect below).
let _isAuthed = sessionStorage.getItem('admin_authed') === '1';

// setToken: called by App.jsx right after a successful login. The JWT argument is
// intentionally ignored — the cookie already carries it; we only flip the flag.
export function setToken(_t) {
  _isAuthed = true;
  sessionStorage.setItem('admin_authed', '1');
}

// getToken: lets components check if we appear to be logged in (non-authoritative)
export function getToken() {
  return _isAuthed ? '1' : '';
}

// clearToken: called by App.jsx on logout — clears the local flag. The cookie is
// cleared server-side by the /logout route (and on any 401/403).
export function clearToken() {
  _isAuthed = false;
  sessionStorage.removeItem('admin_authed');
}

// ── Core fetch helper ─────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      // Send the HttpOnly session cookie on every request (incl. cross-origin dev).
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
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
    window.location.href = ADMIN_BASE_PATH;
    throw new Error('Session expired. Please log in again.');
  }

  if (!res.ok) {
    if (res.status >= 500) {
      window.dispatchEvent(new Event('server-error'));
    }
    const text = await res.text();
    let detail = text;
    try {
      const json = JSON.parse(text);
      if (typeof json.detail === 'string') {
        detail = json.detail;
      } else if (Array.isArray(json.detail)) {
        // Pydantic validation errors return an array of objects — join them into a readable string
        detail = json.detail.map(e => e.msg || JSON.stringify(e)).join(', ');
      } else if (json.detail) {
        detail = JSON.stringify(json.detail);
      }
    } catch (_) { }
    throw new Error(detail);
  }

  return res.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function login(username, password) {
  // POST /api/v1/admin/login — public endpoint. credentials:'include' is required
  // so the browser stores the HttpOnly session cookie the server sets in response.
  const res = await fetch(BASE + '/api/v1/admin/login', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });

  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail || 'Login failed');
  }

  // The session itself is in the HttpOnly cookie now; the body token is ignored
  // by the browser client. Return a truthy marker so callers can flip UI state.
  await res.json().catch(() => ({}));
  return '1';
}

// logout: revoke the session server-side (blocklists the JTI and clears the
// HttpOnly cookie). Best-effort — the local flag is cleared by the caller.
export async function logout() {
  try {
    await fetch(BASE + '/api/v1/admin/logout', {
      method: 'POST',
      credentials: 'include',
    });
  } catch (_err) {
    // Network error on logout is non-fatal; local state is still cleared.
  }
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
  return apiFetch('/api/v1/admin/trips/today', {
    method: 'POST',
    body: JSON.stringify({ route_id: 1 }),
  });
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

export async function reorderStops(stopsArray) {
  return apiFetch(`/api/v1/admin/stops/reorder`, {
    method: 'POST',
    body: JSON.stringify(stopsArray),
  });
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

export async function updateSuggestion(id, data) {
  // 'data.status' now comes directly from the dropdown which uses valid backend values:
  // 'resolved', 'rejected', 'pending'
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

// Recompute the planned-route geometry (both directions) after a stop is
// added, moved, or removed. Without this the drawn route line + the
// gps-ingestion snap route stay stale until the 24h cache TTL expires.
export async function refreshRouteGeometry() {
  return apiFetch('/api/v1/admin/refresh_route_geometry', { method: 'POST' });
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
