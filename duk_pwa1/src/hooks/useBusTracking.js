import { useState, useEffect, useRef } from 'react';
import { useAppContext } from '../context';
import {
  getTripState, getLatestGps, getRouteHistory, getStops, getEta, getAllEtas, getRouteGeometry, getWsBusUrl
} from '../api';
import { getUser } from '../storage';

// Polling intervals (milliseconds).
// Heavy poll: runs on mount and when the bus is NOT live.
// Light poll: runs when the bus IS live (WebSocket carries real-time data;
//             HTTP poll only needed for trip state / ETA updates).
const HEAVY_POLL_MS = 15000;  // 15 s  — full refresh when bus is offline
const LIGHT_POLL_MS = 30000;  // 30 s  — reduced when WS is providing GPS data

// Module-level route geometry cache: fetched once per app session.
// Stored outside React state so it survives component unmounts.
let _cachedRouteGeometry = null;

export default function useBusTracking() {
  const { state, dispatch } = useAppContext();
  const [nextStop, setNextStop] = useState(null);
  const [nextTripTime, setNextTripTime] = useState(null);
  const [locationName, setLocationName] = useState(null);
  const wsRef = useRef(null);
  const intervalRef = useRef(null);
  const isFetchingRef = useRef(false);
  const isLiveRef = useRef(false); // tracks WS liveness without triggering re-renders

  // Core API Polling
  const fetchAll = async () => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;

    try {
      let trip = null;
      try {
        trip = await getTripState();
      } catch (_) {}

      const [busRes, histRes, stopsRes] = await Promise.allSettled([
        getLatestGps(),
        getRouteHistory(trip?.trip_id || null),
        getStops(),
      ]);

      const bus = busRes.status === 'fulfilled' ? busRes.value : null;
      const history = histRes.status === 'fulfilled' ? histRes.value : null;
      const fetchedStops = stopsRes.status === 'fulfilled' ? stopsRes.value : [];

      const updates = {};
      updates.tripState = trip;

      if (bus?.is_live) {
        updates.busPosition = bus;
        updates.isLive = true;
      } else if (bus) {
        updates.busPosition = bus;
      }

      if (bus?.location_name) {
        setLocationName(bus.location_name);
      }

      updates.history = history;

      if (fetchedStops.length > 0) {
        updates.stops = fetchedStops;
      }

      // Dynamic ETA Target
      const user = getUser();
      if (bus?.is_live) {
        const isEvening = trip?.trip?.toLowerCase()?.includes('evening');
        const primaryTargetId = isEvening ? user?.destination_stop_id : user?.boarding_stop_id;

        let finalEtaRes = null;
        let usedTargetId = primaryTargetId;

        const [etaTargetRes, allEtasRes] = await Promise.allSettled([
          primaryTargetId ? getEta(primaryTargetId) : Promise.resolve(null),
          getAllEtas()
        ]);

        if (etaTargetRes.status === 'fulfilled' && etaTargetRes.value) {
          finalEtaRes = etaTargetRes.value;
        }

        if (allEtasRes.status === 'fulfilled' && allEtasRes.value) {
          updates.mlEtas = allEtasRes.value;
        } else {
          updates.mlEtas = null;
        }

        if (!finalEtaRes || finalEtaRes.status === 'passed' || finalEtaRes.status === 'deviated') {
          if (fetchedStops && fetchedStops.length > 0) {
            const orderedStops = isEvening ? [...fetchedStops].reverse() : fetchedStops;
            const finalStop = orderedStops[orderedStops.length - 1];
            if (finalStop && finalStop.id !== primaryTargetId) {
              try {
                finalEtaRes = await getEta(finalStop.id);
                usedTargetId = finalStop.id;
              } catch (_) {}
            }
          }
        }

        updates.eta = finalEtaRes;
        updates.etaTargetStopId = usedTargetId;
      } else {
        updates.eta = null;
        updates.etaTargetStopId = null;
        updates.mlEtas = null;
      }

      // Route Geometry — fetch once per session, cache at module level.
      // Avoids re-fetching the full route GeoJSON on every poll cycle.
      if (!state.plannedCoords?.length || state.plannedCoords.length < 100) {
        if (_cachedRouteGeometry?.length) {
          updates.plannedCoords = _cachedRouteGeometry;
        } else {
          try {
            const geo = await getRouteGeometry();
            if (geo?.coordinates?.length) {
              _cachedRouteGeometry = geo.coordinates;
              updates.plannedCoords = _cachedRouteGeometry;
            }
          } catch (_) {}
        }
      }

      dispatch({ type: 'BATCH_UPDATE', payload: updates });

    } catch (err) {
      console.error('API Fetch failed', err);
    } finally {
      isFetchingRef.current = false;
    }
  };

  // Reschedule the polling interval based on current liveness.
  // When the bus is live, WebSocket handles GPS — we poll less aggressively.
  const _rescheduleInterval = (live) => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    const ms = live ? LIGHT_POLL_MS : HEAVY_POLL_MS;
    intervalRef.current = setInterval(fetchAll, ms);
  };

  // Setup Polling and WebSockets
  useEffect(() => {
    fetchAll(); // Initial fetch
    _rescheduleInterval(false);

    let isMounted = true;
    let reconnectTimeout = null;

    const connectWs = () => {
      const ws = new WebSocket(getWsBusUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        // Switch to light polling — WS now handles GPS updates
        if (isMounted && !isLiveRef.current) {
          isLiveRef.current = true;
          _rescheduleInterval(true);
        }
      };

      ws.onmessage = (event) => {
        if (!isMounted) return;
        try {
          const msg = JSON.parse(event.data);
          if (Number.isFinite(msg.lat) && Number.isFinite(msg.lon)) {
            if (msg.location_name) {
              setLocationName(msg.location_name);
            }
            // Update context state
            dispatch({
              type: 'BATCH_UPDATE',
              payload: {
                busPosition: { lat: msg.lat, lon: msg.lon, speed_kmh: msg.speed_kmh, server_time: msg.server_time, is_live: true },
                isLive: true
              }
            });
            
            // Dispatch native event for Animator if it exists
            window.dispatchEvent(new CustomEvent('live-gps-update', { detail: msg }));
          }
        } catch (e) {}
      };

      ws.onclose = () => {
        if (isMounted) {
          // Drop back to heavy polling when WS disconnects
          if (isLiveRef.current) {
            isLiveRef.current = false;
            _rescheduleInterval(false);
          }
          reconnectTimeout = setTimeout(connectWs, 2000);
        }
      };
    };

    connectWs();

    return () => {
      isMounted = false;
      clearInterval(intervalRef.current);
      if (wsRef.current) wsRef.current.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, []); // Run once on mount

  return { fetchAll, locationName };
}
