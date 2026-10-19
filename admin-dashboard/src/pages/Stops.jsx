// src/pages/Stops.jsx
// ─────────────────────────────────────────────────────────────────────────────
// Stops & Routes — list all bus stops, add new ones, edit coordinates/order,
// and delete stops. Changes persist instantly to the backend database.
// ─────────────────────────────────────────────────────────────────────────────

import React, { useState, useEffect, useRef } from 'react';
import { getAdminStops, createStop, updateStop, deleteStop, setStopRole, refreshRouteGeometry, reorderStops } from '../api.js';
import { useToast } from '../App.jsx';

// ---------------------------------------------------------------------------
// MapLibre GL & Geocoder Asset URLs + Lazy Loader
// ---------------------------------------------------------------------------
const MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css";
const MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js";
const GEOCODER_CSS = "https://unpkg.com/@maplibre/maplibre-gl-geocoder@1.5.0/dist/maplibre-gl-geocoder.css";
const GEOCODER_JS = "https://unpkg.com/@maplibre/maplibre-gl-geocoder@1.5.0/dist/maplibre-gl-geocoder.min.js";
const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

function loadMapLibreWithGeocoder() {
  return new Promise((resolve) => {
    if (window.maplibregl && window.MaplibreGeocoder) {
      resolve({ maplibregl: window.maplibregl, MaplibreGeocoder: window.MaplibreGeocoder });
      return;
    }

    // Load CSS
    if (!document.querySelector(`link[href="${MAPLIBRE_CSS}"]`)) {
      const link = document.createElement("link"); link.rel = "stylesheet"; link.href = MAPLIBRE_CSS;
      document.head.appendChild(link);
    }
    if (!document.querySelector(`link[href="${GEOCODER_CSS}"]`)) {
      const link = document.createElement("link"); link.rel = "stylesheet"; link.href = GEOCODER_CSS;
      document.head.appendChild(link);
    }

    // Load JS sequentially (Geocoder depends on MapLibre)
    const mlScript = document.createElement("script");
    mlScript.src = MAPLIBRE_JS;
    mlScript.onload = () => {
      const gcScript = document.createElement("script");
      gcScript.src = GEOCODER_JS;
      gcScript.onload = () => resolve({ maplibregl: window.maplibregl, MaplibreGeocoder: window.MaplibreGeocoder });
      document.head.appendChild(gcScript);
    };
    document.head.appendChild(mlScript);
  });
}

