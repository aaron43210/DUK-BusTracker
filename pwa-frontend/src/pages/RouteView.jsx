/**
 * RouteView.jsx — DUK Bus Tracker PWA
 * Home screen: live trip status, stop timeline, stats, mini-map.
 * Mirrors RouteViewScreen.tsx from the React Native app exactly.
 */
import React, { useState, useEffect, useRef, useCallback, useContext } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import NotificationDrawer from '../components/NotificationDrawer';
import BusMapView from '../components/BusMapView';
import BusIdleAnimation from '../components/BusIdleAnimation';
import {
  getLatestGps, getTripState, getRouteHistory, getEta, getRouteGeometry, getRouteSegment, getStops, getWsBusUrl
} from '../api';
import GPSAnimator from '../utils/gpsAnimator';
import TrailManager from '../utils/trailManager';
import { getUser } from '../storage';
import {
  getDelayBadge, computeEstimatedTime, getMapViewport,
  haversineDistKm, todayStr, parseTimeToMinutes,
} from '../timetable';

import { SplashContext } from '../App';

const POLL_MS = 15000;

// ── Next Stop Banner — tiny pill above "Tap to view full map" ──────────
function NextStopBanner({ tripState, nextStop }) {
  const status = tripState?.status ?? 'offline';
  const tripName = (typeof tripState?.trip === 'string' ? tripState.trip : '').toLowerCase();
  const isActive = status === 'active';

  if (isActive && !tripName.includes('unscheduled') && nextStop) {
    return (
      <div className="map-next-stop-pill map-next-stop-pill--active">
        Heading to {nextStop.name}
      </div>
    );
  }

  if (isActive && tripName.includes('unscheduled')) {
    return <div className="map-next-stop-pill map-next-stop-pill--unscheduled">Unscheduled</div>;
  }

  return <div className="map-next-stop-pill map-next-stop-pill--offline">Not in Service</div>;
}

let routeViewLoadedOnce = false;

