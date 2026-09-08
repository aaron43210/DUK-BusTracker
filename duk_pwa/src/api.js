/**
 * api.js
 * Centralized backend API client.
 */

const BASE_URL = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
const DEFAULT_TIMEOUT_MS = 30000;

const TOKEN_KEY = 'duk_jwt_token';

let apiToken = localStorage.getItem(TOKEN_KEY) || '';

export function setApiToken(token) {
  apiToken = token || '';
  if (apiToken) localStorage.setItem(TOKEN_KEY, apiToken);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getApiToken() {
  return apiToken;
}

export function clearApiToken() {
  setApiToken('');
}

/**
 * Builds the WebSocket endpoint from the configured backend base URL.
 */
export function getWsBusUrl() {
  const base = BASE_URL || window.location.origin;
  const url = new URL('/api/v1/ws/bus', base);

  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';

  if (apiToken) {
    url.searchParams.append('token', apiToken);
  }

  return url.toString();
}

function buildUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${BASE_URL}${path}`;
}

function dispatchApiEvent(name, detail) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

/**
 * Safe JSON/text response parser.
 */
async function parseResponse(res) {
  if (res.status === 204) return null;

  const contentType = res.headers.get('content-type') || '';

  if (contentType.includes('application/json')) {
    return res.json().catch(() => null);
  }

  return res.text().catch(() => '');
}

/**
 * Shared fetch wrapper.
 * Handles:
 * - JWT auth header
 * - timeout
 * - server/offline/session-expired events
 * - JSON parsing
 */
async function apiFetch(path, options = {}) {
  const {
    method = 'GET',
    body,
    headers = {},
    timeout = DEFAULT_TIMEOUT_MS,
    silent = false
  } = options;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  const isFormData = body instanceof FormData;
  const requestHeaders = {
    ...(body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
    ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
    ...headers
  };

  try {
    const res = await fetch(buildUrl(path), {
      method,
      body,
      headers: requestHeaders,
      signal: controller.signal
    });

    const payload = await parseResponse(res);

    if (!res.ok) {
      if (!silent) {
        if (res.status === 401) dispatchApiEvent('api:unauthorized');
        else if (res.status >= 500) dispatchApiEvent('api:server-error');
      }

      const detail =
        payload?.detail ||
        payload?.message ||
        payload ||
        `Request failed with status ${res.status}`;

      const error = new Error(detail);
      error.status = res.status;
      error.payload = payload;

      throw error;
    }

    return payload;
  } catch (error) {
    if (!silent && error.name !== 'AbortError') {
      dispatchApiEvent('api:offline', error);
    }

    if (error.name === 'AbortError') {
      const timeoutError = new Error('Request timed out. Please try again.');
      timeoutError.status = 408;
      throw timeoutError;
    }

    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

/* Auth */
export function register(name, email, boarding_stop_id) {
  return apiFetch('/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, email, boarding_stop_id })
  });
}

export function verify(email, otp) {
  return apiFetch('/api/v1/auth/verify', {
    method: 'POST',
    body: JSON.stringify({ email, otp })
  });
}

export function updatePreferences(prefs) {
  return apiFetch('/api/v1/auth/preferences', {
    method: 'PATCH',
    body: JSON.stringify(prefs)
  });
}

/* Tracking */
export function getStops() {
  return apiFetch('/api/v1/stops');
}

export function getLatestGps() {
  return apiFetch('/api/v1/latest', { silent: true });
}

export function getTripState() {
  return apiFetch('/api/v1/trip_state', { silent: true });
}

export function getRouteHistory(tripId = null) {
  const query = tripId ? `?trip_id=${encodeURIComponent(tripId)}` : '';
  return apiFetch(`/api/v1/route_history${query}`, { silent: true });
}

export function getEta(stopId) {
  return apiFetch(`/api/v1/eta?stop_id=${encodeURIComponent(stopId)}`, {
    silent: true
  });
}

export function getAllEtas() {
  return apiFetch('/api/v1/eta/all', { silent: true });
}

export function getRouteGeometry() {
  return apiFetch(`/api/v1/route_geometry?t=${Date.now()}`);
}

export function getRouteSegment(lat1, lon1, lat2, lon2) {
  const params = new URLSearchParams({
    lat1,
    lon1,
    lat2,
    lon2
  });

  return apiFetch(`/api/v1/route_segment?${params.toString()}`);
}

export function getTripTrace(tripId) {
  if (!tripId) return apiFetch('/api/v1/current_trace', { silent: true });
  return apiFetch(`/api/v1/trip_trace/${encodeURIComponent(tripId)}`, {
    silent: true
  });
}

/* Suggestions */
export function submitSuggestion(suggestion, trip = '', location = '') {
  return apiFetch('/api/v1/suggestion', {
    method: 'POST',
    body: JSON.stringify({ suggestion, trip, location })
  });
}

/* Notifications */
export function getMyNotifications() {
  return apiFetch('/api/v1/notifications', { silent: true });
}

export function markNotificationRead(id) {
  return apiFetch(`/api/v1/notifications/${encodeURIComponent(id)}/read`, {
    method: 'PUT'
  });
}
