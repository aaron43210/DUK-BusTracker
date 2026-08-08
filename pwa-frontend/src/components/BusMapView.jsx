/**
 * BusMapView.jsx — DUK Bus Tracker PWA
 * Shared MapLibre GL JS map component.
 * Used by RouteView (mini thumbnail) and MapFull (full-screen).
 *
 * Props:
 *   interactive   boolean   — enable pan/zoom gestures
 *   center        [lon, lat]
 *   zoom          number
 *   busCoord      [lon, lat] | null
 *   stops         Array<{ id, name, lat, lon }>
 *   plannedCoords [[lon, lat], ...]
 *   trailCoords   [[lon, lat], ...]
 *   onStopClick   (stop) => void
 *   style         object (container CSS)
 *   className     string
 */
import React, { useEffect, useRef, useCallback } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

const MAP_STYLE     = 'https://tiles.openfreemap.org/styles/liberty';
const TRAIL_COLOR   = '#2563eb';
const PLANNED_COLOR = '#94a3b8';

export default function BusMapView({
  interactive   = true,
  center        = [76.9366, 8.5241],
  zoom          = 12,
  busCoord      = null,
  isLive        = false,
  stops         = [],
  plannedCoords = [],
  trailCoords   = [],
  onStopClick   = null,
  style         = {},
  className     = '',
}) {
  const containerRef  = useRef(null);
  const mapRef        = useRef(null);
  const busMarkerRef  = useRef(null);
  const stopsLayerRef = useRef(false);
  const mapReadyRef   = useRef(false);

  // ── Initialize map ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container:   containerRef.current,
      style:       MAP_STYLE,
      center,
      zoom,
      minZoom:     6,
      maxZoom:     18,
      maxBounds: [
        [73.50, 7.50],  // Southwest: South of Kanyakumari / Lakshadweep Sea
        [84.50, 19.50]  // Northeast: North of Telangana & Andhra Pradesh
      ],
      interactive,
      attributionControl: false,
    });

    mapRef.current = map;

    map.on('load', () => {
      mapReadyRef.current = true;

      // ── Planned route (dashed) ─────────────────────────────────────────
      map.addSource('planned-route', {
        type: 'geojson',
        data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } },
      });
      map.addLayer({
        id: 'planned-route-layer',
        type: 'line',
        source: 'planned-route',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': PLANNED_COLOR,
          'line-width': 2.5,
          'line-opacity': 0.5,
        },
      });

      // ── Bus trail (solid blue #2563eb) ──────────────────────────────────
      map.addSource('trail', {
        type: 'geojson',
        data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } },
      });
      map.addLayer({
        id: 'trail-layer',
        type: 'line',
        source: 'trail',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': TRAIL_COLOR, 'line-width': 3.5, 'line-opacity': 0.9 },
      });

      // ── Stop circles ──────────────────────────────────────────────────
      map.addSource('stops', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      map.addLayer({
        id: 'stops-circle',
        type: 'circle',
        source: 'stops',
        paint: {
          'circle-radius': 5,
          'circle-color': '#64748b',
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#ffffff',
        },
      });
      stopsLayerRef.current = true;

      // Stop click handler
      if (onStopClick) {
        map.on('click', 'stops-circle', (e) => {
          const props = e.features[0]?.properties;
          if (props) onStopClick(JSON.parse(props.stop));
        });
        map.on('mouseenter', 'stops-circle', () => {
          map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', 'stops-circle', () => {
          map.getCanvas().style.cursor = '';
        });
      }

      // Trigger initial data render
      updateMapData(map, plannedCoords, trailCoords, stops);
    });

    return () => {
      mapReadyRef.current  = false;
      stopsLayerRef.current = false;
      map.remove();
      mapRef.current = null;
      busMarkerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Helper: update GeoJSON sources ──────────────────────────────────────
  const updateMapData = (map, planned, trail, stopsArr) => {
    if (!map || !mapReadyRef.current) return;

    // Planned route
    const plannedSrc = map.getSource('planned-route');
    if (plannedSrc) {
      plannedSrc.setData({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: planned },
      });
    }

    // Trail
    const trailSrc = map.getSource('trail');
    if (trailSrc) {
      trailSrc.setData({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: trail },
      });
    }

    // Stops
    const stopsSrc = map.getSource('stops');
    if (stopsSrc) {
      stopsSrc.setData({
        type: 'FeatureCollection',
        features: stopsArr
          .filter(s => s.lat && s.lon)
          .map(s => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [Number(s.lon), Number(s.lat)] },
            properties: { stop: JSON.stringify(s) },
          })),
      });
    }
  };

  // ── Sync planned route, trail, stops when props change ──────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;
    updateMapData(map, plannedCoords, trailCoords, stops);
  }, [plannedCoords, trailCoords, stops]);

  // ── Sync bus marker ──────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (!busCoord) {
      busMarkerRef.current?.remove();
      busMarkerRef.current = null;
      return;
    }

    const iconSrc = isLive ? '/bus_green.png' : '/bus_gray.png';

    if (!busMarkerRef.current) {
      const el = document.createElement('div');
      el.className = 'bus-marker-container';
      el.innerHTML = `<img src="${iconSrc}" class="bus-marker-img" alt="Bus" />`;
      busMarkerRef.current = new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat(busCoord)
        .addTo(map);
    } else {
      const img = busMarkerRef.current.getElement()?.querySelector('.bus-marker-img');
      if (img && img.getAttribute('src') !== iconSrc) {
        img.src = iconSrc;
      }
      busMarkerRef.current.setLngLat(busCoord);
    }
  }, [busCoord, isLive]);

  // ── Sync center/zoom (for mini-map controlled mode) ──────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;
    map.easeTo({ center, zoom, duration: 600 });
  }, [center, zoom]);

  return (
    <div
      ref={containerRef}
      className={`bus-map ${className}`}
      style={style}
    />
  );
}
