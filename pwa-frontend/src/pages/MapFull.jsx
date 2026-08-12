/**
 * MapFull.jsx — DUK Bus Tracker PWA
 * Full-screen live map view.
 * Mirrors MapFullScreen.tsx from the React Native app.
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Crosshair, Plus, Minus } from 'lucide-react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import {
  getLatestGps, getTripState, getRouteHistory, getEta, getRouteGeometry, getRouteSegment, getStops,
} from '../api';
import { getUser } from '../storage';
import {
  getDelayBadge, haversineDistKm, getMapViewport,
} from '../timetable';

const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
const POLL_MS = 1500;
const DEFAULT_CENTER = [76.848, 8.583];
const DEFAULT_ZOOM = 13;

export default function MapFull() {
  const navigate = useNavigate();
  const mapContainer = useRef(null);
  const mapRef = useRef(null);
  const mapReadyRef = useRef(false);
  const busMarkerRef = useRef(null);
  const currentPosRef = useRef(null);
  const intervalRef = useRef(null);
  const animIntervalRef = useRef(null);
  const cameraLocked = useRef(true); // auto-center on bus
  const initialCentered = useRef(false);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [tripState, setTripState] = useState(null);
  const [busPosition, setBusPosition] = useState(null);
  const [stops, setStops] = useState([]);
  const [plannedCoords, setPlannedCoords] = useState([]);
  const [trailCoords, setTrailCoords] = useState([]);
  const [animatedBus, setAnimatedBus] = useState(null);
  const [selectedStop, setSelectedStop] = useState(null);
  const [eta, setEta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [autoCenter, setAutoCenter] = useState(true);
  const [is3D, setIs3D] = useState(false);

  const user = getUser();

  // ── Initialize map ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      minZoom: 6,
      maxZoom: 18,
      maxBounds: [
        [73.50, 7.50],  // Southwest: South of Kanyakumari / Lakshadweep Sea
        [84.50, 19.50]  // Northeast: North of Telangana & Andhra Pradesh
      ],
      attributionControl: false,
    });
    mapRef.current = map;

    map.on('load', () => {
      mapReadyRef.current = true;

      // Planned route
      map.addSource('planned', { type: 'geojson', data: emptyLine() });
      map.addLayer({
        id: 'planned-layer', type: 'line', source: 'planned',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#94a3b8', 'line-width': 3, 'line-dasharray': [2, 3], 'line-opacity': 0.5 },
      });

      // Trail
      map.addSource('trail', { type: 'geojson', data: emptyLine() });
      map.addLayer({
        id: 'trail-layer', type: 'line', source: 'trail',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#2563eb', 'line-width': 4.5, 'line-opacity': 0.9 },
      });

      // Stops
      map.addSource('stops', { type: 'geojson', data: emptyFC() });
      map.addLayer({
        id: 'stops-circle', type: 'circle', source: 'stops',
        paint: {
          'circle-radius': 6,
          'circle-color': '#64748b',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#ffffff',
        },
      });

      // Stop click
      map.on('click', 'stops-circle', (e) => {
        const raw = e.features[0]?.properties?.stop;
        if (raw) setSelectedStop(JSON.parse(raw));
      });
      map.on('mouseenter', 'stops-circle', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'stops-circle', () => { map.getCanvas().style.cursor = ''; });

      // Unlock camera on manual pan
      map.on('dragstart', () => {
        cameraLocked.current = false;
        setAutoCenter(false);
      });
    });

    return () => {
      mapReadyRef.current = false;
      map.remove();
      mapRef.current = null;
      busMarkerRef.current = null;
    };
  }, []);

  // ── Update GeoJSON sources ──────────────────────────────────────────────
  const updateSources = useCallback((planned, trail, stopsArr) => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;

    map.getSource('planned')?.setData({
      type: 'Feature', geometry: { type: 'LineString', coordinates: planned },
    });
    map.getSource('trail')?.setData({
      type: 'Feature', geometry: { type: 'LineString', coordinates: trail },
    });
    map.getSource('stops')?.setData({
      type: 'FeatureCollection',
      features: stopsArr.filter(s => s.lat && s.lon).map(s => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [Number(s.lon), Number(s.lat)] },
        properties: { stop: JSON.stringify(s) },
      })),
    });
  }, []);

  // ── Animate bus marker ──────────────────────────────────────────────────
  const updateBusMarker = useCallback((coord, isLive = false) => {
    const map = mapRef.current;
    if (!map) return;
    const iconSrc = isLive ? '/bus_green.png' : '/bus_gray.png';
    const tagClass = isLive ? 'bus-marker-tag bus-marker-tag--live' : 'bus-marker-tag bus-marker-tag--muted';

    if (!busMarkerRef.current) {
      const el = document.createElement('div');
      el.className = 'bus-marker-wrapper';
      el.innerHTML = `
        <div class="bus-marker-container">
          <img src="${iconSrc}" class="bus-marker-img" alt="Bus" />
        </div>
        <div class="${tagClass}">BUS</div>
      `;
      busMarkerRef.current = new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat(coord)
        .addTo(map);
    } else {
      const img = busMarkerRef.current.getElement()?.querySelector('.bus-marker-img');
      const tag = busMarkerRef.current.getElement()?.querySelector('.bus-marker-tag');
      if (img && img.getAttribute('src') !== iconSrc) {
        img.src = iconSrc;
      }
      if (tag) {
        tag.className = tagClass;
      }
      busMarkerRef.current.setLngLat(coord);
    }
    if (cameraLocked.current) {
      map.easeTo({ center: coord, duration: 600 });
    }
  }, []);

  const animateBusTo = useCallback(async (targetLat, targetLon) => {
    const startPos = currentPosRef.current || [targetLon, targetLat];
    const [startLon, startLat] = startPos;
    const distKm = haversineDistKm(startLon, startLat, targetLon, targetLat);

    if (!currentPosRef.current || distKm > 8) {
      const c = [targetLon, targetLat];
      setAnimatedBus(c); updateBusMarker(c, true);
      currentPosRef.current = c; return;
    }
    if (distKm < 0.0005) {
      const c = [targetLon, targetLat];
      setAnimatedBus(c); updateBusMarker(c, true);
      currentPosRef.current = c; return;
    }

    let poly = [[startLon, startLat], [targetLon, targetLat]];
    try {
      const seg = await getRouteSegment(startLat, startLon, targetLat, targetLon);
      if (seg?.coordinates?.length > 1) poly = seg.coordinates;
    } catch (_) { }

    if (animIntervalRef.current) clearInterval(animIntervalRef.current);
    const steps = Math.min(poly.length, 10);
    const chunk = Math.max(1, Math.floor(poly.length / steps));
    let idx = 0;
    animIntervalRef.current = setInterval(() => {
      if (idx >= poly.length) {
        clearInterval(animIntervalRef.current);
        currentPosRef.current = [targetLon, targetLat];
        return;
      }
      const c = [poly[idx][0], poly[idx][1]];
      setAnimatedBus(c); updateBusMarker(c, true);
      idx += chunk;
    }, Math.floor(POLL_MS / steps));
  }, [updateBusMarker]);

  // ── Fetch data ──────────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    try {
      const [tripRes, busRes, histRes, stopsRes] = await Promise.allSettled([
        getTripState(), getLatestGps(), getRouteHistory(), getStops(),
      ]);

      const trip = tripRes.status === 'fulfilled' ? tripRes.value : null;
      const bus = busRes.status === 'fulfilled' ? busRes.value : null;
      const history = histRes.status === 'fulfilled' ? histRes.value : null;
      const fetchedStops = stopsRes.status === 'fulfilled' ? stopsRes.value : [];

      setTripState(trip);
      setBusPosition(bus);

      if (fetchedStops.length > 0) setStops(fetchedStops);

      if (bus?.lat && bus?.lon) {
        if (bus?.is_live) {
          await animateBusTo(bus.lat, bus.lon);
        } else {
          const c = [bus.lon, bus.lat];
          setAnimatedBus(c);
          updateBusMarker(c, false);
          currentPosRef.current = c;
        }
      }

      // Initial viewport centering
      if (!initialCentered.current && (currentStops.length > 0 || (bus?.lat && bus?.lon))) {
        const busC = (bus?.lon && bus?.lat) ? [bus.lon, bus.lat] : null;
        const vp = getMapViewport(currentStops, busC);
        mapRef.current?.jumpTo({ center: vp.center, zoom: vp.zoom });
        initialCentered.current = true;
      }

      const boardingId = user?.boarding_stop_id;
      if (boardingId && bus?.is_live) {
        getEta(boardingId).then(setEta).catch(() => { });
      }

      // Planned geometry (once)
      if (plannedCoords.length === 0) {
        try {
          const geo = await getRouteGeometry();
          if (geo?.coordinates?.length) setPlannedCoords(geo.coordinates);
        } catch (_) { }
      }

      const trail = history?.coords?.length
        ? history.coords.map(c => [c.lon, c.lat])
        : [];
      setTrailCoords(trail);

      updateSources(
        plannedCoords.length ? plannedCoords : [],
        trail,
        currentStops,
      );

    } finally {
      setLoading(false);
    }
  }, [animateBusTo, plannedCoords, updateBusMarker, updateSources, user?.boarding_stop_id]);

  useEffect(() => {
    fetchAll();
    intervalRef.current = setInterval(fetchAll, POLL_MS);
    return () => {
      clearInterval(intervalRef.current);
      clearInterval(animIntervalRef.current);
    };
  }, [fetchAll]);

  // Update sources when coords change
  useEffect(() => {
    updateSources(plannedCoords, trailCoords, stops);
  }, [plannedCoords, trailCoords, stops, updateSources]);

  // ── Map controls ────────────────────────────────────────────────────────
  const recenter = () => {
    const target = animatedBus || (busPosition?.lon && busPosition?.lat ? [busPosition.lon, busPosition.lat] : null);
    if (target) {
      mapRef.current?.easeTo({ center: target, zoom: 14, duration: 600 });
      cameraLocked.current = true;
      setAutoCenter(true);
    } else if (stops.length) {
      const vp = getMapViewport(stops, null);
      mapRef.current?.easeTo({ center: vp.center, zoom: vp.zoom, duration: 600 });
    }
  };
  const zoomIn = () => mapRef.current?.zoomIn({ duration: 300 });
  const zoomOut = () => mapRef.current?.zoomOut({ duration: 300 });

  const toggle3D = () => {
    if (is3D) {
      mapRef.current?.easeTo({ pitch: 0, bearing: 0, duration: 800 });
      setIs3D(false);
    } else {
      mapRef.current?.easeTo({ pitch: 60, bearing: 45, duration: 1000 });
      setIs3D(true);
    }
  };

  // ── Stop detail card ─────────────────────────────────────────────────────
  const tripName = (typeof tripState?.trip === 'string' ? tripState.trip : '').toLowerCase();
  const direction = tripName.includes('morning') ? 'forward'
    : tripName.includes('evening') ? 'reverse'
      : new Date().getHours() >= 14 ? 'reverse' : 'forward';

  const StopCard = ({ stop }) => {
    const distKm = animatedBus && stop.lat && stop.lon
      ? haversineDistKm(animatedBus[0], animatedBus[1], Number(stop.lon), Number(stop.lat))
      : null;
    const scheduled = direction === 'forward' ? (stop.morning_time || '—') : (stop.evening_time || '—');
    return (
      <div className="stop-card-overlay">
        <div className="stop-card__handle" />
        <button className="stop-card__close" onClick={() => setSelectedStop(null)} aria-label="Close">×</button>
        <div className="stop-card__name">{stop.name}</div>
        <div className="stop-card__row">
          <span className="stop-card__row-icon">🕐</span>
          <div>
            <div className="stop-card__row-label">Scheduled arrival</div>
            <div className="stop-card__row-value">{scheduled}</div>
          </div>
        </div>
        {distKm != null && (
          <div className="stop-card__row">
            <span className="stop-card__row-icon">📍</span>
            <div>
              <div className="stop-card__row-label">Distance from bus</div>
              <div className="stop-card__row-value">
                {distKm < 1 ? `${Math.round(distKm * 1000)}m` : `${distKm.toFixed(1)}km`}
              </div>
            </div>
          </div>
        )}
        {eta?.eta_minutes != null && stop.id === user?.boarding_stop_id && (
          <div className="stop-card__row">
            <span className="stop-card__row-icon">⏱️</span>
            <div>
              <div className="stop-card__row-label">Predicted ETA</div>
              <div className="stop-card__row-value">~{Math.round(eta.eta_minutes)} min</div>
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="map-full" style={{ position: 'absolute', inset: 0 }}>
      <TopBar
        showBack
        onBack={() => navigate('/route', { state: { showLoadingSpinner: true } })}
        title="Live Map"
        onHamburger={() => setDrawerOpen(true)}
      />
      <DrawerMenu isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />

      {/* Map */}
      <div
        ref={mapContainer}
        style={{ position: 'absolute', top: '56px', left: 0, right: 0, bottom: 0 }}
      />

      {loading && (
        <div style={{
          position: 'absolute', top: '56px', inset: 0,
          background: 'rgba(244,245,247,0.85)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12,
        }}>
          <div className="spinner" />
          <span className="spinner-label">Loading map…</span>
        </div>
      )}

      {/* Floating map controls (Unified vertical pill card matching native app) */}
      <div className="map-controls" style={{ bottom: selectedStop ? '240px' : '24px' }}>
        <button
          className={`map-ctrl-btn ${autoCenter ? 'map-ctrl-btn--active' : ''}`}
          onClick={recenter}
          title="Recenter on bus"
          id="map-recenter"
        >
          <Crosshair size={22} color={autoCenter ? '#2563eb' : '#6b7280'} />
        </button>
        <div className="map-ctrl-divider" />
        <button className="map-ctrl-btn" onClick={zoomIn} title="Zoom in" id="map-zoom-in">
          <Plus size={20} color="#1f2937" />
        </button>
        <div className="map-ctrl-divider" />
        <button className="map-ctrl-btn" onClick={zoomOut} title="Zoom out" id="map-zoom-out">
          <Minus size={20} color="#1f2937" />
        </button>
        <div className="map-ctrl-divider" />
        <button
          className="map-ctrl-btn"
          onClick={toggle3D}
          title="Toggle 3D View"
          id="map-3d"
          style={{ fontWeight: '800', fontSize: '13px', color: is3D ? '#2563eb' : '#1f2937' }}
        >
          3D
        </button>
      </div>

      {/* Stop overlay */}
      {selectedStop && <StopCard stop={selectedStop} />}
    </div>
  );
}

function emptyLine() {
  return { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } };
}
function emptyFC() {
  return { type: 'FeatureCollection', features: [] };
}
