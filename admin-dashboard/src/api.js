/*
  src/api.js — DUK Bus Tracker Admin Dashboard
  ─────────────────────────────────────────────
  ALL communication with the FastAPI backend lives here.
  No fetch() calls are scattered across components — they all import from this file.
  This makes it easy to change the base URL or auth headers in one place.
*/

// ── Base URL ──────────────────────────────────────────────────────────────────
// Empty string means "same origin" — Vite's proxy forwards /api/* and /admin/api/*
// to http://127.0.0.1:5004 during development (see vite.config.js).
// In production, the build process injects VITE_API_URL.
const BASE = import.meta.env.VITE_API_URL || '';

// ── Internal: shared fetch wrapper ───────────────────────────────────────────
// `token` — the admin token returned by /admin/api/login.
// We keep it in module-level state so every api* call can use it.
let _token = localStorage.getItem('admin_token') || '';

// setToken: called by App.jsx right after a successful login
// token is stored in memory and localStorage so it persists on reload
export function setToken(t) {
  _token = t; // store the token in this module's private variable
  localStorage.setItem('admin_token', t); // persist to localStorage
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
// All API calls go through this function.
// path   = '/admin/api/trips'  (no base URL needed — Vite proxy handles it)
// opts   = extra fetch options: { method, body, etc. }
async function apiFetch(path, opts = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      headers: {
        'Content-Type':  'application/json', 
        'X-Admin-Token': _token,             
        ...(opts.headers || {}),             
      },
      ...opts, 
    });
  } catch (err) {
    // If fetch itself throws (e.g. TypeError: Failed to fetch), the server is offline/unreachable
    window.dispatchEvent(new Event('server-error'));
    throw err;
  }

  // If the response is not 2xx, throw an error with the server's message
  if (!res.ok) {
    // 502 Bad Gateway, 500 Internal Server Error, 503 Service Unavailable
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

// login: POST /admin/api/login with username + password
// Returns { token } on success; throws on bad credentials
export async function login(username, password) {
  // We DON'T send _token here — /login is a public endpoint (no X-Admin-Token needed)
  const res = await fetch(BASE + '/admin/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }), // JSON.stringify converts JS object → string
  });

  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail || 'Login failed'); // surface FastAPI's error message
  }

  const data = await res.json(); // { token: "CHANGE_ME_ADMIN_TOKEN" }
  return data.token;             // caller stores this via setToken()
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

// getLatestGps: GET /api/v1/latest — returns the most recent GPS ping from the bus
export async function getLatestGps() {
  return apiFetch('/api/v1/latest');
}

// getTripState: GET /api/v1/trip_state — returns current trip direction + status
export async function getTripState() {
  return apiFetch('/api/v1/trip_state');
}

// getTripTrace: GET /api/v1/trip_trace/:trip_id
export async function getTripTrace(tripId) {
  return apiFetch(`/api/v1/trip_trace/${tripId}`);
}

// getStops: GET /api/v1/stops — returns all bus stops (used in map + stops page)
export async function getStops() {
  return apiFetch('/api/v1/stops');
}

// ── Trips ─────────────────────────────────────────────────────────────────────

// getTrips: GET /admin/api/trips — returns last 50 trips
export async function getTrips() {
  return apiFetch('/admin/api/trips');
}

// updateTripStatus: POST /admin/api/trip/status — mark a trip as late or cancelled
// body = { trip_id, status, late_by_minutes?, cancellation_reason? }
export async function updateTripStatus(body) {
  return apiFetch('/admin/api/trip/status', {
    method: 'POST',
    body: JSON.stringify(body), // convert JS object to JSON string for the request body
  });
}

// createTrip: POST /admin/api/trip/create — create a Special Service trip (holidays/weekends)
// direction = 'forward' (morning) | 'reverse' (evening)
// tripDate = 'YYYY-MM-DD' (optional)
// createReturn = boolean (optional)
export async function createTrip(direction, tripDate = '', createReturn = false) {
  // Query params in the URL because the FastAPI endpoint uses Query() parameters
  let url = `/admin/api/trip/create?route_id=1&direction=${direction}`;
  if (tripDate) url += `&trip_date=${tripDate}`;
  if (createReturn) url += `&create_return=true`;
  
  return apiFetch(url, {
    method: 'POST',
  });
}

