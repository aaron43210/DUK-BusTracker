// src/components/MapView.jsx
import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
const HOME = { center: [76.93, 8.56], zoom: 11.2, pitch: 46, bearing: 0 };

// Pre-built empty GeoJSON objects reused across updates — avoids GC churn
const EMPTY_LINE = { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } };
const EMPTY_FC   = { type: 'FeatureCollection', features: [] };

// O(n) — single pass, no conditional reverse copy inside the map
function stopFeatures(stops, isReverse, visitedCount) {
  const total = stops.length;
  return stops.map((s) => {
    // In reverse mode the "forward" index is mirrored
    const fwdIndex = isReverse ? total - 1 - s.order_index : s.order_index;
    let state = 'upcoming';
    if (fwdIndex < visitedCount)      state = 'visited';
    else if (fwdIndex === visitedCount) state = 'next';
    const isTerminal = s.order_index === 0 || s.order_index === total - 1;
    return {
      type: 'Feature',
      properties: {
        name: s.name,
        label: isTerminal
          ? `${s.name}  ·  ${s.order_index === 0 ? 'ORIGIN' : 'DESTINATION'}`
          : s.name,
        state,
        terminal: isTerminal,
      },
      geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
    };
  });
}

// DOM elements created once per marker — no innerHTML parse on every update
function makeBusEl() {
  const el  = document.createElement('div');
  el.className = 'bus-marker';
  const ping = document.createElement('div');
  ping.className = 'bus-ping';
  const orb  = document.createElement('div');
  orb.className  = 'bus-orb';
  orb.innerHTML  =
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
          stroke-linecap="round" stroke-linejoin="round">
       <path d="M5 17a3 3 0 0 0 3 3v2h3v-2h2v2h3v-2a3 3 0 0 0 3-3V8a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v9z"/>
       <path d="M5.5 11h13M5.5 14.5h13"/>
     </svg>`;
  el.appendChild(ping);
  el.appendChild(orb);
  return el;
}

function makePingEl() {
  const el   = document.createElement('div');
  el.className = 'next-stop-ping';
  const ring = document.createElement('div');
  ring.className = 'ring';
  const core = document.createElement('div');
  core.className = 'core';
  el.appendChild(ring);
  el.appendChild(core);
  return el;
}

// Cubic-ease helper — defined once, not recreated per easeTo call
const cubicEase = (t) => 1 - (1 - t) ** 3;

export default function MapView({
  stops, geometry, position, trail, isActive, isReverse, visitedCount,
  recentreTick, onUserPan,
}) {
  const containerRef   = useRef(null);
  const mapRef         = useRef(null);
  const busMarkerRef   = useRef(null);
  const busElRef       = useRef(null);
  const pingMarkerRef  = useRef(null);
  const pingElRef      = useRef(null);
  const followRef      = useRef(true);
  const onUserPanRef   = useRef(onUserPan);
  // Ordered stops cached in a ref — prevents re-reversing on every position tick
  const orderedStopsRef = useRef([]);
  onUserPanRef.current  = onUserPan;

  // Update ordered cache whenever stops or direction changes
  useEffect(() => {
    orderedStopsRef.current = isReverse ? [...stops].reverse() : stops;
  }, [stops, isReverse]);

  /* ── init map once ─────────────────────────────────────────────────── */
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;

    (async () => {
      let style = STYLE_URL;
      try {
        const res = await fetch(STYLE_URL);
        if (!res.ok) throw new Error(`style ${res.status}`);
        style = await res.json();
      } catch { /* use URL fallback */ }
      if (cancelled) return;

      const map = new maplibregl.Map({
        container: containerRef.current,
        style,
        ...HOME,
        antialias: true,
        attributionControl: { compact: true },
      });
      mapRef.current = map;
      map.addControl(
        new maplibregl.NavigationControl({ visualizePitch: true }),
        'bottom-right'
      );

      map.on('load', () => {
        if (cancelled) return;

        // Route line
        map.addSource('route', { type: 'geojson', data: EMPTY_LINE });
        map.addLayer({
          id: 'route-casing', type: 'line', source: 'route',
          paint: { 'line-color': '#FFFFFF', 'line-width': 9, 'line-opacity': 0.95 },
          layout: { 'line-cap': 'round', 'line-join': 'round' },
        });
        map.addLayer({
          id: 'route-line', type: 'line', source: 'route',
          paint: { 'line-color': '#1D4ED8', 'line-width': 4 },
          layout: { 'line-cap': 'round', 'line-join': 'round' },
        });

        // Live trail
        map.addSource('trail', { type: 'geojson', data: EMPTY_LINE });
        map.addLayer({
          id: 'trail-line', type: 'line', source: 'trail',
          paint: { 'line-color': '#3B82F6', 'line-width': 4, 'line-opacity': 0.75 },
          layout: {
            'line-cap': 'round', 'line-join': 'round',
            'line-dasharray': [2.2, 2.4],
          },
        });

        // Stops
        map.addSource('stops-src', { type: 'geojson', data: EMPTY_FC });
        map.addLayer({
          id: 'stops-ring', type: 'circle', source: 'stops-src',
          paint: {
            'circle-radius': ['match', ['get', 'state'], 'next', 7.5, 'visited', 6, 5.5],
            'circle-color':  ['match', ['get', 'state'], 'visited', '#1EA7B4', 'next', '#1D4ED8', '#FFFFFF'],
            'circle-stroke-color': ['match', ['get', 'state'], 'visited', '#FFFFFF', 'next', '#FFFFFF', '#1D4ED8'],
            'circle-stroke-width': ['match', ['get', 'terminal'], true, 3, 2.2],
            'circle-stroke-opacity': 1,
          },
        });
        map.addLayer({
          id: 'stops-label', type: 'symbol', source: 'stops-src',
          layout: {
            'text-field':     ['get', 'label'],
            'text-anchor':    'top',
            'text-offset':    [0, 1.25],
            'text-size':      11,
            'text-optional':  true,
            'text-max-width': 8,
            'text-font':      ['Noto Sans Regular'],
          },
          paint: {
            'text-color':       ['match', ['get', 'state'], 'next', '#1D4ED8', '#0F172A'],
            'text-halo-color':  '#FFFFFF',
            'text-halo-width':  2,
          },
        });
      });

      const panHandler = () => {
        followRef.current = false;
        onUserPanRef.current?.(true);
      };
      ['dragstart', 'wheel', 'touchstart'].forEach((e) => map.on(e, panHandler));
    })();

    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current   = null;
      busMarkerRef.current  = null;
      pingMarkerRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── route geometry ────────────────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !geometry) return;
    const line = geometry.features.find((f) => f.geometry?.type === 'LineString');
    if (line) map.getSource('route')?.setData(line);
  }, [geometry]);

  /* ── stops state ───────────────────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !stops?.length) return;
    const src = map.getSource('stops-src');
    if (!src) return;
    src.setData({
      type: 'FeatureCollection',
      features: stopFeatures(stops, isReverse, visitedCount),
    });
  }, [stops, isReverse, visitedCount]);

  /* ── fit bounds ────────────────────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !stops?.length || !geometry) return;
    const line = geometry.features.find((f) => f.geometry?.type === 'LineString');
    if (!line?.geometry.coordinates.length) return;

    const fit = () => {
      if (!map.isStyleLoaded()) return;
      const c = line.geometry.coordinates;
      const bounds = c.reduce(
        (b, coord) => b.extend(coord),
        new maplibregl.LngLatBounds(c[0], c[0])
      );
      const wide = window.innerWidth > 760;
      map.fitBounds(bounds, {
        padding: isActive
          ? (wide
              ? { top: 96, right: 416, bottom: 130, left: 56 }
              : { top: 96, bottom: 300, left: 56, right: 56 })
          : { top: 96, bottom: 110, left: 56, right: 56 },
        pitch:   HOME.pitch,
        duration: 900,
        maxZoom:  13.4,
      });
    };

    fit();
    const t = setTimeout(fit, 500);
    return () => clearTimeout(t);
  }, [stops, geometry, isActive]);

  /* ── trail ─────────────────────────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !trail?.length) return;
    map.getSource('trail')?.setData({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: trail },
    });
  }, [trail]);

  /* ── live position ──────────────────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (!position?.lat) {
      if (busElRef.current)  busElRef.current.style.display  = 'none';
      if (pingElRef.current) pingElRef.current.style.display = 'none';
      return;
    }

    const lngLat = [position.lon, position.lat];

    // Bus marker — created once
    if (!busMarkerRef.current) {
      busElRef.current = makeBusEl();
      busMarkerRef.current = new maplibregl.Marker({
        element: busElRef.current, anchor: 'center',
      }).setLngLat(lngLat).addTo(map);
    }
    busElRef.current.style.display = '';
    busMarkerRef.current.setLngLat(lngLat).setRotation(position.bearing || 0);

    // Next-stop ping — use cached ordered array (no reverse copy here)
    const ordered = orderedStopsRef.current;
    const total   = ordered.length;
    const nextStop =
      isActive && visitedCount != null && visitedCount < total
        ? ordered[visitedCount]
        : null;

    if (nextStop) {
      if (!pingMarkerRef.current) {
        pingElRef.current = makePingEl();
        pingMarkerRef.current = new maplibregl.Marker({
          element: pingElRef.current, anchor: 'center',
        }).setLngLat([nextStop.lon, nextStop.lat]).addTo(map);
      }
      pingElRef.current.style.display = '';
      pingMarkerRef.current.setLngLat([nextStop.lon, nextStop.lat]);
    } else if (pingElRef.current) {
      pingElRef.current.style.display = 'none';
    }

    if (followRef.current) {
      map.easeTo({ center: lngLat, duration: 1_400, easing: cubicEase });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [position?.lon, position?.lat, position?.bearing, isActive, visitedCount]);

  /* ── recentre ───────────────────────────────────────────────────────── */
  useEffect(() => {
    if (!recentreTick) return;
    const map = mapRef.current;
    if (!map || !position?.lat) return;
    followRef.current = true;
    onUserPanRef.current?.(false);
    map.easeTo({
      center:   [position.lon, position.lat],
      zoom:     Math.max(map.getZoom(), 13.2),
      pitch:    HOME.pitch,
      duration: 1_100,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentreTick]);

  return <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />;
}
