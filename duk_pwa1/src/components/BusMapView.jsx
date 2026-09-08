import React, { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import GPSAnimator from '../utils/gpsAnimator';
import TrailManager from '../utils/trailManager';
import { getRouteSegment } from '../api';

const MAP_STYLE     = 'https://tiles.openfreemap.org/styles/liberty';
const TRAIL_COLOR   = '#2563eb';
const PLANNED_COLOR = '#94a3b8';

export default function BusMapView({
  interactive   = true,
  center        = [76.9366, 8.5241],
  zoom          = 12,
  defaultPitch  = 0,
  defaultBearing = 0,
  busCoord      = null,
  isLive        = false,
  headingStatus = null,
  stops         = [],
  plannedCoords = [],
  onStopClick   = null,
  onMapLoad     = null,
  style         = {},
  className     = '',
}) {
  const containerRef  = useRef(null);
  const mapRef        = useRef(null);
  const busMarkerRef  = useRef(null);
  const stopsLayerRef = useRef(false);
  const mapReadyRef   = useRef(false);

  const animatorRef   = useRef(null);
  const trailManagerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container:   containerRef.current,
      style:       MAP_STYLE,
      center,
      zoom,
      pitch:       defaultPitch,
      bearing:     defaultBearing,
      minZoom:     6,
      maxZoom:     18,
      maxBounds: [
        [73.50, 7.50],
        [84.50, 19.50]
      ],
      interactive,
      attributionControl: false,
    });

    mapRef.current = map;
    trailManagerRef.current = new TrailManager();

    map.on('load', () => {
      mapReadyRef.current = true;
      if (trailManagerRef.current) trailManagerRef.current.setMap(map);
      if (onMapLoad) onMapLoad(map);

      map.addSource('planned-route', {
        type: 'geojson',
        data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } },
      });
      map.addLayer({
        id: 'planned-route-layer', type: 'line', source: 'planned-route',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': PLANNED_COLOR, 'line-width': 2.5, 'line-opacity': 0.5 },
      });

      map.addSource('trail', {
        type: 'geojson',
        data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } },
      });
      map.addLayer({
        id: 'trail-layer', type: 'line', source: 'trail',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': TRAIL_COLOR, 'line-width': 3.5, 'line-opacity': 0.9 },
      });

      map.addSource('stops', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      map.addLayer({
        id: 'stops-circle', type: 'circle', source: 'stops',
        paint: {
          'circle-radius': 5, 'circle-color': '#64748b',
          'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff',
        },
      });
      stopsLayerRef.current = true;

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

      updateMapData(map, plannedCoords, stops);
    });

    // Initialize Animator
    animatorRef.current = new GPSAnimator({
      onPositionUpdate: (point, drawnPath) => {
        updateBusMarker(point, true);
        if (trailManagerRef.current && drawnPath) {
          trailManagerRef.current.drawLive(drawnPath);
        }
      },
      onSegmentFinished: (path) => {
        if (trailManagerRef.current) {
          trailManagerRef.current.commitSegment(path);
        }
      },
      segmentFetcher: async (lat1, lon1, lat2, lon2) => {
        const seg = await getRouteSegment(lat1, lon1, lat2, lon2);
        if (seg && seg.coordinates) {
          return seg.coordinates;
        }
        return [];
      }
    });

    const onLiveGps = (e) => {
      const msg = e.detail;
      if (animatorRef.current && msg.lat && msg.lon) {
        animatorRef.current.pushPoint(msg.lat, msg.lon, msg.server_time);
      }
    };
    window.addEventListener('live-gps-update', onLiveGps);

    return () => {
      window.removeEventListener('live-gps-update', onLiveGps);
      mapReadyRef.current = false;
      stopsLayerRef.current = false;
      if (busMarkerRef.current) {
        busMarkerRef.current.remove();
        busMarkerRef.current = null;
      }
      if (animatorRef.current) animatorRef.current.destroy();
      if (trailManagerRef.current) trailManagerRef.current.clear();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  const updateMapData = (map, planned, stopsArr) => {
    if (!map || !mapReadyRef.current) return;
    const plannedSrc = map.getSource('planned-route');
    if (plannedSrc) {
      plannedSrc.setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: planned } });
    }
    const stopsSrc = map.getSource('stops');
    if (stopsSrc) {
      stopsSrc.setData({
        type: 'FeatureCollection',
        features: stopsArr.filter(s => s.lat && s.lon).map(s => ({
          type: 'Feature', geometry: { type: 'Point', coordinates: [Number(s.lon), Number(s.lat)] },
          properties: { stop: JSON.stringify(s) },
        })),
      });
    }
  };

  useEffect(() => {
    updateMapData(mapRef.current, plannedCoords, stops);
  }, [plannedCoords, stops]);

  const updateBusMarker = (coord, live = false) => {
    const map = mapRef.current;
    if (!map) return;
    const iconSrc = live ? '/bus_green.png' : '/bus_gray.png';

    if (!busMarkerRef.current) {
      const el = document.createElement('div');
      el.className = 'bus-marker-wrapper';

      const container = document.createElement('div');
      container.className = 'bus-marker-container';
      const img = document.createElement('img');
      img.src = iconSrc;
      img.className = 'bus-marker-img';
      img.alt = 'Bus';
      container.appendChild(img);
      el.appendChild(container);
      busMarkerRef.current = new maplibregl.Marker({ element: el, anchor: 'bottom' }).setLngLat(coord).addTo(map);
    } else {
      const el = busMarkerRef.current.getElement();
      const img = el?.querySelector('.bus-marker-img');
      if (img && img.getAttribute('src') !== iconSrc) img.src = iconSrc;
      busMarkerRef.current.setLngLat(coord);
    }
  };

  useEffect(() => {
    if (!busCoord) {
      busMarkerRef.current?.remove();
      busMarkerRef.current = null;
      return;
    }
    // Only set it manually if animator hasn't overridden it yet, to initialize
    updateBusMarker(busCoord, isLive);
  }, [busCoord, isLive, headingStatus]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReadyRef.current) return;
    map.easeTo({ center, zoom, duration: 600 });
  }, [center, zoom]);

  return (
    <div style={{ position: 'relative', ...style }} className={className}>
      <div ref={containerRef} className="bus-map" style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }} />
    </div>
  );
}