export default function RouteView() {
  const navigate = useNavigate();
  const { setSplashReady } = useContext(SplashContext);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [user, setUser] = useState(null);
  const [tripState, setTripState] = useState(null);
  const [busPosition, setBusPosition] = useState(null);
  const [routeHistory, setRouteHistory] = useState(null);
  const [eta, setEta] = useState(null);
  const [etaTargetStopId, setEtaTargetStopId] = useState(null);
  const [stops, setStops] = useState([]);
  const [plannedCoords, setPlannedCoords] = useState([]);
  const [trailCoords, setTrailCoords] = useState([]);
  const [animatedBus, setAnimatedBus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const intervalRef = useRef(null);
  const animatorRef = useRef(null);
  const trailManagerRef = useRef(null);
  const wsRef = useRef(null);

  // Initialize TrailManager
  useEffect(() => {
    trailManagerRef.current = new TrailManager(setTrailCoords);
  }, []);

  // Load user
  useEffect(() => {
    setUser(getUser());
  }, []);

  // Animate bus smoothly between positions
  
  // Initialize animator
  useEffect(() => {
    animatorRef.current = new GPSAnimator({
      onPositionUpdate: (point) => {
        setAnimatedBus(point);
      },
      segmentFetcher: async (lat1, lon1, lat2, lon2) => {
        const seg = await getRouteSegment(lat1, lon1, lat2, lon2);
        if (seg && seg.coordinates) {
          return seg.coordinates;
        }
        return [];
      }
    });

    return () => {
      if (animatorRef.current) animatorRef.current.destroy();
    };
  }, []);

  // Connect WebSocket
  useEffect(() => {
    let isMounted = true;
    let ws = null;
    let reconnectTimeout = null;

    const connectWs = () => {
      ws = new WebSocket(getWsBusUrl());
      wsRef.current = ws;

      ws.onmessage = (event) => {
        if (!isMounted) return;
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'gps' && animatorRef.current) {
            animatorRef.current.pushPoint(msg.lat, msg.lon, msg.server_time);
            setBusPosition((prev) => ({ ...prev, lat: msg.lat, lon: msg.lon, speed_kmh: msg.speed_kmh, is_live: true }));
            
            if (trailManagerRef.current) {
              trailManagerRef.current.addLivePoint(msg.lat, msg.lon);
            }
          }
        } catch (e) {}
      };

      ws.onclose = () => {
        if (isMounted) {
          reconnectTimeout = setTimeout(connectWs, 2000);
        }
      };
    };

    connectWs();

    return () => {
      isMounted = false;
      if (ws) ws.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, []);


  const fetchAll = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setError('');

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

      setTripState(trip);
      if (bus && animatorRef.current && !busPosition) {
        animatorRef.current.pushPoint(bus.lat, bus.lon, null);
        setBusPosition(bus);
      }
      setRouteHistory(history);

      // Only update stops when we get data — avoids blanking on network flap
      if (fetchedStops.length > 0) {
        setStops(fetchedStops);
      }

      

      // Dynamic ETA Target (Morning = boarding stop, Evening = destination stop)
      if (bus?.is_live) {
        const isEvening = trip?.trip?.toLowerCase()?.includes('evening');
        const primaryTargetId = isEvening ? getUser()?.destination_stop_id : getUser()?.boarding_stop_id;
        
        let finalEtaRes = null;
        let usedTargetId = primaryTargetId;

        if (primaryTargetId) {
          try {
            finalEtaRes = await getEta(primaryTargetId);
          } catch (_) { }
        }

        // Fallback to final stop if passed or no target set
        if (!finalEtaRes || finalEtaRes.status === 'passed' || finalEtaRes.status === 'deviated') {
          if (fetchedStops && fetchedStops.length > 0) {
            const orderedStops = isEvening ? [...fetchedStops].reverse() : fetchedStops;
            const finalStop = orderedStops[orderedStops.length - 1];
            if (finalStop && finalStop.id !== primaryTargetId) {
               try {
                 finalEtaRes = await getEta(finalStop.id);
                 usedTargetId = finalStop.id;
               } catch (_) { }
            }
          }
        }
        
        setEta(finalEtaRes);
        setEtaTargetStopId(usedTargetId);
      } else {
        setEta(null);
        setEtaTargetStopId(null);
      }

      // Planned route geometry (only once)
      if (plannedCoords.length === 0) {
        try {
          const geo = await getRouteGeometry();
          if (geo?.coordinates?.length) setPlannedCoords(geo.coordinates);
        } catch (_) { }
      }

      // Trail from full trip trace (for all trips)
      if (trip) {
        if (trailManagerRef.current && trailManagerRef.current.tripId !== trip.trip_id) {
          trailManagerRef.current.init(trip.trip_id);
        }
      } else {
        if (trailManagerRef.current) {
          trailManagerRef.current.clear();
          trailManagerRef.current.tripId = null;
        }
      }

    } catch (err) {
      setError('Unable to load data. Check your connection.');
    } finally {
      setLoading(false);
      setRefreshing(false);
      routeViewLoadedOnce = true;
      if (!hasLoadedInitial.current) {
        hasLoadedInitial.current = true;
        setSplashReady();
      }
    }
  }, [plannedCoords.length, setSplashReady, busPosition]);

  // Initial load + polling
  useEffect(() => {
    fetchAll(true);
    intervalRef.current = setInterval(() => fetchAll(false), POLL_MS);
    return () => {
      clearInterval(intervalRef.current);
      
    };
  }, [fetchAll]);

  // ── Derived data ─────────────────────────────────────────────────────────
  // trip is a plain string like "Morning", "Evening", "Unscheduled"
  const tripName = (typeof tripState?.trip === 'string' ? tripState.trip : '').toLowerCase();
  const direction = tripName.includes('morning') ? 'forward'
    : tripName.includes('evening') ? 'reverse'
      : new Date().getHours() >= 14 ? 'reverse' : 'forward';

  const lateMins = tripState?.late_by_minutes ?? null;

  const tripStatus = tripState ? tripState.status : 'loading';
  const isActive = tripStatus === 'active';
  const busIsLive = busPosition?.is_live === true;
  const isOnline = isActive;
  const isUnscheduled = tripName.includes('unscheduled');

  // ── Idle mode detection ───────────────────────────────────────────────────
  // Idle = no useful timeline to show. 
  // We rely on the backend's `tripStatus` which already accounts for dead hours, weekends, etc.
  const isIdleMode = isUnscheduled || ['offline', 'completed', 'weekend', 'idle'].includes(tripStatus);

  // Build timeline from stops + visit history
  const visitedStops = React.useMemo(() => isActive ? (routeHistory?.visitedStops || {}) : {}, [isActive, routeHistory?.visitedStops]);
  const arrivalTimes = React.useMemo(() => isActive ? (routeHistory?.arrivalTimes || {}) : {}, [isActive, routeHistory?.arrivalTimes]);

  // Helper: parse time string like "07:30 AM" → minutes since midnight for sorting
  const timeToMins = (t) => {
    if (!t) return 9999;
    const [time, ampm] = t.trim().split(' ');
    let [h, m] = time.split(':').map(Number);
    if (ampm === 'PM' && h !== 12) h += 12;
    if (ampm === 'AM' && h === 12) h = 0;
    return h * 60 + m;
  };

  const stopsToShow = React.useMemo(() => {
    const stopsFiltered = stops.filter(s => direction === 'forward' ? !!s.morning_time : !!s.evening_time);
    return direction === 'forward'
      ? [...stopsFiltered].sort((a, b) => timeToMins(a.morning_time) - timeToMins(b.morning_time))
      : [...stopsFiltered].sort((a, b) => timeToMins(a.evening_time) - timeToMins(b.evening_time));
  }, [stops, direction]);

  // Find "current next stop" = first unvisited stop
  const currentNextIdx = React.useMemo(() => stopsToShow.findIndex(s => !visitedStops[s.name]), [stopsToShow, visitedStops]);
  const visitedCount = Object.keys(visitedStops).length;
  const nextStop = currentNextIdx >= 0 ? stopsToShow[currentNextIdx] : null;

  // ── Smooth sliding badge calculation ─────────────────────────────────────
  const ROW_HEIGHT = 54; // px — must match CSS .ios-timeline-row height

  let stopProgress = 0;
  if (isActive && animatedBus && currentNextIdx > 0) {
    const fromStop = stopsToShow[currentNextIdx - 1];
    const toStop = stopsToShow[currentNextIdx];
    if (fromStop?.lat && fromStop?.lon && toStop?.lat && toStop?.lon) {
      // Bug 5 fix: haversineDistKm signature is (lon1, lat1, lon2, lat2)
      const totalDist = haversineDistKm(fromStop.lon, fromStop.lat, toStop.lon, toStop.lat);
      const coveredDist = haversineDistKm(fromStop.lon, fromStop.lat, animatedBus[0], animatedBus[1]);
      stopProgress = totalDist > 0.001 ? Math.min(1, Math.max(0, coveredDist / totalDist)) : 0;
    }
  }

  // Y position in px: center of prevStop dot + fraction toward nextStop dot
  const anchorIdx = isActive
    ? (currentNextIdx > 0 ? currentNextIdx - 1 : Math.max(0, currentNextIdx))
    : (stopsToShow.length > 0 ? stopsToShow.length - 1 : 0);
  const badgeTop = (anchorIdx + stopProgress) * ROW_HEIGHT + ROW_HEIGHT / 2;

  // Viewport for mini-map
  const mapVp = getMapViewport(stopsToShow, animatedBus);

  // Pill label shown above bus marker
  const markerLabel = isActive && !tripName.includes('unscheduled') && nextStop
    ? `Heading to ${nextStop.name}`
    : isActive && tripName.includes('unscheduled')
      ? 'Unscheduled'
      : 'Not in Service';

  // Stats text
  // ETA: guard on status==='passed' (bus already past boarding stop) — show '—' not stale minutes
  const etaText = (() => {
    if (!eta || !isActive) return '\u2014';
    if (eta.status === 'passed' || eta.status === 'deviated') return '\u2014';
    if (eta.eta_minutes != null) return `${Math.round(eta.eta_minutes)} min`;
    return '\u2014';
  })();
  const speedText = (busIsLive && busPosition?.speed_kmh != null)
    ? `${Math.round(busPosition.speed_kmh)} km/h` : '—';
  const stopsDoneText = `${visitedCount}/${stopsToShow.length}`;

  // Timeline empty state message
  const getTimelineEmptyMsg = () => {
    if (tripStatus === 'cancelled') return 'This trip has been cancelled.';
    if (tripName.includes('unscheduled')) return 'Bus is on an unscheduled route.';
    if (tripStatus === 'connecting') return 'Connecting to bus GPS…';
    return 'No stop arrivals recorded yet.';
  };

  // Format last updated time
  const formatLastUpdated = (isoStr) => {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + ', ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  };

  // Format current status subtext
  const getSubtextStatus = () => {
    const status = tripState?.status ?? 'offline';
    const nextTripTime = tripState?.next_trip_time;
    if (!isOnline && tripStatus !== 'connecting') {
      const nextTime = tripState?.next_trip_time;
      if (tripStatus === 'completed') return nextTime ? `Trip Completed • Next Trip: ${nextTime}` : 'Trip Completed';
      return nextTime ? `Not in Service • Next Trip: ${nextTime}` : 'Not in Service';
    }
    if (tripStatus === 'connecting') {
      return <span style={{ color: '#d97706', fontWeight: 700 }}>Connecting to Bus...</span>;
    }
    
    if (tripName.includes('morning')) {
      const lateTag = lateMins > 2
        ? <div style={{ color: '#dc2626', fontWeight: 700, marginTop: '4px' }}>Delayed by {lateMins} mins</div>
        : null;
      return (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span><span style={{ color: 'var(--mint-deeper, #059669)', fontWeight: 700 }}>Morning Trip</span> → Digital University Kerala</span>
          {lateTag}
        </div>
      );
    }
    if (tripName.includes('evening')) {
      const lateTag = lateMins > 2
        ? <div style={{ color: '#dc2626', fontWeight: 700, marginTop: '4px' }}>Delayed by {lateMins} mins</div>
        : null;
      return (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span><span style={{ color: 'var(--mint-deeper, #059669)', fontWeight: 700 }}>Evening Trip</span> → Central Polytechnic</span>
          {lateTag}
        </div>
      );
    }
    if (tripName.includes('unscheduled')) {
      return <span style={{ color: '#d97706', fontWeight: 700 }}>Unscheduled Trip (Live Tracking)</span>;
    }
    if (tripStatus === 'cancelled') {
      return (
        <span style={{ color: '#ef4444', fontWeight: 600 }}>
          Trip Cancelled {tripState?.cancellation_reason ? `(${tripState.cancellation_reason})` : ''}
        </span>
      );
    }
    const isWeekend = [0, 6].includes(new Date().getDay()) || status === 'weekend';
    if (isWeekend) {
      return nextTripTime ? `Weekend (No Service) • Next Trip: ${nextTripTime}` : 'Weekend (No Service)';
    }
    if (status === 'waiting') {
      return nextTripTime ? `Waiting for Service • Next Trip: ${nextTripTime}` : 'Waiting for Service';
    }
    return nextTripTime ? `Not in Service • Next Trip: ${nextTripTime}` : 'Not in Service';
  };

  const location = useLocation();
  const showLoadingSpinner = location.state?.showLoadingSpinner === true;

  // ── Render ───────────────────────────────────────────────────────────────

  
  if (loading && showLoadingSpinner) {
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

  if (loading && !tripState) {
    return (
      <>
        <TopBar onHamburger={() => setDrawerOpen(true)} onNotification={() => setNotificationOpen(true)} />
        <div className="route-view-ios">
          <div className="route-view-ios__scroll" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
            <div className="spinner" style={{ width: 30, height: 30, borderTopColor: '#007AFF', borderWidth: 3 }} />
          </div>
        </div>
      </>
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

          {/* Stats Card — hidden in idle mode */}
          {!isIdleMode && (
            <div className="ios-stats-card">
              <div className="ios-stat-item">
                <div className="ios-stat-value">{etaText}</div>
                <div className="ios-stat-label">
                  {(() => {
                    const primaryTargetId = direction === 'reverse' ? user?.destination_stop_id : user?.boarding_stop_id;
                    const displayTargetId = etaTargetStopId || primaryTargetId || (stopsToShow.length > 0 ? stopsToShow[stopsToShow.length - 1].id : null);
                    const targetName = displayTargetId ? stops.find(s => s.id === displayTargetId)?.name : null;
                    return targetName ? `TO ${targetName.toUpperCase()}` : 'TO SAVED STOP';
                  })()}
                </div>
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
          )}

          {/* Stop Timeline — replaced by animated bus in idle mode */}
          {isIdleMode ? (
            <BusIdleAnimation
              nextTripTime={tripState?.next_trip_time}
              isUnscheduled={isUnscheduled}
            />
          ) : (<div className="ios-timeline-card">
            {stopsToShow.length === 0 ? (
              <div className="ios-empty-state">
                <div className="ios-empty-text">{getTimelineEmptyMsg()}</div>
              </div>
            ) : (
              <div className="ios-timeline">
                {stopsToShow.map((stop, idx) => {
                  const isVisited = !!visitedStops[stop.name];
                  const isCurrent = idx === currentNextIdx;
                  // Read scheduled time directly from the stop object (served by the API)
                  const scheduled = (direction === 'forward' ? stop.morning_time : stop.evening_time) || '';
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

                  // Skipped: not visited but there are visited stops chronologically AFTER this one
                  const isSkipped = !isVisited && isActive &&
                    stopsToShow.slice(idx + 1).some(s => visitedStops[s.name]);

                  if (isSkipped) {
                    statusSubtext = 'Skipped';
                    delayType = 'late'; // red
                  } else if (isVisited) {
                    // Per-stop delay: compare actual arrival vs scheduled
                    if (actualTime && scheduled) {
                      const perStopDiff = parseTimeToMinutes(actualTime) - parseTimeToMinutes(scheduled);
                      const action = idx === 0 ? 'Departed' : 'Arrived';
                      if (perStopDiff > 1) {
                        delayType = 'late';
                        statusSubtext = `${action} • +${perStopDiff}m late`;
                      } else if (perStopDiff < -1) {
                        delayType = 'ahead';
                        statusSubtext = `${action} • ${Math.abs(perStopDiff)}m early`;
                      } else {
                        statusSubtext = `${action} on time`;
                      }
                    } else {
                      statusSubtext = idx === 0 ? 'Departed' : 'Arrived';
                    }
                  } else if (isOnline || (lateMins != null && (tripStatus === 'active' || tripStatus === 'connecting'))) {
                    if (lateMins > 1) delayType = 'late';
                    else if (lateMins < -1) delayType = 'ahead';
                    else delayType = 'ontime';

                    if (isCurrent && idx !== 0) {
                      statusSubtext = 'Next Stop';
                    } else if (isCurrent && idx === 0) {
                      statusSubtext = 'Scheduled';
                    } else {
                      if (delayType === 'late') statusSubtext = 'Delayed';
                      else if (delayType === 'ahead') statusSubtext = `${Math.abs(lateMins)}m ahead`;
                      else statusSubtext = 'On time';
                    }
                  }

                  return (
                    <div key={stop.id || idx} className="ios-timeline-row">
                      {/* Left: Time */}
                      <div className="ios-time-col">
                        <div className="ios-sched-time">{scheduled}</div>
                        {isVisited || isOnline || (lateMins != null && (tripStatus === 'active' || tripStatus === 'connecting')) ? (
                          <div className={`ios-live-time ${delayType === 'late' ? 'ios-time-late' : ''}`}>
                            {displayTime}
                          </div>
                        ) : (
                          <div className="ios-live-time-muted">—</div>
                        )}
                      </div>

                      {/* Center: Track — always render dot; floating badge handles active bus */}
                      <div className="ios-track-col">
                        <div className={`ios-track-dot ${isCurrent ? 'ios-track-dot-current' : ''} ${isVisited ? 'ios-track-dot-visited' : ''}`} />
                        {!isLast && (
                          <div className={`ios-track-line ${stopsToShow[idx + 1]?.name in visitedStops ? 'ios-track-line-visited' : ''}`}>
                            {isActive && anchorIdx === idx && stopProgress > 0 && !(stopsToShow[idx + 1]?.name in visitedStops) && (
                              <div style={{
                                position: 'absolute', top: 0, left: 0, right: 0,
                                height: `${stopProgress * 100}%`,
                                background: '#2563eb'
                              }} />
                            )}
                          </div>
                        )}
                      </div>

                      {/* Right: Stop Info */}
                      <div className="ios-stop-col">
                        <div className={`ios-stop-name ${isCurrent ? 'ios-stop-name-current' : ''} ${isVisited ? 'ios-stop-name-visited' : ''}`}>
                          {stop.name}
                        </div>
                        <div className={`ios-stop-subtext ${isCurrent ? 'ios-stop-subtext-current' : ''} ${delayType === 'late' ? 'ios-stop-subtext-late' : ''} ${(delayType === 'ahead' || delayType === 'ontime') && !isVisited && isOnline && !isCurrent ? 'ios-stop-subtext-ahead' : ''}`}>
                          {statusSubtext}
                        </div>
                      </div>
                    </div>
                  );
                })}

                {/* ── Floating bus badge — slides smoothly down the track ── */}
                {isActive && animatedBus && stopsToShow.length > 0 && (
                  <div className="ios-bus-badge-float" style={{ top: badgeTop }}>
                    <div className="ios-bus-badge-circle">🚍</div>
                  </div>
                )}
              </div>
            )}
          </div>)}

          {/* Live Map section header */}
          <div className="ios-section-header-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="ios-section-title" style={{ margin: 0 }}>Live Map</div>
            {!isIdleMode && busPosition?.server_time ? (
              <div className="ios-last-updated" style={{ margin: 0 }}>
                Last updated: {formatLastUpdated(busPosition.server_time)}
              </div>
            ) : <div />}
          </div>

          <div className={isIdleMode ? 'ios-map-card ios-map-card--idle' : 'ios-map-card'} onClick={() => navigate('/map')}>
            <BusMapView
              interactive={false}
              center={mapVp.center}
              zoom={mapVp.zoom}
              defaultPitch={50}
              busCoord={animatedBus}
              isLive={busPosition?.is_live === true}
              markerLabel={markerLabel}
              stops={stopsToShow}
              plannedCoords={direction === 'reverse' ? [...plannedCoords].reverse() : plannedCoords}
              trailCoords={trailCoords}
              style={{ height: '100%' }}
            />
            <div className="ios-map-overlay">
              <div className="ios-tap-pill">Tap to view full map</div>
            </div>
          </div>

          <div style={{ height: 20 }} />
        </div>
      </div>
    </>
  );
}
