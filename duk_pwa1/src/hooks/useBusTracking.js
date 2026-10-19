import { useState, useEffect, useRef } from 'react';
import { useAppContext } from '../context';
import {
  getTripState, getLatestGps, getRouteHistory, getStops, getEta, getAllEtas, getRouteGeometry, getWsBusUrl, getWsBusProtocols, refreshSession
} from '../api';
import { getUser, getRefreshToken } from '../storage';
import { haversineDistKm } from '../timetable';

// Polling intervals (milliseconds).
// Heavy poll: runs on mount and when the bus is NOT live.
// Light poll: runs when the bus IS live (WebSocket carries real-time data;
//             HTTP poll only needed for trip state / ETA updates).
const HEAVY_POLL_MS = 15000;  // 15 s  — full refresh when bus is offline
const LIGHT_POLL_MS = 30000;  // 30 s  — reduced when WS is providing GPS data

// Module-level route geometry cache: fetched once per app session.
// Stored outside React state so it survives component unmounts.
let _cachedRouteGeometry = {};

function getProximityLocationName(lon, lat, defaultName, stops) {
  if (!stops || stops.length === 0) return defaultName;
  let closestStop = null;
  let minDist = Infinity;
  for (const stop of stops) {
    const dist = haversineDistKm(lon, lat, Number(stop.lon), Number(stop.lat));
    if (dist < minDist) {
      minDist = dist;
      closestStop = stop;
    }
  }
  return (minDist <= 0.2 && closestStop) ? closestStop.name : defaultName;
}

