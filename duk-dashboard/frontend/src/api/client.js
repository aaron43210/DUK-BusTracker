// src/api/client.js
const DEFAULT_TIMEOUT_MS = 8000;

async function request(path, options = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(
    () => ctrl.abort(),
    options.timeoutMs || DEFAULT_TIMEOUT_MS
  );
  try {
    const res = await fetch(path, {
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`${res.status} ${res.statusText} — ${body.slice(0, 200)}`);
    }
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  tripState:     () => request('/api/v1/trip_state'),
  stops:         () => request('/api/v1/stops'),
  latest:        () => request('/api/v1/latest'),
  routeGeometry: () => request('/api/v1/route_geometry'),
  currentTrace:  () => request('/api/v1/current_trace'),
  eta: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null)
    ).toString();
    return request(`/api/v1/eta${qs ? \`?\${qs}\` : ''}`);
  },
  history: (date) => request(`/api/v1/history/${date}`),
  wsUrl: () => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${window.location.host}/api/v1/ws/bus`;
  },
};
