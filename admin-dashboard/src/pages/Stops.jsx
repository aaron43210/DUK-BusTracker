// src/pages/Stops.jsx
// ─────────────────────────────────────────────────────────────────────────────
// Stops & Routes — list all bus stops, add new ones, edit coordinates/order,
// and delete stops. Changes persist instantly to the backend database.
// ─────────────────────────────────────────────────────────────────────────────

import React, { useState, useEffect, useRef } from 'react';
import { getAdminStops, createStop, updateStop, deleteStop } from '../api.js';
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
function Modal({ open, onClose, title, children, footer }) {
  return (
    <div className={`modal-overlay ${open ? 'open' : ''}`}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="modal-close" onClick={onClose}>&#x2715;</button>
        </div>
        {children}
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

// ── Stops page ────────────────────────────────────────────────────────────────
export default function Stops() {
  const showToast = useToast();

  const [stops,   setStops]   = useState([]); // all bus stops from the API
  const [loading, setLoading] = useState(true);

  // editingId: null → "Add new stop" mode; non-null → "Edit stop #id" mode
  const [stopModal, setStopModal] = useState(false);
  const [editingId, setEditingId] = useState(null);

  // Form field values — split into separate useState for simplicity
  const [fName,  setFName]  = useState(''); // stop name
  const [fLat,   setFLat]   = useState(''); // latitude
  const [fLon,   setFLon]   = useState(''); // longitude
  const [fOrder, setFOrder] = useState(''); // order_index (position on the route)

  // Delete confirmation
  const [deleteModal,  setDeleteModal]  = useState(false);
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
            zoom: 14
        });
        
        const geocoderApi = {
            forwardGeocode: async (config) => {
                const features = [];
                try {
                    let request = `https://nominatim.openstreetmap.org/search?q=${config.query}&format=geojson&polygon_geojson=1&addressdetails=1`;
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
    setEditingId(null);           // null = create mode
    setFName(''); setFLat(''); setFLon(''); setFOrder(''); // clear all fields
    setStopModal(true);
  }

  // ── Open Edit modal ───────────────────────────────────────────────────────
  function openEditModal(stop) {
    setEditingId(stop.id);        // non-null = edit mode
    setFName(stop.name);
    setFLat(String(stop.lat));    // convert number → string so input value is controlled
    setFLon(String(stop.lon));
    setFOrder(String(stop.order_index + 1)); // 1-based indexing for UI
    setStopModal(true);
  }

// Save Stop
// Validates the form fields before creating a new stop or updating an existing stop.
async function saveStop() {
    // Ensure all required fields are filled
    if (!fName.trim() || !fLat || !fLon || fOrder === '') {
        showToast('All fields are required', 'error');
        return;
    }

    // Build the body — parseFloat/parseInt convert string inputs to numbers
    const body = {
      name:        fName.trim(),
      lat:         parseFloat(fLat),
      lon:         parseFloat(fLon),
      order_index: parseInt(fOrder, 10) - 1, // Convert back to 0-based for DB
    };


    setSubmitting(true);
    try {
      if (editingId) {
        // Edit mode: PUT /admin/api/stops/:id
        await updateStop(editingId, body);
        showToast('Stop updated');
      } else {
        // Create mode: POST /admin/api/stops (route_id 1 = default route)
        await createStop({ ...body, route_id: 1 }); // spread merges body + route_id
        showToast('Stop added');
      }
      setStopModal(false);
      fetchStops(); // refresh the table
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
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setSubmitting(false);
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
                <th>Order</th>
                <th>Route</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {stops.map((s, i) => (
                <tr key={s.id}>
                  <td><code>{i + 1}</code></td>
                  <td><strong style={{ fontWeight: 600 }}>{s.name}</strong></td>
                  {/* fontFamily monospace makes coordinates align cleanly */}
                  <td style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-muted)' }}>
                    {s.lat}
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-muted)' }}>
                    {s.lon}
                  </td>
                  <td className="text-muted">{s.order_index + 1}</td>
                  <td>
                    <span className="badge badge-blue">Route {s.route_id}</span>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: '6px' }}>
                      {/* Arrow function captures the stop object in the closure */}
                      <button className="btn btn-ghost btn-sm" onClick={() => openEditModal(s)}>
                        Edit
                      </button>
                      <button className="btn btn-danger btn-sm" onClick={() => openDeleteModal(s)}>
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {stops.length === 0 && (
                <tr>
                  <td colSpan="7" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '40px' }}>
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
            <label className="form-label">Stop Name</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. Pattom Junction"
              value={fName}
              onChange={e => setFName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="form-group">
            <label className="form-label">Order Index</label>
            <input
              type="number"
              className="form-input"
              placeholder="e.g. 5"
              min="0"
              value={fOrder}
              onChange={e => setFOrder(e.target.value)}
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