// ── Modal ─────────────────────────────────────────────────────────────────────
// Shared modal wrapper — kept local so this file is self-contained.
// Stays mounted (the map instance lives inside it), so `inert` keeps the closed
// form out of the tab order / a11y tree and stops autoFocus firing on load.
function Modal({ open, onClose, title, children, footer }) {
  return (
    <div className={`modal-overlay ${open ? 'open' : ''}`} inert={!open}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        {/* Pinned header */}
        <div className="modal-header" style={{ padding: '20px 28px 16px', flexShrink: 0 }}>
          <div className="modal-title">{title}</div>
          <button className="modal-close" onClick={onClose} aria-label="Close">&#x2715;</button>
        </div>
        {/* Scrollable body */}
        <div style={{ overflowY: 'auto', padding: '20px 28px 16px', flex: 1 }}>
          {children}
        </div>
        {/* Pinned footer */}
        {footer && (
          <div className="modal-footer" style={{ padding: '16px 28px 20px', flexShrink: 0 }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Time conversion helpers ───────────────────────────────────────────────────
// DB stores times as "HH:MM AM" / "HH:MM PM" strings.
// <input type="time"> works in 24h format ("HH:MM").
// These two helpers convert between the two representations.

/** "07:35 AM" or "18:12" → "07:35" or "18:12" (24-hour, for the time input) */
function to24h(ampmStr) {
  if (!ampmStr) return '';
  const match = ampmStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
  if (!match) return ampmStr; // already 24h — return as-is
  let h = parseInt(match[1], 10);
  const m = match[2];
  const period = match[3].toUpperCase();
  if (period === 'PM' && h !== 12) h += 12;
  if (period === 'AM' && h === 12) h = 0;
  return `${String(h).padStart(2, '0')}:${m}`;
}

/** "07:35" → "07:35 AM" | "18:12" → "06:12 PM" (for storage in DB) */
function to12h(h24Str) {
  if (!h24Str) return '';
  const [hStr, mStr] = h24Str.split(':');
  let h = parseInt(hStr, 10);
  const period = h >= 12 ? 'PM' : 'AM';
  if (h > 12) h -= 12;
  if (h === 0) h = 12;
  return `${String(h).padStart(2, '0')}:${mStr} ${period}`;
}

// ── Stops page ────────────────────────────────────────────────────────────────
export default function Stops() {
  const showToast = useToast();

  const [stops, setStops] = useState([]); // all bus stops from the API
  const [loading, setLoading] = useState(true);

  // editingId: null → "Add new stop" mode; non-null → "Edit stop #id" mode
  const [stopModal, setStopModal] = useState(false);
  const [editingId, setEditingId] = useState(null);

  // Form field values — split into separate useState for simplicity
  const [fName, setFName] = useState('');         // stop name
  const [fLat, setFLat] = useState('');           // latitude
  const [fLon, setFLon] = useState('');           // longitude
  const [fMorningTime, setFMorningTime] = useState(''); // scheduled morning arrival e.g. "07:35 AM"
  const [fEveningTime, setFEveningTime] = useState(''); // scheduled evening arrival e.g. "06:12 PM"

  // Delete confirmation
  const [deleteModal, setDeleteModal] = useState(false);
  const [deletingStop, setDeletingStop] = useState(null); // full stop object

  const [submitting, setSubmitting] = useState(false); // prevents double-submit

  // fetchStops: GET /admin/api/stops — returns all stops ordered by route + index
  async function fetchStops() {
    try {
      const data = await getAdminStops();
      setStops(data);
    } catch (err) {
      showToast(err.message || 'Failed to load stops', 'error');
    } finally {
      setLoading(false);
    }
  }

  // Fetch on mount
  useEffect(() => { fetchStops(); }, []);

  async function moveStop(type, stopId, direction) {
    // 1. Extract both independent sequences based on current logical DB order
    let morningSeq = [...stops].sort((a, b) => (a.morning_order_index || 0) - (b.morning_order_index || 0));
    let eveningSeq = [...stops].sort((a, b) => (a.evening_order_index || 0) - (b.evening_order_index || 0));

    // 2. Perform the swap on the targeted sequence
    let targetSeq = type === 'morning' ? morningSeq : eveningSeq;
    
    const index = targetSeq.findIndex(s => s.id === stopId);
    if (index === -1) return;
    
    if ((index === 0 && direction === -1) || (index === targetSeq.length - 1 && direction === 1)) return;

    const targetIndex = index + direction;
    const temp = targetSeq[index];
    targetSeq[index] = targetSeq[targetIndex];
    targetSeq[targetIndex] = temp;

    // 3. Rebuild the payload with strict 0..N-1 indexing for BOTH sequences.
    // This automatically heals any pre-existing duplicates in the database.
    const payloadMap = {};
    stops.forEach(s => { payloadMap[s.id] = { id: s.id }; });

    morningSeq.forEach((s, i) => { payloadMap[s.id].morning_order_index = i; });
    eveningSeq.forEach((s, i) => { payloadMap[s.id].evening_order_index = i; });

    const payload = Object.values(payloadMap);

    try {
      await reorderStops(payload);
      fetchStops();
    } catch (e) {
      showToast('Failed to reorder', 'error');
    }
  }

  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const markerRef = useRef(null);

  // Map initialization
  useEffect(() => {
    if (!stopModal) {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        markerRef.current = null;
      }
      return;
    }

    setTimeout(() => {
      if (!mapContainerRef.current) return;

      loadMapLibreWithGeocoder().then(({ maplibregl, MaplibreGeocoder }) => {
        const initLat = parseFloat(fLat) || 8.5241;
        const initLon = parseFloat(fLon) || 76.9366;

        mapRef.current = new maplibregl.Map({
          container: mapContainerRef.current,
          style: MAP_STYLE,
          center: [initLon, initLat],
          zoom: 14,
          minZoom: 6,
          maxZoom: 18,
          maxBounds: [
            [73.50, 7.50],
            [84.50, 19.50]
          ]
        });

        const geocoderApi = {
          forwardGeocode: async (config) => {
            const features = [];
            try {
              let request = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(config.query)}&format=geojson&polygon_geojson=1&addressdetails=1`;
              const response = await fetch(request);
              const geojson = await response.json();
              for (let feature of geojson.features) {
                let center = [feature.bbox[0] + (feature.bbox[2] - feature.bbox[0]) / 2, feature.bbox[1] + (feature.bbox[3] - feature.bbox[1]) / 2];
                let point = {
                  type: 'Feature',
                  geometry: { type: 'Point', coordinates: center },
                  place_name: feature.properties.display_name,
                  properties: feature.properties,
                  text: feature.properties.display_name,
                  place_type: ['place'],
                  center: center
                };
                features.push(point);
              }
            } catch (e) { console.error("Geocoding error", e); }
            return { features };
          }
        };

        const geocoder = new MaplibreGeocoder(geocoderApi, {
          maplibregl: maplibregl,
          placeholder: "Search for a location",
          showResultsWhileTyping: true,
          minLength: 3,
          debounceSearch: 1000 // IMPORTANT: 1 second delay to avoid Nominatim banning the IP
        });
        mapRef.current.addControl(geocoder, 'top-left');

        markerRef.current = new maplibregl.Marker({ color: "#ef4444" })
          .setLngLat([initLon, initLat])
          .addTo(mapRef.current);

        mapRef.current.on('click', (e) => {
          const lng = e.lngLat.lng.toFixed(5);
          const lat = e.lngLat.lat.toFixed(5);
          markerRef.current.setLngLat([lng, lat]);
          setFLat(lat);
          setFLon(lng);
        });

        geocoder.on('result', (e) => {
          const lng = e.result.center[0].toFixed(5);
          const lat = e.result.center[1].toFixed(5);
          markerRef.current.setLngLat([lng, lat]);
          setFLat(lat);
          setFLon(lng);
        });
      });
    }, 150);
  }, [stopModal]);

  // ── Open Add modal ────────────────────────────────────────────────────────
  function openAddModal() {
    setEditingId(null);
    setFName(''); setFLat(''); setFLon('');
    setFMorningTime(''); setFEveningTime('');
    setStopModal(true);
  }

  // ── Open Edit modal ───────────────────────────────────────────────────────
  function openEditModal(stop) {
    setEditingId(stop.id);
    setFName(stop.name);
    setFLat(String(stop.lat));
    setFLon(String(stop.lon));
    // Convert stored AM/PM string → 24h for <input type="time">
    setFMorningTime(to24h(stop.morning_time || ''));
    setFEveningTime(to24h(stop.evening_time || ''));
    setStopModal(true);
  }

  async function saveStop() {
    if (!fName.trim() || !fLat || !fLon) {
      showToast('Name and location are required', 'error');
      return;
    }
    if (!fMorningTime) {
      showToast('Morning Time is required', 'error');
      return;
    }
    if (!fEveningTime) {
      showToast('Evening Time is required', 'error');
      return;
    }

    const body = {
      name:                fName.trim(),
      lat:                 parseFloat(fLat),
      lon:                 parseFloat(fLon),
      // Convert 24h picker value back to AM/PM string for DB storage
      morning_time: to12h(fMorningTime),
      evening_time: to12h(fEveningTime),
    };

    setSubmitting(true);
    try {
      if (editingId) {
        await updateStop(editingId, body);
        showToast('Stop updated');
      } else {
        await createStop({ ...body, route_id: 1 });
        showToast('Stop added');
      }
      setStopModal(false);
      fetchStops();
      // Stop coords/order changed → recompute the planned route line so the
      // PWA map + trail snap to the new shape (fire-and-forget; OSRM recompute
      // runs server-side, no need to block the modal close).
      refreshRouteGeometry().catch(() => {});
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setSubmitting(false);
    }
  }

  // ── Delete stop ───────────────────────────────────────────────────────────
  function openDeleteModal(stop) {
    setDeletingStop(stop);  // store the stop object so we can show its name
    setDeleteModal(true);
  }

  async function confirmDelete() {
    setSubmitting(true);
    try {
      await deleteStop(deletingStop.id);
      showToast(`"${deletingStop.name}" removed`);
      setDeleteModal(false);
      fetchStops();
      // Removing a stop changes the route shape → recompute planned geometry.
      refreshRouteGeometry().catch(() => {});
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setSubmitting(false);
    }
  }

  // ── Set terminal role ─────────────────────────────────────────────────────
  const [roleSubmitting, setRoleSubmitting] = useState(null); // holds role string being set

  async function handleSetRole(stopId, role) {
    setRoleSubmitting(role);
    try {
      await setStopRole(stopId, role);
      showToast(`Terminal role set successfully`);
      fetchStops();
    } catch (err) {
      showToast(err.message || 'Failed to set role', 'error');
    } finally {
      setRoleSubmitting(null);
    }
  }

  // ── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner"></div>
        <p>Loading stops…</p>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Stops &amp; Routes</div>
          <div className="page-sub">
            {stops.length} stop{stops.length !== 1 ? 's' : ''} — add, edit, or remove from the route
          </div>
        </div>
        <button className="btn btn-primary" onClick={openAddModal}>Add Stop</button>
      </div>

      {/* ── Route Terminal Configuration card ── */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ fontWeight: 700, fontSize: '14px', color: 'var(--text)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Route Terminal Configuration</div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '16px' }}>
          Select which stop acts as the origin or destination for each trip direction. Changing a role moves it immediately.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
          {[
            { role: 'morning_origin', label: 'Morning Origin', flag: 'is_morning_origin' },
            { role: 'morning_destination', label: 'Morning Destination', flag: 'is_morning_destination' },
            { role: 'evening_origin', label: 'Evening Origin', flag: 'is_evening_origin' },
            { role: 'evening_destination', label: 'Evening Destination', flag: 'is_evening_destination' },
          ].map(({ role, label, flag }) => {
            const currentHolder = stops.find(s => s[flag]);
            const isLoading = roleSubmitting === role;
            return (
              <div key={role}>
                <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  {label}
                </div>
                <select
                  aria-label={label}
                  disabled={isLoading}
                  value={currentHolder?.id ?? ''}
                  onChange={e => {
                    const val = parseInt(e.target.value, 10);
                    if (!isNaN(val)) handleSetRole(val, role);
                  }}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    border: `2px solid ${currentHolder ? 'var(--text)' : 'var(--border)'}`,
                    borderRadius: 0,
                    background: 'var(--surface)',
                    color: 'var(--text)',
                    fontSize: '13px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    outline: 'none',
                    opacity: isLoading ? 0.6 : 1,
                  }}
                >
                  <option value="">— Not set —</option>
                  {stops.map(s => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
                {isLoading && (
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>Saving…</div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Stops table */}
      <div className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Stop Name</th>
                <th>Latitude</th>
                <th>Longitude</th>
                <th>Morning Order</th>
                <th>Evening Order</th>
                <th>Morning Time</th>
                <th>Evening Time</th>
                <th>Route Terminal Role</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {stops.map((s, i) => (
                <tr key={s.id}>
                  <td><code>{i + 1}</code></td>
                  <td><strong style={{ fontWeight: 600 }}>{s.name}</strong></td>
                  <td style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-muted)' }}>{s.lat}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-muted)' }}>{s.lon}</td>
                  <td className="text-muted">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {s.morning_order_index + 1}
                      <div style={{ display: 'flex', flexDirection: 'column' }}>
                        <button style={{ border: 'none', background: 'none', cursor: 'pointer', padding: '0', fontSize: '10px' }} onClick={() => moveStop('morning', s.id, -1)}>▲</button>
                        <button style={{ border: 'none', background: 'none', cursor: 'pointer', padding: '0', fontSize: '10px' }} onClick={() => moveStop('morning', s.id, 1)}>▼</button>
                      </div>
                    </div>
                  </td>
                  <td className="text-muted">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {s.evening_order_index + 1}
                      <div style={{ display: 'flex', flexDirection: 'column' }}>
                        <button style={{ border: 'none', background: 'none', cursor: 'pointer', padding: '0', fontSize: '10px' }} onClick={() => moveStop('evening', s.id, -1)}>▲</button>
                        <button style={{ border: 'none', background: 'none', cursor: 'pointer', padding: '0', fontSize: '10px' }} onClick={() => moveStop('evening', s.id, 1)}>▼</button>
                      </div>
                    </div>
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-muted)' }}>
                    {s.morning_time || '—'}
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-muted)' }}>
                    {s.evening_time || '—'}
                  </td>
                  <td>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                      {s.is_morning_origin && <span style={{ background: '#e5e7eb', color: '#111', fontSize: '11px', fontWeight: 600, padding: '2px 7px', borderRadius: '2px', letterSpacing: '0.03em' }}>M.Origin</span>}
                      {s.is_morning_destination && <span style={{ background: '#e5e7eb', color: '#111', fontSize: '11px', fontWeight: 600, padding: '2px 7px', borderRadius: '2px', letterSpacing: '0.03em' }}>M.Dest</span>}
                      {s.is_evening_origin && <span style={{ background: '#e5e7eb', color: '#111', fontSize: '11px', fontWeight: 600, padding: '2px 7px', borderRadius: '2px', letterSpacing: '0.03em' }}>E.Origin</span>}
                      {s.is_evening_destination && <span style={{ background: '#e5e7eb', color: '#111', fontSize: '11px', fontWeight: 600, padding: '2px 7px', borderRadius: '2px', letterSpacing: '0.03em' }}>E.Dest</span>}
                      {!s.is_morning_origin && !s.is_morning_destination && !s.is_evening_origin && !s.is_evening_destination && (
                        <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>—</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: '6px' }}>
                      <button className="btn btn-ghost btn-sm" onClick={() => openEditModal(s)}>Edit</button>
                      <button className="btn btn-danger btn-sm" onClick={() => openDeleteModal(s)}>Remove</button>
                    </div>
                  </td>
                </tr>
              ))}
              {stops.length === 0 && (
                <tr>
                  <td colSpan="9" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '40px' }}>
                    No stops yet. Add the first stop to get started.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Add / Edit Stop modal ── */}
      <Modal
        open={stopModal}
        onClose={() => setStopModal(false)}
        title={editingId ? 'Edit Stop' : 'Add Stop'} // title changes based on mode
        footer={
          <>
            <button className="btn btn-ghost" onClick={() => setStopModal(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={saveStop} disabled={submitting}>
              {submitting ? 'Saving…' : (editingId ? 'Save Changes' : 'Add Stop')}
            </button>
          </>
        }
      >
        {/* Two-column form layout */}
        <div className="col-2">
          <div className="form-group">
            <label className="form-label" htmlFor="stop-name">Stop Name</label>
            <input
              id="stop-name"
              type="text"
              className="form-input"
              placeholder="e.g. Pattom Junction"
              value={fName}
              onChange={e => setFName(e.target.value)}
              autoFocus
            />
          </div>

        </div>
        <div className="col-2" style={{ marginTop: '8px' }}>
          <div className="form-group">
            <label className="form-label" htmlFor="stop-morning-time">Morning Time</label>
            <input
              id="stop-morning-time"
              type="time"
              className="form-input"
              value={fMorningTime}
              onChange={e => setFMorningTime(e.target.value)}
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="stop-evening-time">Evening Time</label>
            <input
              id="stop-evening-time"
              type="time"
              className="form-input"
              value={fEveningTime}
              onChange={e => setFEveningTime(e.target.value)}
            />
          </div>
        </div>
        <div className="form-group" style={{ marginTop: '8px' }}>
          <label className="form-label">
            Location <span style={{ fontWeight: 'normal', color: 'var(--text-muted)' }}>(Search or click to pin)</span>
          </label>
          <div
            ref={mapContainerRef}
            style={{ width: '100%', height: '240px', borderRadius: 'var(--radius)', background: 'var(--surface2)', overflow: 'hidden', border: '1px solid var(--border)' }}
          />
          {(fLat && fLon) ? (
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px', fontFamily: 'monospace' }}>
              Selected Coordinates: {fLat}, {fLon}
            </div>
          ) : (
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
              Select a location on the map.
            </div>
          )}
        </div>

      </Modal>

      {/* ── Delete confirmation modal ── */}
      <Modal
        open={deleteModal}
        onClose={() => setDeleteModal(false)}
        title="Remove Stop"
        footer={
          <>
            <button className="btn btn-ghost" onClick={() => setDeleteModal(false)}>Cancel</button>
            <button className="btn btn-danger" onClick={confirmDelete} disabled={submitting}>
              {submitting ? 'Removing…' : 'Remove Stop'}
            </button>
          </>
        }
      >
        <p style={{ fontSize: '14px', color: 'var(--text-muted)', lineHeight: '1.6' }}>
          Are you sure you want to remove{' '}
          <strong style={{ color: 'var(--text)' }}>{deletingStop?.name}</strong>?{' '}
          This will affect the route immediately and cannot be undone.
        </p>
      </Modal>
    </div>
  );
}


/* =============================================================================
                           STOPS.JSX - LINE BY LINE EXPLANATION
===============================================================================

Lines 1 - 6
------------------------------------------------------------------------------
File information and imports.

Imports:

• React
• React Hooks (useState, useEffect)
• Stops API functions
• Toast notification hook

===============================================================================
Modal Component
===============================================================================

Lines 7 - 24
------------------------------------------------------------------------------
Reusable popup component used throughout the page.

Displays:

• Modal title
• Close button
• Modal body
• Footer buttons

Used for:

• Add Stop
• Edit Stop
• Remove Stop

===============================================================================
Stops Component
===============================================================================

Lines 25 - 46
------------------------------------------------------------------------------
Starts the Stops component.

Creates:

• Toast hook

React States:

• Stops list
• Loading state
• Add/Edit modal state
• Editing stop ID
• Form fields
• Delete confirmation state
• Selected stop
• Submit state

===============================================================================
fetchStops()
===============================================================================

Lines 47 - 58
------------------------------------------------------------------------------
Downloads all bus stops from the backend.

Steps:

• Calls getAdminStops().
• Stores stops into React state.
• Displays an error message if the API fails.
• Stops the loading animation.

===============================================================================
useEffect()
===============================================================================

Lines 59 - 61
------------------------------------------------------------------------------
Runs only once after the page loads.

Performs:

• Calls fetchStops().
• Loads all available bus stops.

===============================================================================
openAddModal()
===============================================================================

Lines 62 - 69
------------------------------------------------------------------------------
Opens the Add Stop popup.

Steps:

• Clears editing mode.
• Clears all form fields.
• Opens the modal.

===============================================================================
openEditModal(stop)
===============================================================================

Lines 70 - 79
------------------------------------------------------------------------------
Opens the Edit Stop popup.

Steps:

• Stores selected stop ID.
• Loads stop information.
• Converts numeric values to strings.
• Opens the modal.

===============================================================================
saveStop()
===============================================================================

Lines 80 - 114
------------------------------------------------------------------------------
Creates a new stop or updates an existing stop.

Validation:

• Stop name required.
• Latitude required.
• Longitude required.
• Order Index required.

Steps:

• Validate user input.
• Build request object.
• Convert string values into numbers.
• Disable submit button.

If editing:

• Update existing stop.

Otherwise:

• Create a new stop.

Finally:

• Close modal.
• Reload stops.
• Enable submit button.

===============================================================================
openDeleteModal(stop)
===============================================================================

Lines 115 - 120
------------------------------------------------------------------------------
Opens the Remove Stop confirmation popup.

Steps:

• Stores selected stop.
• Opens delete confirmation modal.

===============================================================================
confirmDelete()
===============================================================================

Lines 121 - 133
------------------------------------------------------------------------------
Deletes the selected stop.

Steps:

• Disable submit button.
• Call deleteStop().
• Remove stop from database.
• Show success message.
• Close modal.
• Reload stops.
• Enable submit button.

===============================================================================
Loading Screen
===============================================================================

Lines 134 - 143
------------------------------------------------------------------------------
Shows loading spinner while stops are loading.

Displays:

• Spinner
• Loading message

===============================================================================
Stops Page UI
===============================================================================

Lines 144 - 219
------------------------------------------------------------------------------
Builds the main Stops interface.

Displays:

• Page title
• Total number of stops
• Add Stop button

Shows a table containing:

• Stop ID
• Stop Name
• Latitude
• Longitude
• Order Index
• Route ID
• Action buttons

Actions:

• Edit Stop
• Remove Stop

If no stops exist:

• Show "No stops yet. Add the first stop to get started."

===============================================================================
Add / Edit Stop Modal
===============================================================================

Lines 220 - 279
------------------------------------------------------------------------------
Displays the Add/Edit Stop popup.

Contains:

• Stop Name
• Order Index
• Latitude
• Longitude

Buttons:

• Cancel
• Add Stop
• Save Changes

===============================================================================
Delete Confirmation Modal
===============================================================================

Lines 280 - 301
------------------------------------------------------------------------------
Displays the Remove Stop confirmation popup.

Shows:

• Selected stop name
• Warning message

Buttons:

• Cancel
• Remove Stop

===============================================================================
OVERALL EXECUTION FLOW
===============================================================================

Stops Page Starts
       │
       ▼
Create React States
       │
       ▼
fetchStops()
       │
       ▼
Download All Stops
       │
       ▼
Display Stops Table
       │
       ▼
User Selects Action
       │
       ├── Add Stop
       ├── Edit Stop
       └── Remove Stop
       │
       ▼
Validate User Input
       │
       ▼
Call Backend API
       │
       ▼
Update Database
       │
       ▼
Refresh Stops List
       │
       ▼
Update User Interface

===============================================================================
*/
