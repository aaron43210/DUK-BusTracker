// src/pages/Dashboard.jsx
// ─────────────────────────────────────────────────────────────────────────────
// Dashboard — live overview of the bus system.
// Fetches GPS, trip state, and stops in parallel; auto-refreshes every 10 s.
// ─────────────────────────────────────────────────────────────────────────────

import React, { useState, useEffect, useRef } from 'react';
// useState  → holds fetched data and UI state
// useEffect → side effects: data fetching, interval timer, map init
// useRef    → stores the interval ID without causing re-renders

import { getLatestGps, getTripState, getStops } from '../api.js';
import { useToast } from '../App.jsx';
import busSvgRaw from '../assets/bus.svg?raw';

// ── Status badge helper ───────────────────────────────────────────────────────
// Maps a status string to the correct CSS badge class and display label.
function statusBadge(status) {
  const map = {
    active: { cls: 'badge-green', label: 'Active' },
    on_trip: { cls: 'badge-green', label: 'Active' },
    connecting: { cls: 'badge-yellow', label: 'Connecting...' },
    waiting: { cls: 'badge-blue', label: 'Waiting', fontSize: '14px' },
    completed: { cls: 'badge-gray', label: 'Completed' },
    offline: { cls: 'badge-gray', label: 'Offline' },
    cancelled: { cls: 'badge-red', label: 'Cancelled' },
    late: { cls: 'badge-yellow', label: 'Late' },
  };
  const s = map[status] || { cls: 'badge-gray', label: 'Unknown' };
  return <span className={`badge ${s.cls}`}>{s.label}</span>;
}

// ── MapLibre (loaded via CDN — no npm bundle, no token needed) ───────────────
function loadMapLibre(callback) {
  if (window.maplibregl) { callback(); return; }

  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = 'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css';
  document.head.appendChild(link);

  const script = document.createElement('script');
  script.src = 'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js';
  script.onload = callback;
  document.head.appendChild(script);
}

function buildMapLive(containerId, busLat, busLon, isLive, stops, mapRef, markerRef) {
  if (!document.getElementById(containerId)) return;

  const map = new window.maplibregl.Map({
    container: containerId,
    style: 'https://tiles.openfreemap.org/styles/liberty',
    center: [busLon, busLat],
    zoom: 14,
  });
  mapRef.current = map;

  map.addControl(new window.maplibregl.NavigationControl(), 'top-right');

  // Bus marker — custom SVG bus icon (green when live, grey when last-seen/offline)
  const busColor = isLive ? '#16a34a' : '#6b7280';
  const busSvg = busSvgRaw
    .replace(/fill="#000000"/g, `fill="${busColor}"`)
    .replace(/width="512"/, 'width="100%" viewBox="0 0 512 512"')
    .replace(/height="512"/, 'height="100%"');

  const busEl = document.createElement('div');
  busEl.id = 'bus-dot';
  busEl.innerHTML = busSvg;
  busEl.style.cssText = [
    'width:36px', 'height:36px',
    'cursor:pointer',
    'filter:drop-shadow(0 3px 8px rgba(0,0,0,0.4))',
    'transition:transform 0.4s ease-out',
  ].join(';');

  const popup = new window.maplibregl.Popup({ offset: 16 }).setHTML(
    isLive
      ? '<strong>Live Bus</strong><br>Tracking in real-time'
      : '<strong>Last Known Location</strong><br>Bus is currently offline'
  );

  const marker = new window.maplibregl.Marker({ element: busEl })
    .setLngLat([busLon, busLat])
    .setPopup(popup)
    .addTo(map);
  markerRef.current = marker;

  // Stop markers — small blue dots
  (stops || []).forEach(s => {
    const el = document.createElement('div');
    el.style.cssText = 'width:10px;height:10px;background:#2563eb;border-radius:50%;border:2px solid white;cursor:pointer';
    new window.maplibregl.Marker({ element: el })
      .setLngLat([s.lon, s.lat])
      .setPopup(new window.maplibregl.Popup({ offset: 10 }).setHTML(`<strong>${s.name}</strong>`))
      .addTo(map);
  });
}

