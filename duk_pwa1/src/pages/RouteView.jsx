import React, { useState, useEffect, useRef, useCallback, useContext, lazy, Suspense } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Crosshair, Plus, Minus } from 'lucide-react';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import NotificationDrawer from '../components/NotificationDrawer';
import BusIdleAnimation from '../components/BusIdleAnimation';
import { getUser } from '../storage';
import { getMapViewport, haversineDistKm, parseTimeToMinutes, computeEstimatedTime } from '../timetable';
import { SplashContext, useNotifications, useViewMode } from '../App';
import { useAppContext } from '../context';
import useBusTracking from '../hooks/useBusTracking';

// maplibre-gl is ~1 MB of source, so the map is split into its own chunk and
// only fetched the first time the user opens the Map tab.
const BusMapView = lazy(() => import('../components/BusMapView'));

export default function RouteView() {
  const { fetchAll, locationName } = useBusTracking();
  const navigate = useNavigate();
  const location = useLocation();
  const { setSplashReady } = useContext(SplashContext);
  const { checkNotifications } = useNotifications();
  const { state, dispatch } = useAppContext();
  const { viewMode, setViewMode } = useViewMode();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [pullY, setPullY] = useState(0);
  const [isPulling, setIsPulling] = useState(false);
  const [selectedStop, setSelectedStop] = useState(null);
  const [autoCenter, setAutoCenter] = useState(true);
  const [is3D, setIs3D] = useState(false);
  const startYRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const mapRef = useRef(null);
  // Once mounted the map stays mounted (display-toggled) so its state survives tab switches.
  const [mapMounted, setMapMounted] = useState(viewMode === 'map');

  useEffect(() => {
    if (viewMode === 'map') setMapMounted(true);
  }, [viewMode]);

  const {
    tripState, busPosition, history: routeHistory, stops,
    plannedCoords, eta, etaTargetStopId, mlEtas, isLive
  } = state;

  const activeBusCoord = React.useMemo(() => {
    return busPosition?.lon && busPosition?.lat ? [Number(busPosition.lon), Number(busPosition.lat)] : null;
  }, [busPosition?.lon, busPosition?.lat]);

  // True only when a real scheduled trip is actively running
  const isActiveTrip = ['active', 'connecting', 'on_trip', 'late'].includes(tripState?.status);

  const user = getUser();

  // Read ?view=map from URL on initial load (from DrawerMenu navigation)
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get('view') === 'map') {
      setViewMode('map');
    }
  }, []);

  useEffect(() => {
    if (tripState || stops.length > 0) setSplashReady();
  }, [tripState, stops.length, setSplashReady]);

  // --- Pull to refresh ---
  const handleTouchStart = (e) => {
    if (viewMode !== 'route') return;
    const timelineEl = e.target.closest('.ios-timeline');
    if (timelineEl && timelineEl.scrollTop > 0) { startYRef.current = null; return; }
    if (scrollContainerRef.current && scrollContainerRef.current.scrollTop === 0) {
      startYRef.current = e.touches[0].clientY;
    } else { startYRef.current = null; }
  };

  const handleTouchMove = (e) => {
    if (viewMode !== 'route') return;
    if (startYRef.current !== null) {
      const dy = e.touches[0].clientY - startYRef.current;
      if (dy > 0 && dy < 150) setPullY(dy);
    }
  };

  const handleTouchEnd = async () => {
    if (pullY > 60) {
      setIsPulling(true);
      await checkNotifications();
      await new Promise(r => setTimeout(r, 500));
      setPullY(0);
      setIsPulling(false);
    } else { setPullY(0); }
    startYRef.current = null;
  };

  // --- Trip state helpers ---
  const tripName = tripState?.trip?.toLowerCase() || '';
  const tripStatus = tripState?.status || 'offline';
  const isActive = tripStatus === 'active' || tripStatus === 'on_trip' || tripStatus === 'late' || tripStatus === 'connecting';
  const showHistory = isActive || tripStatus === 'completed';
  const isUnscheduled = tripName === 'unscheduled';

  const direction = tripName.includes('morning') ? 'forward'
    : tripName.includes('evening') ? 'reverse'
      // Prefer the backend's explicit direction field over wall-clock heuristics—
      // the heuristic fires before tripState loads and shows the wrong stop list.
      : (tripState?.direction === 'forward' || tripState?.direction === 'reverse')
        ? tripState.direction
        : new Date().getHours() >= 14 ? 'reverse' : 'forward';

  const lateMins = tripState?.late_by_minutes ?? null;
  const isOnline = isActive;
  const isIdleMode = ['offline', 'weekend', 'idle', 'completed', 'waiting'].includes(tripStatus) || isUnscheduled;

  // Genuinely receiving live telemetry from a bus that has actually started.
  // The backend only reports 'active'/'on_trip'/'late' once GPS is alive and the
  // trip is running; 'connecting' means the departure window is open but the bus
  // has NOT reported live yet. We must not show ETAs, per-stop early/late labels,
  // predicted times, or a "departed" state until this is true — otherwise the
  // timeline fabricates delays and times before the bus has even moved.
  const isTrackingLive =
    (tripStatus === 'active' || tripStatus === 'on_trip' || tripStatus === 'late')
    && busPosition?.is_live === true;

  const visitedStops = React.useMemo(() => {
    // Only use visited stops when we have an active trip AND a real trip_id.
    // Without trip_id the backend can't scope the query and returns empty data;
    // this guard ensures no stale cached data bleeds into the timeline.
    if (!showHistory || !tripState?.trip_id) return {};
    return routeHistory?.visitedStops || {};
  }, [showHistory, tripState?.trip_id, routeHistory?.visitedStops]);

  const arrivalTimes = React.useMemo(() => {
    if (!showHistory || !tripState?.trip_id) return {};
    return routeHistory?.arrivalTimes || {};
  }, [showHistory, tripState?.trip_id, routeHistory?.arrivalTimes]);

  const stopsToShow = React.useMemo(() => {
    const filtered = stops.filter(s => direction === 'forward' ? !!s.morning_time : !!s.evening_time);
    return direction === 'forward'
      ? [...filtered].sort((a, b) => (a.morning_order_index || 0) - (b.morning_order_index || 0))
      : [...filtered].sort((a, b) => (a.evening_order_index || 0) - (b.evening_order_index || 0));
  }, [stops, direction]);

  const closestStopIdx = React.useMemo(() => {
    if (!activeBusCoord || stopsToShow.length === 0) return -1;
    let minD = Infinity;
    let idx = -1;

    // Find the highest index among all visited stops, so we don't get stuck on a skipped stop.
    let highestVisitedIdx = -1;
    for (let i = 0; i < stopsToShow.length; i++) {
      if (visitedStops[stopsToShow[i].name]) highestVisitedIdx = i;
    }
    let baseIdx = highestVisitedIdx + 1;
    if (baseIdx >= stopsToShow.length) baseIdx = stopsToShow.length;
    
    // Widen the search window: 2 stops behind and 5 ahead.
    // The narrower [-1,+3] window missed the bus's actual stop whenever
    // the backend visitedStops lagged by one poll cycle (15–30 s).
    const startSearchIdx = Math.max(0, baseIdx - 2);
    const endSearchIdx = Math.min(stopsToShow.length, baseIdx + 5);

    for (let i = startSearchIdx; i < endSearchIdx; i++) {
      const s = stopsToShow[i];
      const lat = Number(s.lat);
      const lon = Number(s.lon);
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        const d = haversineDistKm(activeBusCoord[0], activeBusCoord[1], lon, lat);
        if (d < minD) { minD = d; idx = i; }
      }
    }
    return idx;
  }, [activeBusCoord, stopsToShow, visitedStops]);

  const currentNextIdx = React.useMemo(() => {
    let highestVisitedIdx = -1;
    for (let i = 0; i < stopsToShow.length; i++) {
      if (visitedStops[stopsToShow[i].name]) highestVisitedIdx = i;
    }
    let idx = highestVisitedIdx + 1;
    
    // Catch-up: If the bus is physically at or past a stop, advance the index
    if (closestStopIdx !== -1 && closestStopIdx > idx) {
      idx = closestStopIdx;
    }
    return Math.min(idx, stopsToShow.length);
  }, [stopsToShow, visitedStops, closestStopIdx]);

  const visitedCount = Math.max(Object.keys(visitedStops).length, currentNextIdx);
  const nextStop = currentNextIdx >= 0 && currentNextIdx < stopsToShow.length ? stopsToShow[currentNextIdx] : null;

  const computedLateMins = React.useMemo(() => {
    // Only trust the delay the backend actually computed from live GPS, and only
    // while we are genuinely tracking a live, running bus. Never infer a
    // "presumed delay" from the wall clock — that is what produced fake
    // "Delayed by Nm" labels on a trip that had not departed yet.
    if (!isTrackingLive) return null;
    return tripState?.late_by_minutes ?? null;
  }, [isTrackingLive, tripState?.late_by_minutes]);

  const ROW_HEIGHT = 54;
  let stopProgress = 0;
  if (isActive && activeBusCoord && currentNextIdx > 0) {
    const fromStop = stopsToShow[currentNextIdx - 1];
    const toStop = stopsToShow[currentNextIdx];
    if (Number.isFinite(Number(fromStop?.lat)) && Number.isFinite(Number(fromStop?.lon)) && Number.isFinite(Number(toStop?.lat)) && Number.isFinite(Number(toStop?.lon))) {
      const totalDist = haversineDistKm(Number(fromStop.lon), Number(fromStop.lat), Number(toStop.lon), Number(toStop.lat));
      const coveredDist = haversineDistKm(Number(fromStop.lon), Number(fromStop.lat), activeBusCoord[0], activeBusCoord[1]);
      stopProgress = totalDist > 0.001 ? Math.min(1, Math.max(0, coveredDist / totalDist)) : 0;
    }
  }

  const anchorIdx = isActive
    ? (currentNextIdx > 0 ? currentNextIdx - 1 : Math.max(0, currentNextIdx))
    : (stopsToShow.length > 0 ? stopsToShow.length - 1 : 0);
  const badgeTop = (anchorIdx + stopProgress) * ROW_HEIGHT + ROW_HEIGHT / 2;

  const mapVp = getMapViewport(stopsToShow, activeBusCoord);

  const headingStatus = isActive && !tripName.includes('unscheduled') && nextStop
    ? `Heading to ${nextStop.name.split(',')[0]}`
    : (locationName ? locationName : 'Locating...');

  const etaText = (() => {
    if (!eta || !isActive || tripStatus === 'connecting') return '\u2014';
    if (eta.status === 'passed' || eta.status === 'deviated') return '\u2014';
    if (eta.eta_minutes != null) return `${Math.round(eta.eta_minutes)} min`;
    return '\u2014';
  })();
  const speedText = (isLive && tripStatus !== 'connecting' && busPosition?.is_live && busPosition?.speed_kmh != null) ? `${Math.round(busPosition.speed_kmh)} km/h` : '\u2014';
  const stopsDoneText = `${visitedCount}/${stopsToShow.length}`;

  const getTimelineEmptyMsg = () => {
    if (tripStatus === 'cancelled') return 'This trip has been cancelled.';
    if (tripName.includes('unscheduled')) return 'Bus is on an unscheduled route.';
    if (tripStatus === 'connecting') return 'Connecting to bus GPS...';
    return 'No stop arrivals recorded yet.';
  };

  const formatLastUpdated = () => {
    const rawTime = (busPosition && busPosition.server_time) || (tripState && tripState.last_updated_time);
    if (!rawTime) return null;
    const date = new Date(rawTime);
    if (isNaN(date.getTime())) return null;
    
    const timeString = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    const day = date.getDate();
    const month = date.toLocaleString([], { month: 'short' });
    return `${timeString}, ${day} ${month}`;
  };

  const globalDelayStatus = (computedLateMins != null && computedLateMins >= 1) ? 'late' : 'ontime';

  const getSubtextStatus = () => {
    const status = tripState?.status ?? 'offline';

    if (!isOnline) {
      if (status === 'cancelled') return <span style={{ color: '#ef4444', fontWeight: 600 }}>Trip Cancelled {tripState?.cancellation_reason ? `(${tripState.cancellation_reason})` : ''}</span>;
      return locationName ? locationName : 'Last Location';
    }

    if (tripName.includes('unscheduled')) {
      return <span style={{ color: '#1f2937', fontWeight: 700 }}>{locationName ? locationName : 'Unscheduled Trip'}</span>;
    }
    
    return locationName ? locationName : 'Live Tracking';
  };

  const displayHistoryCoords = React.useMemo(() => {
    if (isIdleMode) return [];

    // PRIMARY: Use the full server-side trail from trip_trace (DB).
    // gps-ingestion writes every snapped GPS point here from trip start,
    // so a user who opens the app mid-trip sees the complete route — not
    // just from when they logged in. This is the fix for the "trail only
    // shows from login" bug: the planned-route slice was winning over this
    // and silently throwing away the real historical path.
    if (routeHistory?.coords && routeHistory.coords.length > 0) {
      return routeHistory.coords;
    }

    // FALLBACK: DB trail not yet fetched (first render before the HTTP poll
    // returns, or trip_id not known yet). Show a temporary visual placeholder:
    // the planned route sliced up to the bus's current position. It will be
    // replaced by the real DB trail as soon as the first poll completes.
    if (plannedCoords && plannedCoords.length > 0 && activeBusCoord) {
      let minD = Infinity;
      let closestIdx = -1;
      for (let i = 0; i < plannedCoords.length; i++) {
        const d = haversineDistKm(activeBusCoord[0], activeBusCoord[1], plannedCoords[i][0], plannedCoords[i][1]);
        if (d < minD) {
          minD = d;
          closestIdx = i;
        }
      }
      if (closestIdx !== -1) {
        return plannedCoords.slice(0, closestIdx + 1);
      }
    }

    return [];
  }, [routeHistory?.coords, plannedCoords, activeBusCoord, isIdleMode]);

  useEffect(() => {
    if (viewMode !== 'route') return;
    const timer = setTimeout(() => {
      const el = document.getElementById('active-stop-row');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 100);
    return () => clearTimeout(timer);
  }, [stopsToShow.length, currentNextIdx, viewMode]);

  // Map controls
  const recenter = () => {
    if (activeBusCoord) { mapRef.current?.easeTo({ center: activeBusCoord, zoom: 14, duration: 600 }); setAutoCenter(true); }
    else if (stops.length) { const vp = getMapViewport(stops, null); mapRef.current?.easeTo({ center: vp.center, zoom: vp.zoom, duration: 600 }); }
  };
  const zoomIn = () => mapRef.current?.zoomIn({ duration: 300 });
  const zoomOut = () => mapRef.current?.zoomOut({ duration: 300 });
  const toggle3D = () => {
    if (is3D) { mapRef.current?.easeTo({ pitch: 0, bearing: 0, duration: 800 }); setIs3D(false); }
    else { mapRef.current?.easeTo({ pitch: 60, bearing: 45, duration: 1000 }); setIs3D(true); }
  };

  // Stop card for map view
  const StopCard = ({ stop }) => {
    const distKm = activeBusCoord && stop.lat && stop.lon
      ? haversineDistKm(activeBusCoord[0], activeBusCoord[1], Number(stop.lon), Number(stop.lat))
      : null;
    const scheduled = direction === 'forward' ? (stop.morning_time || '\u2014') : (stop.evening_time || '\u2014');
    return (
      <div className="stop-card-overlay" style={{ backdropFilter: 'blur(24px) saturate(180%)', WebkitBackdropFilter: 'blur(24px) saturate(180%)', background: 'rgba(255,255,255,0.72)', border: '1px solid rgba(255,255,255,0.9)' }}>
        <div className="stop-card__handle" />
        <button className="stop-card__close" onClick={() => setSelectedStop(null)}>&#x2715;</button>
        <div className="stop-card__name">{stop.name}</div>
        <div className="stop-card__row">
          <span className="stop-card__row-icon"></span>
          <div><div className="stop-card__row-label">Scheduled arrival</div><div className="stop-card__row-value">{scheduled}</div></div>
        </div>
        {distKm != null && (
          <div className="stop-card__row">
            <span className="stop-card__row-icon"></span>
            <div><div className="stop-card__row-label">Distance from bus</div><div className="stop-card__row-value">{distKm < 1 ? `${Math.round(distKm * 1000)}m` : `${distKm.toFixed(1)}km`}</div></div>
          </div>
        )}
        {eta?.eta_minutes != null && stop.id === user?.boarding_stop_id && (
          <div className="stop-card__row">
            <span className="stop-card__row-icon"></span>
            <div><div className="stop-card__row-label">Predicted ETA</div><div className="stop-card__row-value">~{Math.round(eta.eta_minutes)} min</div></div>
          </div>
        )}
      </div>
    );
  };

  // Only calculate the initial viewport once on mount (or when stops/bus load)
  const initialVp = React.useMemo(() => getMapViewport(stops, activeBusCoord), [stops.length > 0, !!activeBusCoord]);

  // Auto-center whenever activeBusCoord changes, IF autoCenter is enabled.
  // While live, BusMapView recenters on the *animated* position every frame, so
  // we skip the raw-point easeTo here to avoid fighting it (which caused the map
  // to step between GPS pings). Offline (no animation feed) still eases here.
  useEffect(() => {
    if (autoCenter && activeBusCoord && mapRef.current && !isLive) {
      mapRef.current.easeTo({ center: activeBusCoord, duration: 600 });
    }
  }, [activeBusCoord, autoCenter, isLive]);

  // Fix map size when switching to Map View.
  // Do NOT re-enable autoCenter here — the user may have intentionally panned
  // away and then switched tabs; forcing follow back on is what caused the
  // "can't pan permanently" bug. A one-shot easeTo is enough to show the bus.
  useEffect(() => {
    if (viewMode === 'map' && mapRef.current) {
      const timer = setTimeout(() => {
        if (mapRef.current) {
          mapRef.current.resize();
          if (activeBusCoord) {
            mapRef.current.easeTo({ center: activeBusCoord, zoom: 14, duration: 600 });
          } else if (stops.length) {
            const vp = getMapViewport(stops, null);
            mapRef.current.easeTo({ center: vp.center, zoom: vp.zoom, duration: 600 });
          }
        }
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [viewMode]); // We purposefully only react to viewMode changes

  return (
    <>
      <TopBar onHamburger={() => setDrawerOpen(true)} onNotification={() => setNotificationOpen(true)} />
      <DrawerMenu isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />
      <NotificationDrawer isOpen={notificationOpen} onClose={() => setNotificationOpen(false)} />

      {/* --- Route View Header & Stats (Visible in both modes) --- */}
      <div style={{ padding: '12px 16px 0 16px', flexShrink: 0, position: 'relative', zIndex: 10, background: '#ffffff' }}>
        <div className="ios-header-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div className="ios-screen-title">{getSubtextStatus()}</div>
          {formatLastUpdated() && (
            <div style={{ fontSize: '10px', color: 'var(--text-muted, #71717a)', fontWeight: 500 }}>
              Last updated: {formatLastUpdated()}
            </div>
          )}
        </div>
        <div className="ios-trip-subtext" style={{ marginBottom: isIdleMode ? '8px' : '6px', fontSize: '12px' }}>Current Location</div>

        {/* Stats Card */}
        {!isIdleMode && (
          <div className="ios-stats-card" style={{ marginBottom: '8px', padding: '10px 12px' }}>
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
            <div className="ios-stat-item"><div className="ios-stat-value">{speedText}</div><div className="ios-stat-label">SPEED</div></div>
          </div>
        )}
      </div>

      {/* --- Liquid Glass Toggle Bar --- */}
      <div className="duk1-toggle-bar" style={{ 
        flexShrink: 0, 
        position: 'relative', 
        zIndex: 10,
        top: 'auto',
        background: 'transparent',
        borderBottom: 'none',
        boxShadow: 'none',
        paddingBottom: '8px',
        paddingTop: '0px',
      }}>
        <div className="duk1-toggle-track">
          <button
            id="duk1-tab-route"
            className={`duk1-toggle-tab${viewMode === 'route' ? ' duk1-toggle-tab--active' : ''}`}
            onClick={() => setViewMode('route')}
          >
            Route View
          </button>
          <button
            id="duk1-tab-map"
            className={`duk1-toggle-tab${viewMode === 'map' ? ' duk1-toggle-tab--active' : ''}`}
            onClick={() => { setViewMode('map'); recenter(); }}
          >
            Map View
          </button>
        </div>
      </div>

      {/* --- Dynamic Content Area --- */}
      <div style={{ position: 'relative', flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* --- Route View Timeline Panel --- */}
        <div
          className="route-view-ios"
          style={{
            display: viewMode === 'route' ? 'block' : 'none',
            position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, overflow: 'hidden'
          }}
        >
          {/* Pull to refresh indicator */}
          <div style={{
            position: 'absolute', top: 0, left: 0, right: 0, height: '60px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            opacity: pullY > 10 ? Math.min(1, pullY / 60) : 0,
            transform: `translateY(${(isPulling ? 60 : pullY) - 60}px)`,
            transition: isPulling || pullY === 0 ? 'transform 0.3s ease-out, opacity 0.3s ease-out' : 'none',
            zIndex: 1
          }}>
            <div className="spinner" style={{ width: 24, height: 24, borderTopColor: '#007AFF', borderWidth: 2 }} />
          </div>

          <div
            className="route-view-ios__scroll"
            ref={scrollContainerRef}
            onTouchStart={handleTouchStart}
            onTouchMove={handleTouchMove}
            onTouchEnd={handleTouchEnd}
            style={{
              paddingTop: '8px',
              transform: `translateY(${isPulling ? 60 : pullY}px)`,
              transition: isPulling || pullY === 0 ? 'transform 0.3s ease-out' : 'none',
              zIndex: 2, position: 'relative', height: '100%', overflowY: 'auto'
            }}
          >
            {isIdleMode ? (
            <BusIdleAnimation nextTripTime={tripState?.next_trip_time} isUnscheduled={isUnscheduled} />
          ) : (
            <div className="ios-timeline-card">
              {stopsToShow.length === 0 ? (
                <div className="ios-empty-state"><div className="ios-empty-text">{getTimelineEmptyMsg()}</div></div>
              ) : (
                <div className={`ios-timeline ios-timeline--status-${globalDelayStatus}`}>
                  {stopsToShow.map((stop, idx) => {
                    const isPassedGeographically = idx < currentNextIdx;
                    const isVisited = !!visitedStops[stop.name] || isPassedGeographically;
                    const isCurrent = idx === currentNextIdx;
                    const scheduled = (direction === 'forward' ? stop.morning_time : stop.evening_time) || '';
                    const actualTime = arrivalTimes[stop.name] || null;
                    const effectiveLateMins = (computedLateMins != null && computedLateMins <= -1) ? 0 : computedLateMins;
                    let displayTime = isVisited ? actualTime || scheduled : effectiveLateMins != null ? computeEstimatedTime(scheduled, effectiveLateMins) || scheduled : scheduled;
                    const isLast = idx === stopsToShow.length - 1;
                    let delayType = 'none';
                    let statusSubtext = '';

                    const isSkipped = (!visitedStops[stop.name] && isPassedGeographically) || (!isVisited && isActive && stopsToShow.slice(idx + 1).some(s => visitedStops[s.name]));
                    if (isSkipped) { statusSubtext = 'Passed / Skipped'; delayType = 'late'; }
                    else if (isVisited) {
                      if (actualTime && scheduled) {
                        const perStopDiff = parseTimeToMinutes(actualTime) - parseTimeToMinutes(scheduled);
                        const action = idx === 0 ? 'Departed' : 'Arrived';
                        if (perStopDiff > 1) { delayType = 'late'; statusSubtext = `${action}  +${perStopDiff}m late`; }
                        else if (perStopDiff < -1) { delayType = 'ahead'; statusSubtext = `${action}  ${Math.abs(perStopDiff)}m early`; }
                        else { statusSubtext = `${action} on time`; }
                      } else { statusSubtext = idx === 0 ? 'Departed' : 'Passed'; }
                    } else if (isTrackingLive) {
                      let usedMl = false;
                      // Only use ML ETAs for future stops!
                      if (!isPassedGeographically && mlEtas && mlEtas[stop.id] !== undefined) {
                        const now = new Date();
                        const currentMins = now.getHours() * 60 + now.getMinutes();
                        const scheduledMins = parseTimeToMinutes(scheduled);
                        if (scheduledMins) {
                          const mlPredictedDelay = Math.round(currentMins + mlEtas[stop.id] - scheduledMins);
                          displayTime = computeEstimatedTime(scheduled, mlPredictedDelay) || scheduled;
                          if (mlPredictedDelay > 1) { delayType = 'late'; statusSubtext = `Delayed by ${mlPredictedDelay}m`; }
                          else if (mlPredictedDelay < -1) { delayType = 'ahead'; statusSubtext = `${Math.abs(mlPredictedDelay)}m early`; }
                          else { delayType = 'ontime'; statusSubtext = `On time`; }
                          usedMl = true;
                        }
                      }
                      if (!usedMl) {
                        if (effectiveLateMins >= 1) delayType = 'late';
                        else if (effectiveLateMins <= -1) delayType = 'ahead';
                        else delayType = 'ontime';
                        
                        if (delayType === 'late') statusSubtext = `Delayed by ${effectiveLateMins}m`;
                        else if (delayType === 'ahead') statusSubtext = `${Math.abs(effectiveLateMins)}m early`;
                        else statusSubtext = 'On time';
                      }
                    }

                    return (
                      <div key={stop.id || idx} id={isCurrent ? 'active-stop-row' : undefined} className="ios-timeline-row">
                        <div className="ios-time-col">
                          <div className="ios-sched-time">{scheduled}</div>
                          {isVisited || isTrackingLive ? (
                            <div className={`ios-live-time ${delayType === 'late' ? 'ios-time-late' : ''}`}>{displayTime}</div>
                          ) : (<div className="ios-live-time-muted">&mdash;</div>)}
                        </div>
                        <div className="ios-track-col">
                          <div className={`ios-track-dot ${isCurrent ? 'ios-track-dot-current' : ''} ${isVisited ? 'ios-track-dot-visited' : ''}`} />
                          {!isLast && (
                            <div className={`ios-track-line ${stopsToShow[idx + 1]?.name in visitedStops ? 'ios-track-line-visited' : ''}`}>
                              {isActive && anchorIdx === idx && stopProgress > 0 && !(stopsToShow[idx + 1]?.name in visitedStops) && (
                                <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: `${stopProgress * 100}%`, background: 'var(--timeline-track, #059669)' }} />
                              )}
                            </div>
                          )}
                        </div>
                        <div className="ios-stop-col">
                          <div className={`ios-stop-name ${isCurrent ? 'ios-stop-name-current' : ''} ${isVisited ? 'ios-stop-name-visited' : ''}`}>{stop.name}</div>
                          {statusSubtext && <div className={`ios-stop-subtext ${isCurrent ? 'ios-stop-subtext-current' : ''} ${delayType === 'late' ? 'ios-stop-subtext-late' : ''} ${(delayType === 'ahead' || delayType === 'ontime') && !isVisited && isTrackingLive && !isCurrent ? 'ios-stop-subtext-ahead' : ''}`}>{statusSubtext}</div>}
                        </div>
                      </div>
                    );
                  })}
                  {isActive && activeBusCoord && stopsToShow.length > 0 && (
                    <div className="ios-bus-badge-float" style={{ top: badgeTop }}><div className="ios-bus-badge-circle"></div></div>
                  )}
                </div>
              )}
            </div>
          )}
          <div style={{ height: 20 }} />
        </div>
      </div>

      {/* --- Map View Panel (mounted on first open, then display-toggled) --- */}
      <div
        style={{
          display: viewMode === 'map' ? 'block' : 'none',
          position: 'absolute',
          top: 0,
          left: 0, right: 0, bottom: 0,
          zIndex: 1,
        }}
      >
        {mapMounted && (
          <Suspense fallback={null}>
            <BusMapView
              interactive={true}
              center={initialVp.center}
              zoom={initialVp.zoom}
              busCoord={activeBusCoord}
              isLive={isLive}
              isActiveTrip={isActiveTrip && !isUnscheduled}
              autoCenter={autoCenter}
              onAutoCenterChange={setAutoCenter}
              headingStatus={headingStatus}
              stops={isIdleMode ? [] : stops}
              plannedCoords={isIdleMode ? [] : plannedCoords}
              routeHistoryCoords={displayHistoryCoords}
              onStopClick={(s) => setSelectedStop(s)}
              onMapLoad={(map) => { 
                mapRef.current = map; 
                map.on('dragstart', () => setAutoCenter(false)); 
                recenter();
              }}
              style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
            />
          </Suspense>
        )}

        {/* Map Controls */}
        <div className="map-controls" style={{ bottom: selectedStop ? '240px' : '24px', position: 'absolute', right: '16px', zIndex: 10 }}>
          <button
            className={`map-ctrl-btn ${autoCenter ? 'map-ctrl-btn--active' : ''}`}
            onClick={recenter}
            style={{ backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', background: 'rgba(255,255,255,0.7)' }}
          >
            <Crosshair size={22} color={autoCenter ? '#007AFF' : '#6b7280'} />
          </button>
          <div className="map-ctrl-divider" />
          <button className="map-ctrl-btn" onClick={zoomIn} style={{ backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', background: 'rgba(255,255,255,0.7)' }}>
            <Plus size={20} color="#1f2937" />
          </button>
          <div className="map-ctrl-divider" />
          <button className="map-ctrl-btn" onClick={zoomOut} style={{ backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', background: 'rgba(255,255,255,0.7)' }}>
            <Minus size={20} color="#1f2937" />
          </button>
          <div className="map-ctrl-divider" />
          <button
            className="map-ctrl-btn"
            onClick={toggle3D}
            style={{ fontWeight: '800', fontSize: '13px', color: is3D ? '#007AFF' : '#1f2937', backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', background: 'rgba(255,255,255,0.7)' }}
          >
            3D
          </button>
        </div>

        {selectedStop && <StopCard stop={selectedStop} />}
      </div>
      </div>
    </>
  );
}
