import React, { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import GPSAnimator from '../utils/gpsAnimator';
import TrailManager, { SNAP_THRESHOLD_M } from '../utils/trailManager';
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
  isActiveTrip  = false,
  autoCenter    = false,
  headingStatus = null,
  stops         = [],
  plannedCoords = [],
  routeHistoryCoords = [],
  onStopClick   = null,
  onMapLoad     = null,
  onAutoCenterChange = null, // notify parent when a user gesture turns off follow
  style         = {},
  className     = '',
}) {
  const containerRef  = useRef(null);
  const mapRef        = useRef(null);
  const busMarkerRef  = useRef(null);
  const markerLiveRef = useRef(null); // current icon state on the marker (avoids per-frame innerHTML rewrites)
  const stopsLayerRef = useRef(false);
  const mapReadyRef   = useRef(false);

  const animatorRef     = useRef(null);
  const trailManagerRef = useRef(null);
  const isActiveTripRef = useRef(false); // ref so animator callbacks always read the latest value
  // Refs so the (mount-time) animator callback always sees the latest props: while
  // live and following, we recenter the map on the *animated* position each frame
  // so the map glides with the marker instead of stepping to each raw GPS point.
  const isLiveRef       = useRef(false);
  const autoCenterRef   = useRef(false);
  // True only while the user is actively dragging/zooming, so the per-frame
  // follow never fights their gesture (the "map is stuck / not movable" bug).
  const userInteractingRef = useRef(false);
  const onAutoCenterChangeRef = useRef(onAutoCenterChange);
  useEffect(() => { onAutoCenterChangeRef.current = onAutoCenterChange; }, [onAutoCenterChange]);

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

    // A user-initiated pan/zoom/rotate carries an `originalEvent` (programmatic
    // moves like our follow-setCenter do not). On such a gesture we release
    // auto-follow so the map stays where the user put it, and tell the parent so
    // the crosshair button reflects the change. We also suppress the per-frame
    // follow for the duration of the gesture.
    const onUserGestureStart = (e) => {
      if (!e || !e.originalEvent) return;
      userInteractingRef.current = true;
      if (autoCenterRef.current) {
        autoCenterRef.current = false;
        if (onAutoCenterChangeRef.current) onAutoCenterChangeRef.current(false);
      }
    };
    const onUserGestureEnd = (e) => {
      if (e && e.originalEvent) userInteractingRef.current = false;
    };
    map.on('dragstart', onUserGestureStart);
    map.on('zoomstart', onUserGestureStart);
    map.on('rotatestart', onUserGestureStart);
    map.on('dragend', onUserGestureEnd);
    map.on('zoomend', onUserGestureEnd);
    map.on('rotateend', onUserGestureEnd);

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

      updateMapData(map, plannedCoords, stops, routeHistoryCoords);
    });

    // Initialize Animator with smart snap-to-route segment fetcher.
    // If the bus is within SNAP_THRESHOLD_M of the planned route → instant slice.
    // If deviated or unscheduled → call OSRM for the actual road shape.
    animatorRef.current = new GPSAnimator({
      onPositionUpdate: (point, drawnPath) => {
        updateBusMarker(point, true);
        // Follow the animated point (smooth) rather than the raw GPS ping — but
        // never while the user is actively panning/zooming the map.
        if (
          autoCenterRef.current &&
          isLiveRef.current &&
          !userInteractingRef.current &&
          mapRef.current
        ) {
          mapRef.current.setCenter(point);
        }
        if (trailManagerRef.current && drawnPath && isActiveTripRef.current) {
          trailManagerRef.current.drawLive(drawnPath);
        }
      },
      onSegmentFinished: (path) => {
        if (trailManagerRef.current && isActiveTripRef.current) {
          trailManagerRef.current.commitSegment(path);
        }
      },
      segmentFetcher: async (lat1, lon1, lat2, lon2) => {
        const trail = trailManagerRef.current;
        const hasPlanned = trail && trail._plannedCoords && trail._plannedCoords.length > 0;

        if (hasPlanned) {
          // Try snapping both endpoints to the planned route
          const snap1 = trail.findNearestOnRoute(lon1, lat1);
          const snap2 = trail.findNearestOnRoute(lon2, lat2);

          if (
            snap1 && snap2 &&
            snap1.distM <= SNAP_THRESHOLD_M &&
            snap2.distM <= SNAP_THRESHOLD_M
          ) {
            // Bus is on the known route — return planned route slice instantly (no OSRM).
            // Only treat as genuinely backward if the snap index clearly regressed by
            // >= 3 waypoints — smaller diffs are GPS jitter at a stop and reversing
            // them is what makes the marker briefly "back up" along the route.
            const idxDiff = snap1.idx - snap2.idx;
            const isBackward = idxDiff >= 3;
            const from = Math.min(snap1.idx, snap2.idx);
            const to   = Math.max(snap1.idx, snap2.idx);
            let slice = trail._plannedCoords.slice(from, to + 1);
            
            if (isBackward) {
              slice.reverse();
            }
            
            if (slice.length >= 2) return slice;
          }
          // Deviated beyond threshold — fall through to OSRM
        }

        // Unscheduled trip or deviated > 300m: use OSRM for accurate road shape
        const seg = await getRouteSegment(lat1, lon1, lat2, lon2);
        return seg?.coordinates || [];
      },
    });

    const onLiveGps = (e) => {
      const msg = e.detail;
      if (animatorRef.current && msg.lat && msg.lon) {
        animatorRef.current.pushPoint(msg.lat, msg.lon, msg.server_time, msg.osrm_path);
      }
    };
    window.addEventListener('live-gps-update', onLiveGps);

    // When useBusTracking fetches planned coords, wire them into TrailManager
    // so the smart segmentFetcher can snap to them.
    const onPlannedRoute = (e) => {
      if (trailManagerRef.current) {
        trailManagerRef.current.setPlannedRoute(e.detail);
      }
    };
    window.addEventListener('planned-route-ready', onPlannedRoute);

    return () => {
      window.removeEventListener('live-gps-update', onLiveGps);
      window.removeEventListener('planned-route-ready', onPlannedRoute);
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

  const updateMapData = (map, planned, stopsArr, historyCoords) => {
    if (!map || !mapReadyRef.current) return;
    const plannedSrc = map.getSource('planned-route');
    if (plannedSrc && planned && planned.length > 0) {
      const isMulti = Array.isArray(planned[0]) && Array.isArray(planned[0][0]);
      plannedSrc.setData({ 
        type: 'Feature', 
        geometry: { 
          type: isMulti ? 'MultiLineString' : 'LineString', 
          coordinates: planned 
        } 
      });
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

    if (historyCoords && historyCoords.length > 0 && trailManagerRef.current) {
      // Guard: only reseed from history if it has at least as many points as the
      // current live trail. The route_history API is cached 30 s in Redis, so a
      // poll response can arrive slightly behind the live segments commitSegment()
      // just appended — blindly calling setHistory() would shrink the visible trail.
      // When historyCoords is longer (or equal), it's safe to reseed because the
      // DB has caught up and the history is more authoritative.
      const currentLen = trailManagerRef.current.trailCoords.length;
      if (historyCoords.length >= currentLen) {
        trailManagerRef.current.setHistory(historyCoords);
      }
    }
  };

  useEffect(() => {
    updateMapData(mapRef.current, plannedCoords, stops, routeHistoryCoords);
  }, [plannedCoords, stops, routeHistoryCoords]);

  // Rewrites the marker icon HTML only when the live/offline state actually changes.
  // During animation this is called ~60×/s with the same `live` value, so guarding
  // the innerHTML write keeps the DOM identical while avoiding needless reflows.
  const setMarkerIcon = (live) => {
    if (!busMarkerRef.current || markerLiveRef.current === live) return;
    markerLiveRef.current = live;
    const imgSrc = live ? `${import.meta.env.BASE_URL}bus_live.webp` : `${import.meta.env.BASE_URL}bus_offline.webp`;
    busMarkerRef.current.getElement().innerHTML = `
      <div class="marker-wrapper" style="position: relative; display: flex; align-items: center; justify-content: center; width: 48px; height: 48px;">
        <img src="${imgSrc}" style="width: 32px; height: 32px; object-fit: contain; z-index: 2;" alt="Bus" />
      </div>
    `;
  };

  const updateBusMarker = (coord, live = false) => {
    const map = mapRef.current;
    if (!map) return;

    if (!busMarkerRef.current) {
      const el = document.createElement('div');
      el.className = 'bus-marker-wrapper';
      busMarkerRef.current = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(coord).addTo(map);
      markerLiveRef.current = null;
      setMarkerIcon(live);
    } else {
      setMarkerIcon(live);
      busMarkerRef.current.setLngLat(coord);
    }
  };

  useEffect(() => {
    if (!busCoord) {
      busMarkerRef.current?.remove();
      busMarkerRef.current = null;
      markerLiveRef.current = null;
      return;
    }
    // While live, GPSAnimator owns marker motion (smooth rAF interpolation via
    // onPositionUpdate). Snapping to the raw busCoord here would fight the animator
    // and teleport the marker on every ping.
    //
    // Extra guard: if the animator already has a tracked position (currentPos != null)
    // we must NEVER override it with the raw GPS coord — even if isLive hasn't
    // propagated to true yet in this render cycle (React batching edge case). This
    // prevents the "jump forward then snap backward" glitch on every WS/poll cycle.
    const animatorHasPosition = animatorRef.current?.currentPos != null;
    if (!busMarkerRef.current) {
      // First appearance — place marker at initial position
      updateBusMarker(busCoord, isLive);
    } else if (!isLive && !animatorHasPosition) {
      // Genuinely offline and no animator running — direct position update
      updateBusMarker(busCoord, isLive);
    } else {
      // Live OR animator already has a position — only update the icon
      setMarkerIcon(isLive);
    }
  }, [busCoord, isLive, headingStatus]);

  // Sync isActiveTrip prop into a ref so animator callbacks always read the latest value
  useEffect(() => {
    isActiveTripRef.current = isActiveTrip;
    // When trip goes inactive, clear any live trail segments that were drawn
    if (!isActiveTrip && trailManagerRef.current) {
      trailManagerRef.current.clear();
    }
  }, [isActiveTrip]);

  // Keep the live/follow refs current for the mount-time animator callback.
  useEffect(() => { isLiveRef.current = isLive; }, [isLive]);
  useEffect(() => { autoCenterRef.current = autoCenter; }, [autoCenter]);

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