// ── Dashboard component ───────────────────────────────────────────────────────
export default function Dashboard() {
  const showToast = useToast();

  const [gps, setGps] = useState(null);
  const [tripState, setTripState] = useState(null);
  const [stops, setStops] = useState([]);
  const [loading, setLoading] = useState(true);
  const [wsStatus, setWsStatus] = useState('Connecting...');

  const markerRef = useRef(null); // MapLibre marker
  const mapRef = useRef(null); // MapLibre map instance
  const wsRef = useRef(null); // WebSocket
  const mapBuilt = useRef(false);

  // ── Initial data fetch ──────────────────────────────────────────────────────
  // This runs immediately when the Dashboard opens. It reaches out to the 
  // Postgres database to grab the current state of the system before the WebSocket connects.
  async function fetchAll() {
    try {
      // Promise.all fires all three database queries at the exact same time for speed
      const [gpsData, tripData, stopsData] = await Promise.all([
        // getLatestGps() asks the DB: "Where is the absolute last place the bus was seen?"
        // If the DB is 100% empty (or throws an error), .catch() safely returns null
        getLatestGps().catch(() => null),

        // Checks if the trip is 'waiting', 'active', 'completed', etc.
        getTripState().catch(() => null),

        // Loads the 21 bus stops to draw them on the map
        getStops().catch(() => []),
      ]);

      // Save the database responses into React state so the UI can render them
      setGps(gpsData);
      setTripState(tripData);
      setStops(stopsData);

      // Keep marker color in sync during 10s polling
      const busDot = document.getElementById('bus-dot');
      if (busDot) {
        busDot.querySelectorAll('path').forEach(p => p.setAttribute('fill', gpsData?.is_live ? '#16a34a' : '#6b7280'));
      }
    } catch {
      showToast('Failed to refresh dashboard', 'error');
    } finally {
      setLoading(false);
    }
  }

  // ── Mount: fetch once, then connect WebSocket ────────────────────────────
  useEffect(() => {
    fetchAll();

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws/bus`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setWsStatus('Live');
    ws.onclose = () => setWsStatus('Offline');

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'gps' && data.lat && data.lon) {
          setGps(prev => ({ ...prev, lat: data.lat, lon: data.lon, speed_kmh: data.speed_kmh, server_time: data.server_time, is_live: true }));

          // Move existing marker if map is already built
          if (markerRef.current) {
            markerRef.current.setLngLat([data.lon, data.lat]);

            // Turn marker green as soon as live data arrives (was grey if offline on load)
            const body = document.getElementById('bus-body');
            if (body) {
              body.setAttribute('fill', '#16a34a');
            }

            // Pan map if bus drifts out of view
            if (mapRef.current) {
              const bounds = mapRef.current.getBounds();
              if (!bounds.contains([data.lon, data.lat])) {
                mapRef.current.panTo([data.lon, data.lat]);
              }
            }
          }
        }
      } catch (_) { }
    };

    return () => ws.close();
  }, []);

  // ── Build map after initial load (always, with fallback coords) ───────────
  // Central Poly (8.5350, 76.9908) is used when GPS is offline.
  useEffect(() => {
    if (loading || mapBuilt.current) return;
    mapBuilt.current = true;
    const lat = gps?.lat ?? 8.5350;
    const lon = gps?.lon ?? 76.9908;
    const isLive = gps?.is_live === true; // true = ping in last 60s; false = offline/no data
    loadMapLibre(() =>
      setTimeout(() => buildMapLive('admin-map', lat, lon, isLive, stops, mapRef, markerRef), 150)
    );
  }, [loading]); // triggers once when loading flips false



  // ── Loading skeleton ───────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner"></div>
        <p>Loading…</p>
      </div>
    );
  }

  // ── Page render ────────────────────────────────────────────────────────────
  return (
    <div>

      {/* ── Page header ─────────────────────────────────────────── */}
      <div className="page-header">
        <div>
          <div className="page-title">Dashboard</div>

        </div>
      </div>

      {/* ── 4 stat cards ─────────────────────────────────────────── */}
      <div className="stats-row">

        {/* Trip direction */}
        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>Current Trip</div>
          <div className="stat-val" style={{ fontSize: '16px', textTransform: 'capitalize', fontWeight: 700 }}>
            {tripState?.trip || '—'}
          </div>
        </div>

        {/* GPS signal */}
        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>GPS Signal</div>
          <div>
            <span className={`badge ${gps?.is_live ? 'badge-green' : (gps ? 'badge-gray' : 'badge-red')}`}>
              {gps?.is_live ? 'Active' : (gps ? 'Offline' : 'No data')}
            </span>
          </div>
        </div>

        {/* Total stops count */}
        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>Bus Stops</div>
          <div className="stat-val">{stops.length}</div>
        </div>

        {/* Trip status */}
        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>Status</div>
          <div>
            {statusBadge(tripState?.status)}
          </div>
          {/* Late minutes — shown only if bus is running late */}
          {tripState?.late_by_minutes && (
            <div style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '6px' }}>
              {tripState.late_by_minutes} min delay
            </div>
          )}
        </div>
      </div>

      {/* ── Live Map section (full width) ────────────────────────── */}
      <div className="card">
        <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Live Position</span>
          {gps?.server_time && (
            <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-muted)', textTransform: 'none', letterSpacing: 'normal' }}>
              Last updated: {new Date(gps.server_time).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}
            </span>
          )}
        </div>
        <div id="admin-map" style={{ minHeight: '400px' }}></div>

        {/* GPS coordinates below the map */}
        {gps ? (
          <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-muted)', lineHeight: '1.6' }}>
            <span>{gps.lat?.toFixed(5)}, {gps.lon?.toFixed(5)}</span>
            {gps.speed_kmh != null && (
              <span style={{ marginLeft: '10px' }}>{gps.speed_kmh} km/h</span>
            )}
          </div>
        ) : (
          <p style={{ color: 'var(--text-muted)', marginTop: '12px', fontSize: '13px' }}>
            No GPS data available. The tracker may be offline.
          </p>
        )}
      </div>

      {/* ── Stops list ────────────────────────────────────────────── */}
      <div className="card">
        <div className="card-title">Route Stops ({stops.length})</div>
        {stops.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No stops configured.</p>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {/* Each stop shown as a small pill chip */}
            {stops.map((s, i) => (
              <span
                key={s.id}
                style={{
                  background: 'var(--surface2)',
                  border: '1px solid var(--border)',
                  borderRadius: '6px',
                  padding: '4px 11px',
                  fontSize: '12px',
                  color: 'var(--text-muted)',
                }}
              >
                {i + 1}. {s.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* =============================================================================
                         DASHBOARD.JSX - LINE BY LINE EXPLANATION
===============================================================================

Lines 1 - 17
------------------------------------------------------------------------------
File information and imports.

• Imports React.
• Imports React Router.
• Imports API functions.
• Imports Toast notification hook.

===============================================================================
statusBadge(status)
===============================================================================

Lines 18 - 32
------------------------------------------------------------------------------
Converts the trip status into a colored badge.

Supported Status:

• Active
• On Trip
• Connecting
• Waiting
• Completed
• Cancelled
• Late

If the status is not found,
it returns an "Unknown" badge.

Returns a JSX <span> with the proper CSS class.

===============================================================================
loadMapLibre(callback)
===============================================================================

Lines 33 - 46
------------------------------------------------------------------------------
Loads the MapLibre library dynamically.

Steps:

1. Checks whether MapLibre is already loaded.
2. If loaded → run callback().
3. Create CSS <link>.
4. Load MapLibre stylesheet.
5. Create JavaScript <script>.
6. Load MapLibre JS.
7. After loading finishes,
   execute callback().

===============================================================================
buildMapLive(...)
===============================================================================

Lines 47 - 95
------------------------------------------------------------------------------
Creates the live map.

Main operations:

• Checks whether map container exists.
• Creates MapLibre map.
• Sets initial center.
• Sets zoom level.
• Saves map reference.
• Adds navigation controls.
• Creates custom bus marker.
• Changes marker color based on GPS status.
• Creates popup.
• Places marker on map.
• Stores marker reference.
• Loops through every bus stop.
• Creates blue stop markers.
• Adds popup for each stop.

===============================================================================
Dashboard Component
===============================================================================

Lines 96 - 110
------------------------------------------------------------------------------
Starts the Dashboard component.

Creates:

• Toast hook
• GPS state
• Trip state
• Stops state
• Loading state
• WebSocket status

Creates references for:

• Marker
• Map
• WebSocket
• Map initialization flag

===============================================================================
fetchAll()
===============================================================================

Lines 111 - 136
------------------------------------------------------------------------------
Fetches all dashboard data.

Downloads:

• Latest GPS
• Trip state
• Stops list

Updates React state.

Updates marker color depending on
whether GPS is live or offline.

Handles API errors.

Stops loading animation.

===============================================================================
First useEffect()
===============================================================================

Lines 137 - 181
------------------------------------------------------------------------------
Runs once after the page loads.

Performs:

• Calls fetchAll().
• Creates WebSocket connection.
• Stores socket reference.
• Detects connection status.
• Receives live GPS.
• Updates GPS state.
• Moves existing marker.
• Turns marker green.
• Automatically pans map if bus leaves screen.
• Closes WebSocket when component unmounts.

===============================================================================
Second useEffect()
===============================================================================

Lines 182 - 194
------------------------------------------------------------------------------
Builds the map only once.

Steps:

• Wait until loading finishes.
• Prevent multiple map creation.
• Use GPS coordinates.
• Use fallback coordinates if GPS is unavailable.
• Load MapLibre.
• Create live map.

===============================================================================
Loading Screen
===============================================================================

Lines 195 - 204
------------------------------------------------------------------------------
Shows loading spinner while data is loading.

Displays:

• Spinner
• Loading message

===============================================================================
Dashboard UI
===============================================================================

Lines 205 - 267
------------------------------------------------------------------------------
Builds the main Dashboard interface.

Displays:

• Dashboard title
• Current Trip card
• GPS Signal card
• Total Stops card
• Trip Status card

Shows:

• GPS activity
• Last update time
• Delay information

===============================================================================
Live Map Section
===============================================================================

Lines 268 - 290
------------------------------------------------------------------------------
Displays the live map.

Shows:

• Map container
• GPS coordinates
• Current speed
• Offline message when GPS is unavailable

===============================================================================
Stops Section
===============================================================================

Lines 291 - 319
------------------------------------------------------------------------------
Displays all bus stops.

If no stops exist:

• Show "No stops configured."

Otherwise:

• Display every stop as a pill.
• Show stop number.
• Show stop name.

===============================================================================
OVERALL EXECUTION FLOW
===============================================================================

Dashboard Starts
       │
       ▼
Create React States
       │
       ▼
fetchAll()
       │
       ▼
Download GPS + Trip + Stops
       │
       ▼
Open WebSocket
       │
       ▼
Receive Live GPS
       │
       ▼
Move Marker
       │
       ▼
Build Map
       │
       ▼
Display Dashboard
       │
       ▼
Show Live Map
       │
       ▼
Show Route Stops

===============================================================================
*/