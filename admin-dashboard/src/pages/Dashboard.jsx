import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getLatestGps, getTripState, getStops, getRouteGeometry, getRouteSegment, getTripTrace, getWsBusUrl } from '../api.js';
import GPSAnimator from '../utils/gpsAnimator.js';
import TrailManager from '../utils/trailManager.js';
import { useToast } from '../App.jsx';
import { loadMapLibre, haversineDistKm } from '../utils.js';
import busSvgRaw from '../assets/bus.svg?raw';

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


class CenterBusControl {
  onAdd(map) {
    this._map = map;
    this._container = document.createElement('div');
    this._container.className = 'maplibregl-ctrl maplibregl-ctrl-group';

    const btn = document.createElement('button');
    btn.className = 'maplibregl-ctrl-icon';
    btn.type = 'button';
    btn.title = 'Recenter on Bus';
    btn.id = 'recenter-bus-btn';
    btn.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style="margin: auto; display: block; padding-top: 4px;"><circle cx="12" cy="12" r="3"></circle><line x1="12" y1="2" x2="12" y2="5"></line><line x1="12" y1="19" x2="12" y2="22"></line><line x1="2" y1="12" x2="5" y2="12"></line><line x1="19" y1="12" x2="22" y2="12"></line></svg>`;

    btn.onclick = () => window.dispatchEvent(new Event('recenter-bus'));

    this._container.appendChild(btn);
    return this._container;
  }
  onRemove() {
    this._container.parentNode.removeChild(this._container);
    this._map = undefined;
  }
}

function formatTripName(trip) {
  if (!trip) return '—';
  const t = trip.toLowerCase().trim();
  if (t === 'forward' || t === 'morning') return 'Morning';
  if (t === 'reverse' || t === 'evening') return 'Evening';
  if (t === 'unscheduled') return 'Unscheduled';
  return trip;
}

function isScheduledTrip(trip) {
  if (!trip) return false;
  const t = trip.toLowerCase().trim();
  return t === 'morning' || t === 'evening' || t === 'forward' || t === 'reverse';
}

function buildMapLive(containerId, busLat, busLon, isLive, stops, mapRef, markerRef, isScheduled, tripId, trailCoordsRef) {
  if (!document.getElementById(containerId)) return;

  const map = new window.maplibregl.Map({
    container: containerId,
    style: 'https://tiles.openfreemap.org/styles/liberty',
    center: [busLon, busLat],
    zoom: 14,
    minZoom: 6,
    maxZoom: 18,
    maxBounds: [
      [73.50, 7.50],  // Southwest: South of Kanyakumari / Lakshadweep Sea
      [84.50, 19.50]  // Northeast: North of Telangana & Andhra Pradesh
    ]
  });
  mapRef.current = map;

  map.addControl(new window.maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new CenterBusControl(), 'top-right');

  const busColor = isLive ? '#16a34a' : '#6b7280';
  const busSvg = busSvgRaw
    .replace(/fill="#000000"/g, `fill="${busColor}"`)
    .replace(/width="512"/, 'width="100%" viewBox="0 0 512 512"')
    .replace(/height="512"/, 'height="100%"');

  const busEl = document.createElement('div');
  busEl.id = 'bus-marker-container';
  busEl.style.cssText = 'width:36px;height:36px;cursor:pointer;filter:drop-shadow(0 3px 8px rgba(0,0,0,0.4));';

  const busInner = document.createElement('div');
  busInner.id = 'bus-dot';
  busInner.innerHTML = busSvg;
  busInner.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;transition:transform 0.3s ease-out;';
  busEl.appendChild(busInner);

  const popup = new window.maplibregl.Popup({ offset: [0, -36] }).setHTML(
    isLive ? '<strong>Live Bus</strong><br>Tracking in real-time' : '<strong>Last Known Location</strong><br>Bus is currently offline'
  );

  const marker = new window.maplibregl.Marker({ element: busEl, anchor: 'bottom' })
    .setLngLat([busLon, busLat])
    .setPopup(popup)
    .addTo(map);
  markerRef.current = marker;

  if (isScheduled) {
    (stops || []).forEach(s => {
      const el = document.createElement('div');
      el.style.cssText = 'width:10px;height:10px;background:#2563eb;border-radius:50%;border:2px solid white;cursor:pointer';
      new window.maplibregl.Marker({ element: el })
        .setLngLat([s.lon, s.lat])
        .setPopup(new window.maplibregl.Popup({ offset: 10 }).setHTML(`<strong>${s.name}</strong>`))
        .addTo(map);
    });
  }

  map.on('dragstart', () => window.dispatchEvent(new Event('map-interaction')));
  map.on('wheel', () => window.dispatchEvent(new Event('map-interaction')));
  map.on('touchstart', () => window.dispatchEvent(new Event('map-interaction')));

  map.on('load', async () => {
    if (!map.getSource('planned-route')) {
      map.addSource('planned-route', {
        type: 'geojson',
        data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } },
      });
      map.addLayer({
        id: 'planned-route-layer',
        type: 'line',
        source: 'planned-route',
        paint: { 'line-color': '#94a3b8', 'line-width': 4, 'line-opacity': 0.5, 'line-dasharray': [2, 2] },
        layout: {
          'line-cap': 'round',
          'line-join': 'round',
          'visibility': isScheduled ? 'visible' : 'none'
        },
      });
    }

    try {
      if (isScheduled) {
        const geo = await getRouteGeometry();
        if (geo?.coordinates?.length && map.getSource('planned-route')) {
          map.getSource('planned-route').setData({
            type: 'Feature',
            geometry: { type: 'LineString', coordinates: geo.coordinates },
          });
        }
      }
    } catch (_) { }

    if (!map.getSource('live-trail')) {
      map.addSource('live-trail', {
        type: 'geojson',
        data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } },
      });
      map.addLayer({
        id: 'live-trail-layer',
        type: 'line',
        source: 'live-trail',
        paint: { 'line-color': '#2563eb', 'line-width': 5, 'line-opacity': 0.9 },
        layout: {
          'line-cap': 'round',
          'line-join': 'round',
          'visibility': 'visible'
        },
      });
    }
  });
}

export default function Dashboard() {
  const showToast = useToast();

  const [gps, setGps] = useState(null);
  const [tripState, setTripState] = useState(null);
  const [stops, setStops] = useState([]);
  const [loading, setLoading] = useState(true);
  const [wsStatus, setWsStatus] = useState('Connecting...');
  const [autoCenter, setAutoCenter] = useState(true);

  const markerRef = useRef(null);
  const mapRef = useRef(null);
  const wsRef = useRef(null);
  const mapBuilt = useRef(false);
  const trailCoordsRef = useRef([]);
  const lastGpsRef = useRef(null);
  const currentPosRef = useRef(null);
  const isAutoCenterRef = useRef(true);
  const animatorRef = useRef(null);
  const trailManagerRef = useRef(null);

  useEffect(() => {
    isAutoCenterRef.current = autoCenter;
  }, [autoCenter]);

  useEffect(() => {
    const handleInteraction = () => {
      if (isAutoCenterRef.current) setAutoCenter(false);
    };
    const handleRecenter = () => {
      setAutoCenter(true);
      if (mapRef.current && currentPosRef.current) {
        mapRef.current.panTo(currentPosRef.current, { duration: 500 });
      }
    };
    window.addEventListener('map-interaction', handleInteraction);
    window.addEventListener('recenter-bus', handleRecenter);
    return () => {
      window.removeEventListener('map-interaction', handleInteraction);
      window.removeEventListener('recenter-bus', handleRecenter);
    };
  }, []);

  useEffect(() => {
    const btn = document.getElementById('recenter-bus-btn');
    if (btn) {
      btn.style.color = autoCenter ? '#2563eb' : '#475569';
    }
  }, [autoCenter]);

  
  // Initialize animator when map is ready
  useEffect(() => {
    // Only init if not already init
    if (!animatorRef.current) {
      animatorRef.current = new GPSAnimator({
        fetchRoadSegment: true,
        segmentFetcher: async (lat1, lon1, lat2, lon2) => {
          const seg = await getRouteSegment(lat1, lon1, lat2, lon2);
          if (seg && seg.coordinates) return seg.coordinates;
          return [];
        },
        onPositionUpdate: (point) => {
          currentPosRef.current = point;
          if (markerRef.current) {
            markerRef.current.setLngLat(point);
          }
          if (mapRef.current && isAutoCenterRef.current) {
            mapRef.current.jumpTo({ center: point });
          }
        }
      });
    }

    if (mapRef.current && !trailManagerRef.current) {
      trailManagerRef.current = new TrailManager(mapRef.current, 'live-trail');
      if (tripState?.trip_id && isScheduledTrip(tripState?.trip) && tripState?.status !== 'completed') {
        trailManagerRef.current.init(tripState.trip_id);
      }
    }

    return () => {
      if (animatorRef.current) animatorRef.current.destroy();
    };
  }, [tripState?.trip_id]);


  async function fetchAll() {
    try {
      const [gpsData, tripData, stopsData] = await Promise.all([
        getLatestGps().catch(() => null),
        getTripState().catch(() => null),
        getStops().catch(() => []),
      ]);

      setGps(gpsData);
      setTripState(tripData);
      setStops(stopsData);

      if (gpsData?.lat && gpsData?.lon) {
        lastGpsRef.current = [gpsData.lat, gpsData.lon];
        currentPosRef.current = [gpsData.lon, gpsData.lat];
        if (trailCoordsRef.current.length === 0) {
          trailCoordsRef.current = [[gpsData.lon, gpsData.lat]];
        }
      }

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

  // Reset trail when trip completes or goes offline
  useEffect(() => {
    if (tripState?.status === 'completed' || tripState?.status === 'offline' || tripState?.status === 'waiting') {
      if (trailManagerRef.current) {
        trailManagerRef.current.clear();
      }
    }
  }, [tripState?.status]);

  useEffect(() => {
    fetchAll();

    
    // No more aggressive 1.5s live GPS polling — we use WebSocket for real-time
    // But we still poll trip state occasionally
    const pollInterval = setInterval(async () => {
      try {
        const tripData = await getTripState().catch(() => null);
        if (tripData) setTripState(tripData);
      } catch (_) {}
    }, 15000); // 15s instead of 5s

    const wsUrl = getWsBusUrl();
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setWsStatus('Live');
    ws.onclose = () => setWsStatus('Offline');

    ws.onmessage = async (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'gps' && data.lat && data.lon) {
          setGps(prev => ({ ...prev, lat: data.lat, lon: data.lon, speed_kmh: data.speed_kmh, server_time: data.server_time, is_live: true }));
          
          if (animatorRef.current) {
            animatorRef.current.pushPoint(data.lat, data.lon, data.server_time);
          }
          
          if (trailManagerRef.current && tripState?.status !== 'completed' && isScheduledTrip(tripState?.trip)) {
            trailManagerRef.current.addLivePoint(data.lat, data.lon);
          }

          const busDot = document.getElementById('bus-dot');
          if (busDot) {
            busDot.querySelectorAll('path').forEach(p => p.setAttribute('fill', '#16a34a'));
          }
        }
      } catch (_) { }
    };

    return () => {
      clearInterval(pollInterval);
      ws.close();
    };
  }, []);


  useEffect(() => {
    if (loading || mapBuilt.current) return;
    mapBuilt.current = true;
    const lat = gps?.lat ?? 8.5350;
    const lon = gps?.lon ?? 76.9908;
    const isLive = gps?.is_live === true;
    loadMapLibre().then(() => {
      setTimeout(() => {
        const isScheduled = isScheduledTrip(tripState?.trip);
        buildMapLive('admin-map', lat, lon, isLive, stops, mapRef, markerRef, isScheduled, tripState?.trip_id, trailCoordsRef);
      }, 150);
    });
  }, [loading]);

  useEffect(() => {
    if (!mapRef.current) return;
    const map = mapRef.current;
    const isScheduled = isScheduledTrip(tripState?.trip) && tripState?.status !== 'completed';

    if (map.getLayer('live-trail-layer')) {
      map.setLayoutProperty('live-trail-layer', 'visibility', 'visible');
    }
    if (map.getLayer('planned-route-layer')) {
      map.setLayoutProperty('planned-route-layer', 'visibility', isScheduled ? 'visible' : 'none');
    }

    // When trip completes, clearing is handled by the other useEffect hook.
    // Unscheduled trips should retain their blue trail, so we no longer clear based on !isScheduled.
  }, [tripState]);

  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner"></div>
        <p>Loading…</p>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Dashboard</div>
        </div>
      </div>

      <div className="stats-row">
        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>Current Trip</div>
          <div className="stat-val" style={{ fontSize: '16px', textTransform: 'capitalize', fontWeight: 700 }}>
            {formatTripName(tripState?.trip)}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>GPS Signal</div>
          <div
            className="stat-val"
            style={{
              fontSize: '16px',
              fontWeight: 700,
              color: gps?.is_live ? '#15803d' : (gps ? '#4b5563' : '#b91c1c'),
            }}
          >
            {gps?.is_live ? 'Active' : (gps ? 'Offline' : 'No data')}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>Bus Stops</div>
          <div className="stat-val">{stops.length}</div>
        </div>

        <div className="stat-card">
          <div className="stat-label" style={{ marginBottom: '8px', marginTop: 0 }}>Status</div>
          {(() => {
            const map = {
              active: { label: 'Active', color: '#15803d' },
              on_trip: { label: 'Active', color: '#15803d' },
              connecting: { label: 'Connecting...', color: '#b45309' },
              waiting: { label: 'Waiting', color: '#2563eb' },
              completed: { label: 'Completed', color: '#4b5563' },
              offline: { label: 'Offline', color: '#4b5563' },
              cancelled: { label: 'Cancelled', color: '#b91c1c' },
              late: { label: 'Late', color: '#b45309' },
            };
            const s = tripState?.status;
            const res = map[s] || { label: s ? s.charAt(0).toUpperCase() + s.slice(1) : '—', color: '#4b5563' };
            return (
              <div className="stat-val" style={{ fontSize: '16px', fontWeight: 700, color: res.color }}>
                {res.label}
              </div>
            );
          })()}
          {tripState?.late_by_minutes && (
            <div style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '6px' }}>
              {tripState.late_by_minutes} min delay
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '20px', marginTop: '20px' }}>
        <div className="card">
          <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Live Position</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              {gps?.ist_time && (
                <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-muted)', textTransform: 'none', letterSpacing: 'normal' }}>
                  Last updated: {new Date(gps.ist_time).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })}
                </span>
              )}
            </div>
          </div>

          <div style={{ position: 'relative' }}>
            <div id="admin-map" style={{ width: '100%', height: '65vh', minHeight: '350px', borderRadius: '8px', border: '1px solid #e2e8f0' }}></div>
          </div>

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

        <div className="card">
          <div className="card-title">Route Stops ({stops.length})</div>
          {stops.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No stops configured.</p>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
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
    </div>
  );
}