// ensureTodayTrips: POST /admin/api/trips/today
// Auto-creates Morning + Evening trips for today if it's a weekday and they don't exist yet.
// Safe to call multiple times — the backend is idempotent (won't create duplicates).
export async function ensureTodayTrips() {
  return apiFetch('/admin/api/trips/today', { method: 'POST' });
}

// cancelAdvanceTrip: POST /admin/api/trip/cancel-advance
// Pre-cancels trip(s) for a specific date + direction.
// Creates the trip record if needed so it always appears in the log.
export async function cancelAdvanceTrip({ trip_date, direction, also_cancel_return = false, reason = '' }) {
  return apiFetch('/admin/api/trip/cancel-advance', {
    method: 'POST',
    body: JSON.stringify({ trip_date, direction, also_cancel_return, reason: reason || undefined }),
  });
}

// revokeCancelTrip: POST /admin/api/trip/revoke-cancel
// Restores a cancelled trip back to 'scheduled' and sends a push notification.
export async function revokeCancelTrip({ trip_id, reason = '' }) {
  return apiFetch('/admin/api/trip/revoke-cancel', {
    method: 'POST',
    body: JSON.stringify({ trip_id, reason: reason || undefined }),
  });
}

// revokeRangeCancel: POST /admin/api/trip/revoke-cancel-range
// Restores multiple cancelled trips at once and sends a single push notification.
// trip_ids = array of trip IDs to restore
export async function revokeRangeCancel({ trip_ids, reason = '' }) {
  return apiFetch('/admin/api/trip/revoke-cancel-range', {
    method: 'POST',
    body: JSON.stringify({ trip_ids, reason: reason || undefined }),
  });
}

// getRouteHistory: GET /admin/api/route-history
// Returns GPS sessions + completed_dates (list of ISO date strings) for the calendar.
// from_date and to_date are YYYY-MM-DD strings.
export async function getRouteHistory(fromDate, toDate) {
  return apiFetch(`/admin/api/route-history?from_date=${fromDate}&to_date=${toDate}`);
}

// ── Stops ─────────────────────────────────────────────────────────────────────

// getAdminStops: GET /admin/api/stops — returns all stops (admin version with route_id)
export async function getAdminStops() {
  return apiFetch('/admin/api/stops');
}

// createStop: POST /admin/api/stops — add a new stop to the database
export async function createStop(body) {
  return apiFetch('/admin/api/stops', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// updateStop: PUT /admin/api/stops/:id — edit an existing stop's fields
export async function updateStop(id, body) {
  return apiFetch(`/admin/api/stops/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

// deleteStop: DELETE /admin/api/stops/:id — remove a stop permanently
export async function deleteStop(id) {
  return apiFetch(`/admin/api/stops/${id}`, {
    method: 'DELETE',
  });
}

// setStopRole: PUT /admin/api/stops/:id/role — assign morning/evening origin or destination role
// role: "morning_origin" | "morning_destination" | "evening_origin" | "evening_destination"
export async function setStopRole(id, role) {
  return apiFetch(`/admin/api/stops/${id}/role`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  });
}

// ── Broadcast ─────────────────────────────────────────────────────────────────

// sendBroadcast: POST /admin/api/broadcast — push notification to all users
export async function sendBroadcast(title, body) {
  return apiFetch('/admin/api/broadcast', {
    method: 'POST',
    body: JSON.stringify({ title, body, target: 'all' }),
  });
}

// ── Suggestions ───────────────────────────────────────────────────────────────

// getSuggestions: GET /admin/api/suggestions — returns last 100 user suggestions
export async function getSuggestions() {
  return apiFetch('/admin/api/suggestions');
}

// deleteSuggestion: DELETE /admin/api/suggestions/:id
export async function deleteSuggestion(id) {
  return apiFetch(`/admin/api/suggestions/${id}`, {
    method: 'DELETE',
  });
}

// updateSuggestion: PATCH /admin/api/suggestions/:id
export async function updateSuggestion(id, data) {
  return apiFetch(`/admin/api/suggestions/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
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



