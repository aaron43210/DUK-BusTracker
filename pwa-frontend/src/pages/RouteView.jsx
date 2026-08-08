/**
 * RouteView.jsx — DUK Bus Tracker PWA
 * Home screen: live trip status, stop timeline, stats, mini-map.
 * Mirrors RouteViewScreen.tsx from the React Native app exactly.
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Expand } from 'lucide-react';
import TopBar             from '../components/TopBar';
import DrawerMenu         from '../components/DrawerMenu';
import NotificationDrawer from '../components/NotificationDrawer';
import BusMapView         from '../components/BusMapView';
import {
  getLatestGps, getTripState, getRouteHistory, getEta, getRouteGeometry, getRouteSegment,
} from '../api';
import { getUser } from '../storage';
import {
  MORNING_SCHEDULE, EVENING_SCHEDULE,
  getDelayBadge, computeEstimatedTime, getMapViewport,
  haversineDistKm, todayStr,
} from '../timetable';

const POLL_MS = 2500;

export default function RouteView() {
  const navigate = useNavigate();

  const [drawerOpen,       setDrawerOpen]       = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [user,             setUser]             = useState(null);
  const [tripState,     setTripState]     = useState(null);
  const [busPosition,   setBusPosition]   = useState(null);
  const [routeHistory,  setRouteHistory]  = useState(null);
  const [eta,           setEta]           = useState(null);
  const [stops,         setStops]         = useState([]);
  const [plannedCoords, setPlannedCoords] = useState([]);
  const [trailCoords,   setTrailCoords]   = useState([]);
  const [animatedBus,   setAnimatedBus]   = useState(null);
  const [loading,       setLoading]       = useState(true);
  const [refreshing,    setRefreshing]    = useState(false);
  const [error,         setError]         = useState('');

  const intervalRef    = useRef(null);
  const animIntervalRef = useRef(null);
  const currentPosRef  = useRef(null);

  // Load user
  useEffect(() => {
    setUser(getUser());
  }, []);

  // Animate bus smoothly between positions
  const animateBusTo = useCallback(async (targetLat, targetLon) => {
    const startPos = currentPosRef.current || [targetLon, targetLat];
    const [startLon, startLat] = startPos;
    const distKm = haversineDistKm(startLon, startLat, targetLon, targetLat);

    if (!currentPosRef.current || distKm > 8.0) {
      setAnimatedBus([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      return;
    }
    if (distKm < 0.0005) {
      setAnimatedBus([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      return;
    }

    let polyline = [[startLon, startLat], [targetLon, targetLat]];
    try {
      const seg = await getRouteSegment(startLat, startLon, targetLat, targetLon);
      if (seg?.coordinates?.length > 1) polyline = seg.coordinates;
    } catch (_) {}

    if (animIntervalRef.current) clearInterval(animIntervalRef.current);
    const steps = Math.min(polyline.length, 12);
    const chunk = Math.max(1, Math.floor(polyline.length / steps));
    let idx = 0;
    animIntervalRef.current = setInterval(() => {
      if (idx >= polyline.length) {
        clearInterval(animIntervalRef.current);
        currentPosRef.current = [targetLon, targetLat];
        return;
      }
      setAnimatedBus([polyline[idx][0], polyline[idx][1]]);
      idx += chunk;
    }, Math.floor(POLL_MS / steps));
  }, []);

  const fetchAll = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setError('');

    try {
      const [tripRes, busRes, histRes] = await Promise.allSettled([
        getTripState(),
        getLatestGps(),
        getRouteHistory(),
      ]);

      const trip    = tripRes.status === 'fulfilled'    ? tripRes.value    : null;
      const bus     = busRes.status === 'fulfilled'     ? busRes.value     : null;
      const history = histRes.status === 'fulfilled'    ? histRes.value    : null;

      setTripState(trip);
      setBusPosition(bus);
      setRouteHistory(history);

      if (bus?.lat && bus?.lon) {
        await animateBusTo(bus.lat, bus.lon);
      }

      // Load stops list from trip data
      if (trip?.stops) {
        setStops(trip.stops);
      }

      // ETA to boarding stop
      const boardingStopId = getUser()?.boarding_stop_id;
      if (boardingStopId && bus?.is_live) {
        try {
          const etaRes = await getEta(boardingStopId);
          setEta(etaRes);
        } catch (_) {}
      }

      // Planned route geometry (only once)
      if (plannedCoords.length === 0) {
        try {
          const geo = await getRouteGeometry();
          if (geo?.coordinates?.length) setPlannedCoords(geo.coordinates);
        } catch (_) {}
      }

      // Trail from route history
      if (history?.coords?.length) {
        setTrailCoords(history.coords.map(c => [c.lon, c.lat]));
      }

    } catch (err) {
      setError('Unable to load data. Check your connection.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [animateBusTo, plannedCoords.length]);

  // Initial load + polling
  useEffect(() => {
    fetchAll(true);
    intervalRef.current = setInterval(() => fetchAll(false), POLL_MS);
    return () => {
      clearInterval(intervalRef.current);
      clearInterval(animIntervalRef.current);
    };
  }, [fetchAll]);

  // ── Derived data ─────────────────────────────────────────────────────────
  const direction = tripState?.trip?.direction || (new Date().getHours() >= 14 ? 'reverse' : 'forward');
  const schedule = direction === 'forward' ? MORNING_SCHEDULE : EVENING_SCHEDULE;
  const lateMins = tripState?.trip?.late_by_minutes ?? null;
  
  const tripStatus = tripState?.status ?? 'idle'; // 'active' | 'idle' | 'waiting' | 'cancelled'
  const isActive   = tripStatus === 'active';
  const busIsLive  = busPosition?.is_live === true;
  const isOnline   = isActive;

  // Build timeline from stops + visit history
  const visitedStops  = routeHistory?.visitedStops  || {};
  const arrivalTimes  = routeHistory?.arrivalTimes  || {};

  const stopsToShow = stops.length ? stops : Object.keys(schedule).map((name, i) => ({
    id: i + 1, name, lat: null, lon: null, order_index: i,
  }));

  // Find "current next stop" = first unvisited stop
  const currentNextIdx = stopsToShow.findIndex(s => !visitedStops[s.name]);
  const visitedCount   = Object.keys(visitedStops).length;

  // Viewport for mini-map
  const mapVp = getMapViewport(stops, animatedBus);

  // Delay badge
  const delayBadge = getDelayBadge(null, null, lateMins);

  // Stats text
  const etaText = eta?.eta_minutes != null ? `${Math.round(eta.eta_minutes)} min` : '—';
  const speedText = busPosition?.speed != null ? `${Math.round(busPosition.speed)} km/h` : '—';
  const stopsDoneText = `${visitedCount}/${stopsToShow.length}`;
  
  // Format last updated time
  const formatLastUpdated = (isoStr) => {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + ', ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  };

  // Format current status subtext
  const getSubtextStatus = () => {
    const rawTrip = typeof tripState?.trip === 'string' ? tripState.trip : (tripState?.trip?.direction || '');
    const status = tripState?.status ?? 'offline';
    const nextTripTime = tripState?.next_trip_time;
    const tripLower = (rawTrip || '').toLowerCase();

    // 1. Cancelled Trip
    if (status === 'cancelled') {
      return (
        <span style={{ color: '#ef4444', fontWeight: 600 }}>
          Trip Cancelled {tripState?.cancellation_reason ? `(${tripState.cancellation_reason})` : ''}
        </span>
      );
    }

    // 2. Connecting to bus GPS
    if (status === 'connecting') {
      return (
        <span style={{ color: '#d97706', fontWeight: 600 }}>
          Connecting to Bus GPS…
        </span>
      );
    }

    // 3. Special Service
    if (tripLower.includes('special')) {
      return (
        <span>
          <span style={{ color: '#8b5cf6', fontWeight: 700 }}>Special Service</span> → {direction === 'forward' ? 'Digital University Kerala' : 'Central Polytechnic'}
        </span>
      );
    }

    // 4. Morning Active Trip
    if (tripLower.includes('morning') || tripLower === 'forward') {
      return (
        <span>
          <span style={{ color: 'var(--mint-deeper, #059669)', fontWeight: 700 }}>Morning Trip</span> → Digital University Kerala
        </span>
      );
    }

    // 5. Evening Active Trip
    if (tripLower.includes('evening') || tripLower === 'reverse') {
      return (
        <span>
          <span style={{ color: 'var(--mint-deeper, #059669)', fontWeight: 700 }}>Evening Trip</span> → Central Polytechnic
        </span>
      );
    }

    // 6. Unscheduled Active Trip (Bus moving outside timetable)
    if (tripLower.includes('unscheduled')) {
      return (
        <span style={{ color: '#d97706', fontWeight: 700 }}>
          Unscheduled Trip (Live Tracking)
        </span>
      );
    }

    // 7. Weekend (No service on Saturday / Sunday unless active)
    const isWeekend = [0, 6].includes(new Date().getDay()) || status === 'weekend';
    if (isWeekend) {
      return nextTripTime ? `Weekend (No Service) • Next Trip: ${nextTripTime}` : 'Weekend (No Service)';
    }

    // 8. Waiting / Driver Preparation Window
    if (status === 'waiting') {
      return nextTripTime ? `Waiting for Service • Next Trip: ${nextTripTime}` : 'Waiting for Service';
    }

    // 9. Completed Trip (e.g. Morning finished on weekday, waiting for evening)
    if (status === 'completed') {
      return nextTripTime ? `Trip Completed • Next Trip: ${nextTripTime}` : 'Trip Completed';
    }

    return nextTripTime ? `Not in Service • Next Trip: ${nextTripTime}` : 'Not in Service';
  };

  // ── Render ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="app-shell" style={{ position: 'relative', height: '100%' }}>
        <TopBar onHamburger={() => setDrawerOpen(true)} />
        <div className="spinner-screen">
          <div className="spinner" />
          <span className="spinner-label">Loading tracker…</span>
        </div>
      </div>
    );
  }

  return (
    <>
      <TopBar
        onHamburger={() => setDrawerOpen(true)}
        onNotification={() => setNotificationOpen(true)}
      />
      <DrawerMenu isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />
      <NotificationDrawer isOpen={notificationOpen} onClose={() => setNotificationOpen(false)} />

      <div className="route-view-ios">
        <div className="route-view-ios__scroll">

          {/* Header Row */}
          <div className="ios-header-row">
            <div className="ios-screen-title">Route View</div>
          </div>
          
          <div className="ios-trip-subtext">
            {getSubtextStatus()}
          </div>

          {/* Stats Card */}
          <div className="ios-stats-card">
            <div className="ios-stat-item">
              <div className="ios-stat-value">{etaText}</div>
              <div className="ios-stat-label">NEXT STOP</div>
            </div>
            <div className="ios-stat-divider" />
            <div className="ios-stat-item">
              <div className="ios-stat-value">{speedText}</div>
              <div className="ios-stat-label">SPEED</div>
            </div>
            <div className="ios-stat-divider" />
            <div className="ios-stat-item">
              <div className="ios-stat-value">{stopsDoneText}</div>
              <div className="ios-stat-label">STOPS</div>
            </div>
          </div>

          {/* Stop Timeline */}
          <div className="ios-timeline-card">
            {stopsToShow.length === 0 ? (
              <div className="ios-empty-state">
                <div className="ios-empty-text">No stop arrivals recorded yet.</div>
              </div>
            ) : (
              <div className="ios-timeline">
                {stopsToShow.map((stop, idx) => {
                  const isVisited  = !!visitedStops[stop.name];
                  const isCurrent  = idx === currentNextIdx;
                  const scheduled  = schedule[stop.name] || '';
                  const actualTime = arrivalTimes[stop.name] || null;
                  const displayTime = isVisited
                    ? actualTime || scheduled
                    : lateMins != null
                      ? computeEstimatedTime(scheduled, lateMins) || scheduled
                      : scheduled;
                  
                  const isLast = idx === stopsToShow.length - 1;
                  
                  // Compute delay state
                  let delayType = 'none';
                  let statusSubtext = 'Scheduled';
                  
                  if (isVisited) {
                    statusSubtext = 'Arrived';
                  } else if (isOnline) {
                    if (isCurrent) {
                      statusSubtext = eta?.eta_minutes != null ? `Approaching • ETA ${Math.round(eta.eta_minutes)} min` : 'Next Stop';
                    }
                  }

                  return (
                    <div key={stop.id || idx} className="ios-timeline-row">
                      {/* Left: Time */}
                      <div className="ios-time-col">
                        <div className="ios-sched-time">{scheduled}</div>
                        {isVisited || isCurrent ? (
                          <div className={`ios-live-time ${delayType === 'late' ? 'ios-time-late' : ''}`}>
                            {displayTime}
                          </div>
                        ) : (
                          <div className="ios-live-time-muted">—</div>
                        )}
                      </div>

                      {/* Center: Track */}
                      <div className="ios-track-col">
                        {isCurrent ? (
                          <div className="ios-bus-badge-wrap">
                            <div className="ios-bus-badge-circle">🚌</div>
                          </div>
                        ) : (
                          <div className={`ios-track-dot ${isVisited ? 'ios-track-dot-visited' : ''}`} />
                        )}
                        {!isLast && (
                          <div className={`ios-track-line ${isVisited ? 'ios-track-line-visited' : ''}`} />
                        )}
                      </div>

                      {/* Right: Stop Info */}
                      <div className="ios-stop-col">
                        <div className={`ios-stop-name ${isCurrent ? 'ios-stop-name-current' : ''} ${isVisited ? 'ios-stop-name-visited' : ''}`}>
                          {stop.name}
                        </div>
                        <div className={`ios-stop-subtext ${isCurrent ? 'ios-stop-subtext-current' : ''}`}>
                          {statusSubtext}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Live Map */}
          <div className="ios-section-header-row">
            <div className="ios-section-title">Live Map</div>
            {busPosition?.server_time && (
              <div className="ios-last-updated">
                Last updated: {formatLastUpdated(busPosition.server_time)}
              </div>
            )}
          </div>
          
          <div className="ios-map-card" onClick={() => navigate('/map')}>
            <BusMapView
              interactive={false}
              center={mapVp.center}
              zoom={mapVp.zoom}
              busCoord={animatedBus}
              isLive={busPosition?.is_live === true}
              stops={stops}
              plannedCoords={plannedCoords}
              trailCoords={trailCoords}
              style={{ height: '100%' }}
            />
            <div className="ios-map-overlay">
              <div className="ios-tap-pill">Tap to view full map</div>
            </div>
          </div>
          
          <div style={{ height: 40 }} />
        </div>
      </div>
    </>
  );
}
