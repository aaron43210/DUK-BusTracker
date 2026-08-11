/**
 * RouteView.jsx — DUK Bus Tracker PWA
 * Home screen: live trip status, stop timeline, stats, mini-map.
 * Mirrors RouteViewScreen.tsx from the React Native app exactly.
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import NotificationDrawer from '../components/NotificationDrawer';
import BusMapView from '../components/BusMapView';
import {
  getLatestGps, getTripState, getRouteHistory, getEta, getRouteGeometry, getRouteSegment, getStops,
} from '../api';
import { getUser } from '../storage';
import {
  getDelayBadge, computeEstimatedTime, getMapViewport,
  haversineDistKm, todayStr,
} from '../timetable';

const POLL_MS = 2500;

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

export default function RouteView() {
  const navigate = useNavigate();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [user, setUser] = useState(null);
  const [tripState, setTripState] = useState(null);
  const [busPosition, setBusPosition] = useState(null);
  const [routeHistory, setRouteHistory] = useState(null);
  const [eta, setEta] = useState(null);
  const [stops, setStops] = useState([]);
  const [plannedCoords, setPlannedCoords] = useState([]);
  const [trailCoords, setTrailCoords] = useState([]);
  const [animatedBus, setAnimatedBus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const intervalRef = useRef(null);
  const animIntervalRef = useRef(null);
  const currentPosRef = useRef(null);

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
    } catch (_) { }

    // Calculate cumulative distances for smooth interpolation
    const cumDists = [0];
    for (let i = 1; i < polyline.length; i++) {
      cumDists.push(cumDists[i - 1] + haversineDistKm(polyline[i - 1][0], polyline[i - 1][1], polyline[i][0], polyline[i][1]));
    }
    const totalDist = cumDists[cumDists.length - 1];

    if (totalDist <= 0.00001) {
      setAnimatedBus([targetLon, targetLat]);
      currentPosRef.current = [targetLon, targetLat];
      return;
    }

    if (animIntervalRef.current) cancelAnimationFrame(animIntervalRef.current);

    const duration = POLL_MS;
    const startTime = performance.now();

    const step = (now) => {
      let progress = (now - startTime) / duration;
      if (progress > 1.0) progress = 1.0;

      const currentDist = progress * totalDist;
      let segIdx = 0;
      while (segIdx < cumDists.length - 2 && cumDists[segIdx + 1] < currentDist) {
        segIdx++;
      }

      const p1 = polyline[segIdx];
      const p2 = polyline[segIdx + 1];
      const segSpan = (cumDists[segIdx + 1] - cumDists[segIdx]) || 0.00001;
      const segFrac = Math.max(0, Math.min(1, (currentDist - cumDists[segIdx]) / segSpan));

      const curLon = p1[0] + (p2[0] - p1[0]) * segFrac;
      const curLat = p1[1] + (p2[1] - p1[1]) * segFrac;

      setAnimatedBus([curLon, curLat]);
      currentPosRef.current = [curLon, curLat];

      if (progress < 1.0) {
        animIntervalRef.current = requestAnimationFrame(step);
      } else {
        const finalPoint = polyline[polyline.length - 1];
        setAnimatedBus(finalPoint);
        currentPosRef.current = finalPoint;
      }
    };

    animIntervalRef.current = requestAnimationFrame(step);
  }, []);

  const fetchAll = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setError('');

    try {
      const [tripRes, busRes, histRes, stopsRes] = await Promise.allSettled([
        getTripState(),
        getLatestGps(),
        getRouteHistory(),
        getStops(),
      ]);

      const trip = tripRes.status === 'fulfilled' ? tripRes.value : null;
      const bus = busRes.status === 'fulfilled' ? busRes.value : null;
      const history = histRes.status === 'fulfilled' ? histRes.value : null;
      const fetchedStops = stopsRes.status === 'fulfilled' ? stopsRes.value : [];

      setTripState(trip);
      setBusPosition(bus);
      setRouteHistory(history);

      // Only update stops when we get data — avoids blanking on network flap
      if (fetchedStops.length > 0) {
        setStops(fetchedStops);
      }

      if (bus?.lat && bus?.lon) {
        await animateBusTo(bus.lat, bus.lon);
      }

      // ETA to boarding stop
      const boardingStopId = getUser()?.boarding_stop_id;
      if (boardingStopId && bus?.is_live) {
        try {
          const etaRes = await getEta(boardingStopId);
          setEta(etaRes);
        } catch (_) { }
      }

      // Planned route geometry (only once)
      if (plannedCoords.length === 0) {
        try {
          const geo = await getRouteGeometry();
          if (geo?.coordinates?.length) setPlannedCoords(geo.coordinates);
        } catch (_) { }
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
      cancelAnimationFrame(animIntervalRef.current);
    };
  }, [fetchAll]);

  // ── Derived data ─────────────────────────────────────────────────────────
  // trip is a plain string like "Morning", "Evening", "Unscheduled"
  const tripName = (typeof tripState?.trip === 'string' ? tripState.trip : '').toLowerCase();
  const direction = tripName.includes('morning') ? 'forward'
    : tripName.includes('evening') ? 'reverse'
      : new Date().getHours() >= 14 ? 'reverse' : 'forward';

  const lateMins = tripState?.late_by_minutes ?? null;

  const tripStatus = tripState?.status ?? 'idle';
  const isActive = tripStatus === 'active';
  const busIsLive = busPosition?.is_live === true;
  const isOnline = isActive;

  // Build timeline from stops + visit history
  // Only show visited data when a trip is actively running to avoid stale morning data showing all afternoon
  const visitedStops = isActive ? (routeHistory?.visitedStops || {}) : {};
  const arrivalTimes = isActive ? (routeHistory?.arrivalTimes || {}) : {};

  // Filter to only morning or evening stops based on current direction
  const stopsToShow = stops.filter(s =>
    direction === 'forward' ? !!s.morning_time : !!s.evening_time
  );

  // Find "current next stop" = first unvisited stop
  const currentNextIdx = stopsToShow.findIndex(s => !visitedStops[s.name]);
  const visitedCount = Object.keys(visitedStops).length;
  const nextStop = currentNextIdx >= 0 ? stopsToShow[currentNextIdx] : null;

  // Viewport for mini-map
  const mapVp = getMapViewport(stopsToShow, animatedBus);

  // Pill label shown above bus marker
  const markerLabel = isActive && !tripName.includes('unscheduled') && nextStop
    ? `Heading to ${nextStop.name}`
    : isActive && tripName.includes('unscheduled')
    ? 'Unscheduled'
    : 'Not in Service';

  // Stats text
  const etaText = eta?.eta_minutes != null ? `${Math.round(eta.eta_minutes)} min` : '—';
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

    if (status === 'cancelled') {
      return (
        <span style={{ color: '#ef4444', fontWeight: 600 }}>
          Trip Cancelled {tripState?.cancellation_reason ? `(${tripState.cancellation_reason})` : ''}
        </span>
      );
    }
    // 'connecting' state — fall through to show trip name below
    if (tripName.includes('special')) {
      return (
        <span>
          <span style={{ color: '#8b5cf6', fontWeight: 700 }}>Special Service</span> → {direction === 'forward' ? 'Digital University Kerala' : 'Central Polytechnic'}
        </span>
      );
    }
    if (tripName.includes('morning')) {
      return (
        <span>
          <span style={{ color: 'var(--mint-deeper, #059669)', fontWeight: 700 }}>Morning Trip</span> → Digital University Kerala
        </span>
      );
    }
    if (tripName.includes('evening')) {
      return (
        <span>
          <span style={{ color: 'var(--mint-deeper, #059669)', fontWeight: 700 }}>Evening Trip</span> → Central Polytechnic
        </span>
      );
    }
    if (tripName.includes('unscheduled')) {
      return <span style={{ color: '#d97706', fontWeight: 700 }}>Unscheduled Trip (Live Tracking)</span>;
    }
    const isWeekend = [0, 6].includes(new Date().getDay()) || status === 'weekend';
    if (isWeekend) {
      return nextTripTime ? `Weekend (No Service) • Next Trip: ${nextTripTime}` : 'Weekend (No Service)';
    }
    if (status === 'waiting') {
      return nextTripTime ? `Waiting for Service • Next Trip: ${nextTripTime}` : 'Waiting for Service';
    }
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

                  if (isVisited) {
                    statusSubtext = 'Arrived';
                    if (lateMins > 1) delayType = 'late';
                    else if (lateMins < -1) delayType = 'ahead';
                  } else if (isOnline) {
                    if (lateMins > 1) delayType = 'late';
                    else if (lateMins < -1) delayType = 'ahead';
                    else delayType = 'ontime';

                    if (isCurrent) {
                      statusSubtext = eta?.eta_minutes != null ? `Approaching • ETA ${Math.round(eta.eta_minutes)} min` : 'Next Stop';
                    } else {
                      if (delayType === 'late') statusSubtext = `+${lateMins}m delay`;
                      else if (delayType === 'ahead') statusSubtext = `${Math.abs(lateMins)}m ahead`;
                      else statusSubtext = 'On time';
                    }
                  }

                  return (
                    <div key={stop.id || idx} className="ios-timeline-row">
                      {/* Left: Time */}
                      <div className="ios-time-col">
                        <div className="ios-sched-time">{scheduled}</div>
                        {isVisited || isOnline ? (
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
                            <div className="ios-bus-badge-circle">🚍</div>
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
                        <div className={`ios-stop-subtext ${isCurrent ? 'ios-stop-subtext-current' : ''} ${delayType === 'late' && !isVisited ? 'ios-stop-subtext-late' : ''} ${(delayType === 'ahead' || delayType === 'ontime') && !isVisited && isOnline && !isCurrent ? 'ios-stop-subtext-ahead' : ''}`}>
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
              defaultPitch={50}
              busCoord={animatedBus}
              isLive={busPosition?.is_live === true}
              markerLabel={markerLabel}
              stops={stopsToShow}
              plannedCoords={plannedCoords}
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