export default function useBusTracking() {
  const { state, dispatch } = useAppContext();

  const [locationName, setLocationName] = useState(null);
  const wsRef = useRef(null);
  const intervalRef = useRef(null);
  // Counts consecutive expired-token (4003) refresh attempts so a refresh↔reject
  // loop can't hammer the server; reset once a connection opens or closes cleanly.
  const wsRefreshAttemptsRef = useRef(0);
  const isFetchingRef = useRef(false);
  const isLiveRef = useRef(false); // tracks WS liveness without triggering re-renders
  const stopsRef = useRef([]);

  // Core API Polling
  const fetchAll = async () => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;

    try {
      let trip = null;
      try {
        trip = await getTripState();
      } catch (_) { }

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
        // Only override isLive→false from the HTTP poll when the WebSocket is
        // genuinely disconnected. If the WS is up, the backend's is_live flag
        // can lag behind the real-time stream and would incorrectly snap the
        // marker back to an older GPS position on every poll cycle.
        if (!isLiveRef.current) {
          updates.isLive = false;
        }
      }

      if (fetchedStops.length > 0) {
        updates.stops = fetchedStops;
        stopsRef.current = fetchedStops;
      }

      let newLocationName = bus?.location_name || null;
      if (bus?.lat && bus?.lon && fetchedStops.length > 0) {
        newLocationName = getProximityLocationName(bus.lon, bus.lat, newLocationName, fetchedStops);
      }
      if (newLocationName) {
        setLocationName(newLocationName);
      }

      updates.history = history;

      // Dynamic ETA Target
      const user = getUser();
      // Only fetch ETAs when we have a genuinely LIVE bus on a running trip.
      // 'connecting' means the window is open but the bus has not reported live
      // GPS yet — computing ETAs off the stale last-known position produced
      // fake "delayed/early" times before the bus had departed.
      const isActiveTrip = trip
        && ['active', 'on_trip', 'late'].includes(trip.status)
        && bus?.is_live === true;

      if (bus && isActiveTrip) {
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
            const orderedStops = isEvening 
              ? [...fetchedStops].sort((a, b) => (a.evening_order_index || 0) - (b.evening_order_index || 0))
              : [...fetchedStops].sort((a, b) => (a.morning_order_index || 0) - (b.morning_order_index || 0));
            const finalStop = orderedStops[orderedStops.length - 1];
            if (finalStop && finalStop.id !== primaryTargetId) {
              try {
                finalEtaRes = await getEta(finalStop.id);
                usedTargetId = finalStop.id;
              } catch (_) { }
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

      // Route Geometry — fetch once per trip, cache at module level.
      const currentTripId = trip?.trip_id;
      if (currentTripId) {
        if (!_cachedRouteGeometry[currentTripId]) {
          try {
            const geo = await getRouteGeometry(currentTripId, trip.direction);
            if (geo?.coordinates?.length) {
              _cachedRouteGeometry[currentTripId] = geo.coordinates;
              updates.plannedCoords = geo.coordinates;
              // Notify BusMapView's TrailManager so it can snap-to-route instantly
              window.dispatchEvent(new CustomEvent('planned-route-ready', { detail: geo.coordinates }));
            }
          } catch (_) { }
        } else if (state.plannedCoords !== _cachedRouteGeometry[currentTripId]) {
          updates.plannedCoords = _cachedRouteGeometry[currentTripId];
          // Re-emit in case BusMapView remounted and lost the previous event
          window.dispatchEvent(new CustomEvent('planned-route-ready', { detail: _cachedRouteGeometry[currentTripId] }));
        }
      } else {
        updates.plannedCoords = [];
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
      // Carry the JWT in the Sec-WebSocket-Protocol subprotocol (["bearer","<jwt>"])
      // rather than the URL query string, keeping it out of logs/history. The
      // server authenticates the handshake from it (or a session cookie).
      const protocols = getWsBusProtocols();
      const ws = protocols
        ? new WebSocket(getWsBusUrl(), protocols)
        : new WebSocket(getWsBusUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        wsRefreshAttemptsRef.current = 0;  // healthy connection — clear refresh guard

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
            let newLocationName = msg.location_name || null;
            if (stopsRef.current && stopsRef.current.length > 0) {
              newLocationName = getProximityLocationName(msg.lon, msg.lat, newLocationName, stopsRef.current);
            }
            if (newLocationName) {
              setLocationName(newLocationName);
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
        } catch (e) { }
      };

      ws.onclose = (event) => {
        if (event.code === 4001) {
          // 4001: the token's JTI is revoked (explicit logout / server-side
          // revocation). This is a genuine "you are no longer authorized" — send
          // the user to login and stop reconnecting.
          window.dispatchEvent(new CustomEvent('api:unauthorized'));
          return;
        }

        if (event.code === 4008) {
          // 4008: server-side infrastructure error (Redis unavailable). This is
          // NOT a session revocation — reconnect normally after a short backoff.
          if (isMounted) {
            if (isLiveRef.current) { isLiveRef.current = false; _rescheduleInterval(false); }
            reconnectTimeout = setTimeout(connectWs, 3000);
          }
          return;
        }

        if (event.code === 4003) {
          // 4003: the server could not decode the token — almost always just an
          // EXPIRED access token (JWT past its exp), NOT a revoked session. The
          // HTTP layer silently refreshes in this case; the WS must do the same so
          // a user with a valid 365-day refresh token is never bounced to login.
          // Only if the refresh itself fails (refresh token also rejected) do we
          // treat it as unauthorized.
          if (!isMounted) return;
          if (isLiveRef.current) {
            isLiveRef.current = false;
            _rescheduleInterval(false);
          }
          if (wsRefreshAttemptsRef.current >= 3) {
            // Guard against a tight refresh↔4003 loop (e.g. clock skew).
            window.dispatchEvent(new CustomEvent('api:unauthorized'));
            return;
          }
          wsRefreshAttemptsRef.current += 1;
          refreshSession()
            .then((ok) => {
              if (!isMounted) return;
              if (ok) {
                // New access token is in storage — getWsBusUrl() will pick it up.
                reconnectTimeout = setTimeout(connectWs, 500);
              } else {
                // refreshSession returns false for BOTH auth failures AND transient
                // network errors. Only logout if we've exhausted retries AND there
                // is no refresh token left (meaning _silentRefresh cleared it on
                // an explicit 401/403).
                if (!getRefreshToken()) {
                  // Token was explicitly cleared — real auth failure
                  window.dispatchEvent(new CustomEvent('api:unauthorized'));
                } else {
                  // Transient — still have refresh token, back off and retry WS
                  reconnectTimeout = setTimeout(connectWs, 3000);
                }
              }
            })
            .catch(() => {
              // Unexpected error — retry WS connection, don't logout
              if (isMounted) reconnectTimeout = setTimeout(connectWs, 3000);
            });
          return;
        }

        if (isMounted) {
          // Normal disconnect (network blip, server restart) — a healthy reconnect
          // succeeded at least once, so reset the refresh guard.
          wsRefreshAttemptsRef.current = 0;
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
