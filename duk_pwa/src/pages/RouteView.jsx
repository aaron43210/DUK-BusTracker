import React, { useState, useEffect, useRef, useCallback, useContext } from 'react';
import { useNavigate } from 'react-router-dom';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import NotificationDrawer from '../components/NotificationDrawer';
import BusMapView from '../components/BusMapView';
import BusIdleAnimation from '../components/BusIdleAnimation';
import { getUser } from '../storage';
import { getMapViewport, haversineDistKm, parseTimeToMinutes, computeEstimatedTime } from '../timetable';
import { SplashContext, useNotifications } from '../App';
import { useAppContext } from '../context';
import useBusTracking from '../hooks/useBusTracking';

export default function RouteView() {
  const { fetchAll, locationName } = useBusTracking();
  const navigate = useNavigate();
  const { setSplashReady } = useContext(SplashContext);
  const { checkNotifications } = useNotifications();
  const { state, dispatch } = useAppContext();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [pullY, setPullY] = useState(0);
  const [isPulling, setIsPulling] = useState(false);
  const startYRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const [notificationOpen, setNotificationOpen] = useState(false);

  const {
    tripState, busPosition, history: routeHistory, stops,
    plannedCoords, eta, etaTargetStopId, mlEtas, animatedBus, isLive
  } = state;

  const user = getUser();

  useEffect(() => {
    if (tripState || stops.length > 0) {
      setSplashReady();
    }
  }, [tripState, stops.length, setSplashReady]);

  const handleTouchStart = (e) => {
    const timelineEl = e.target.closest('.ios-timeline');
    if (timelineEl && timelineEl.scrollTop > 0) {
      startYRef.current = null;
      return;
    }
    if (scrollContainerRef.current && scrollContainerRef.current.scrollTop === 0) {
      startYRef.current = e.touches[0].clientY;
    } else {
      startYRef.current = null;
    }
  };

  const handleTouchMove = (e) => {
    if (startYRef.current !== null) {
      const y = e.touches[0].clientY;
      const dy = y - startYRef.current;
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
    } else {
      setPullY(0);
    }
    startYRef.current = null;
  };

  const tripName = tripState?.trip?.toLowerCase() || '';
  const tripStatus = tripState?.status || 'offline';
  const isActive = tripStatus === 'active' || tripStatus === 'on_trip' || tripStatus === 'late';
  const showHistory = isActive || tripStatus === 'completed';
  const isUnscheduled = tripName === 'unscheduled';

  const direction = tripName.includes('morning') ? 'forward'
    : tripName.includes('evening') ? 'reverse'
      : new Date().getHours() >= 14 ? 'reverse' : 'forward';

  const lateMins = tripState?.late_by_minutes ?? null;
  const isOnline = isActive;

  const isIdleMode = ['offline', 'weekend', 'idle', 'completed', 'waiting'].includes(tripStatus) || isUnscheduled;

  const visitedStops = React.useMemo(() => showHistory ? (routeHistory?.visitedStops || {}) : {}, [showHistory, routeHistory?.visitedStops]);
  const arrivalTimes = React.useMemo(() => showHistory ? (routeHistory?.arrivalTimes || {}) : {}, [showHistory, routeHistory?.arrivalTimes]);

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

  const currentNextIdx = React.useMemo(() => stopsToShow.findIndex(s => !visitedStops[s.name]), [stopsToShow, visitedStops]);
  const visitedCount = Object.keys(visitedStops).length;
  const nextStop = currentNextIdx >= 0 ? stopsToShow[currentNextIdx] : null;

  const ROW_HEIGHT = 54;
  let stopProgress = 0;
  if (isActive && animatedBus && currentNextIdx > 0) {
    const fromStop = stopsToShow[currentNextIdx - 1];
    const toStop = stopsToShow[currentNextIdx];
    if (
      Number.isFinite(fromStop?.lat) && Number.isFinite(fromStop?.lon) &&
      Number.isFinite(toStop?.lat) && Number.isFinite(toStop?.lon)
    ) {
      const totalDist = haversineDistKm(fromStop.lon, fromStop.lat, toStop.lon, toStop.lat);
      const coveredDist = haversineDistKm(fromStop.lon, fromStop.lat, animatedBus[0], animatedBus[1]);
      stopProgress = totalDist > 0.001 ? Math.min(1, Math.max(0, coveredDist / totalDist)) : 0;
    }
  }

  const anchorIdx = isActive
    ? (currentNextIdx > 0 ? currentNextIdx - 1 : Math.max(0, currentNextIdx))
    : (stopsToShow.length > 0 ? stopsToShow.length - 1 : 0);
  const badgeTop = (anchorIdx + stopProgress) * ROW_HEIGHT + ROW_HEIGHT / 2;

  const mapVp = getMapViewport(stopsToShow, animatedBus);

  const headingStatus = isActive && !tripName.includes('unscheduled') && nextStop
    ? `Heading to ${nextStop.name.split(',')[0]}`
    : (locationName ? `At ${locationName}` : 'Locating...');

  const etaText = (() => {
    if (!eta || !isActive) return '\u2014';
    if (eta.status === 'passed' || eta.status === 'deviated') return '\u2014';
    if (eta.eta_minutes != null) return `${Math.round(eta.eta_minutes)} min`;
    return '\u2014';
  })();
  const speedText = (isLive && busPosition?.speed_kmh != null)
    ? `${Math.round(busPosition.speed_kmh)} km/h` : '—';
  const stopsDoneText = `${visitedCount}/${stopsToShow.length}`;

  const getTimelineEmptyMsg = () => {
    if (tripStatus === 'cancelled') return 'This trip has been cancelled.';
    if (tripName.includes('unscheduled')) return 'Bus is on an unscheduled route.';
    if (tripStatus === 'connecting') return 'Connecting to bus GPS…';
    return 'No stop arrivals recorded yet.';
  };

  const formatLastUpdated = () => {
    const rawTime = (busPosition && busPosition.server_time) || (tripState && tripState.last_updated_time);
    if (!rawTime) return '';
    const d = new Date(rawTime);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + ', ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  };

  const globalDelayStatus = (lateMins != null && lateMins >= 1) ? 'late' : 'ontime';

  const getSubtextStatus = () => {
    const status = tripState?.status ?? 'offline';
    const nextTripTime = tripState?.next_trip_time;
    if (!isOnline) {
      const nextTime = tripState?.next_trip_time;
      if (tripStatus === 'completed') return nextTime ? `Trip Completed • Next Trip: ${nextTime}` : 'Trip Completed';
      return locationName
        ? (nextTime ? `At ${locationName} • Next Trip: ${nextTime}` : `At ${locationName}`)
        : (nextTime ? `Not in Service • Next Trip: ${nextTime}` : 'Not in Service');
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
    return locationName
      ? (nextTripTime ? `At ${locationName} • Next Trip: ${nextTripTime}` : `At ${locationName}`)
      : (nextTripTime ? `Not in Service • Next Trip: ${nextTripTime}` : 'Not in Service');
  };

  useEffect(() => {
    const timer = setTimeout(() => {
      const activeElement = document.getElementById('active-stop-row');
      if (activeElement) {
        activeElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 100);
    return () => clearTimeout(timer);
  }, [stopsToShow.length, currentNextIdx]);

  return (
    <>
      <TopBar onHamburger={() => setDrawerOpen(true)} onNotification={() => setNotificationOpen(true)} />
      <DrawerMenu isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />
      <NotificationDrawer isOpen={notificationOpen} onClose={() => setNotificationOpen(false)} />

      <div className="route-view-ios" style={{ position: 'relative', overflow: 'hidden' }}>
        <div
          style={{
            position: 'absolute', top: 0, left: 0, right: 0, height: '60px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            opacity: pullY > 10 ? Math.min(1, pullY / 60) : 0,
            transform: `translateY(${(isPulling ? 60 : pullY) - 60}px)`,
            transition: isPulling || pullY === 0 ? 'transform 0.3s ease-out, opacity 0.3s ease-out' : 'none',
            zIndex: 1
          }}
        >
          <div className="spinner" style={{ width: 24, height: 24, borderTopColor: '#007AFF', borderWidth: 2 }} />
        </div>

        <div
          className="route-view-ios__scroll"
          ref={scrollContainerRef}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          style={{
            transform: `translateY(${isPulling ? 60 : pullY}px)`,
            transition: isPulling || pullY === 0 ? 'transform 0.3s ease-out' : 'none',
            zIndex: 2, position: 'relative'
          }}
        >
          <div className="ios-header-row"><div className="ios-screen-title">Route View</div></div>
          <div className="ios-trip-subtext">{getSubtextStatus()}</div>

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
              <div className="ios-stat-item"><div className="ios-stat-value">{speedText}</div><div className="ios-stat-label">SPEED</div></div>
              <div className="ios-stat-divider" />
              <div className="ios-stat-item"><div className="ios-stat-value">{stopsDoneText}</div><div className="ios-stat-label">STOPS</div></div>
            </div>
          )}

          {isIdleMode ? (
            <BusIdleAnimation nextTripTime={tripState?.next_trip_time} isUnscheduled={isUnscheduled} />
          ) : (<div className="ios-timeline-card">
            {stopsToShow.length === 0 ? (
              <div className="ios-empty-state"><div className="ios-empty-text">{getTimelineEmptyMsg()}</div></div>
            ) : (
              <div className={`ios-timeline ios-timeline--status-${globalDelayStatus}`}>
                {stopsToShow.map((stop, idx) => {
                  const isVisited = !!visitedStops[stop.name];
                  const isCurrent = idx === currentNextIdx;
                  const scheduled = (direction === 'forward' ? stop.morning_time : stop.evening_time) || '';
                  const actualTime = arrivalTimes[stop.name] || null;
                  const effectiveLateMins = (lateMins != null && lateMins <= -1 && !isCurrent) ? 0 : lateMins;
                  const displayTime = isVisited ? actualTime || scheduled : effectiveLateMins != null ? computeEstimatedTime(scheduled, effectiveLateMins) || scheduled : scheduled;
                  const isLast = idx === stopsToShow.length - 1;
                  let delayType = 'none';
                  let statusSubtext = 'Scheduled';

                  const isSkipped = !isVisited && isActive && stopsToShow.slice(idx + 1).some(s => visitedStops[s.name]);
                  if (isSkipped) { statusSubtext = 'Skipped'; delayType = 'late'; }
                  else if (isVisited) {
                    if (actualTime && scheduled) {
                      const perStopDiff = parseTimeToMinutes(actualTime) - parseTimeToMinutes(scheduled);
                      const action = idx === 0 ? 'Departed' : 'Arrived';
                      if (perStopDiff > 1) { delayType = 'late'; statusSubtext = `${action} • +${perStopDiff}m late`; }
                      else if (perStopDiff < -1) { delayType = 'ahead'; statusSubtext = `${action} • ${Math.abs(perStopDiff)}m early`; }
                      else { statusSubtext = `${action} on time`; }
                    } else { statusSubtext = idx === 0 ? 'Departed' : 'Arrived'; }
                  } else if (isOnline || (lateMins != null && (tripStatus === 'active' || tripStatus === 'connecting'))) {
                    let usedMl = false;
                    if (mlEtas && mlEtas[stop.id] !== undefined && !isCurrent) {
                      const now = new Date();
                      const currentMins = now.getHours() * 60 + now.getMinutes();
                      const scheduledMins = parseTimeToMinutes(scheduled);
                      if (scheduledMins) {
                        const mlPredictedDelay = Math.round(currentMins + mlEtas[stop.id] - scheduledMins);
                        if (mlPredictedDelay > 1) { delayType = 'late'; statusSubtext = `Predicted • Delayed ${mlPredictedDelay}m`; }
                        else if (mlPredictedDelay < -1) { delayType = 'ahead'; statusSubtext = `Predicted • ${Math.abs(mlPredictedDelay)}m early`; }
                        else { delayType = 'ontime'; statusSubtext = `Predicted • On time`; }
                        usedMl = true;
                      }
                    }
                    if (!usedMl) {
                      if (lateMins >= 1) delayType = 'late';
                      else if (lateMins <= -1) delayType = isCurrent ? 'ahead' : 'ontime';
                      else delayType = 'ontime';

                      if (isCurrent && idx !== 0) {
                        if (delayType === 'late') statusSubtext = `Next Stop • Delayed ${lateMins}m`;
                        else if (delayType === 'ahead') statusSubtext = `Next Stop • ${Math.abs(lateMins)}m ahead`;
                        else statusSubtext = 'Next Stop';
                      } else if (isCurrent && idx === 0) {
                        if (delayType === 'late') statusSubtext = `Scheduled • Delayed ${lateMins}m`;
                        else if (delayType === 'ahead') statusSubtext = `Scheduled • ${Math.abs(lateMins)}m ahead`;
                        else statusSubtext = 'Scheduled';
                      } else {
                        if (delayType === 'late') statusSubtext = `Delayed ${lateMins}m`;
                        else statusSubtext = 'On time';
                      }
                    }
                  }
                  return (
                    <div key={stop.id || idx} id={isCurrent ? 'active-stop-row' : undefined} className="ios-timeline-row">
                      <div className="ios-time-col">
                        <div className="ios-sched-time">{scheduled}</div>
                        {isVisited || isOnline || (lateMins != null && (tripStatus === 'active' || tripStatus === 'connecting')) ? (
                          <div className={`ios-live-time ${delayType === 'late' ? 'ios-time-late' : ''}`}>{displayTime}</div>
                        ) : (<div className="ios-live-time-muted">—</div>)}
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
                        <div className={`ios-stop-subtext ${isCurrent ? 'ios-stop-subtext-current' : ''} ${delayType === 'late' ? 'ios-stop-subtext-late' : ''} ${(delayType === 'ahead' || delayType === 'ontime') && !isVisited && isOnline && !isCurrent ? 'ios-stop-subtext-ahead' : ''}`}>{statusSubtext}</div>
                      </div>
                    </div>
                  );
                })}
                {isActive && animatedBus && stopsToShow.length > 0 && (
                  <div className="ios-bus-badge-float" style={{ top: badgeTop }}><div className="ios-bus-badge-circle"></div></div>
                )}
              </div>
            )}
          </div>)}

          <div className="ios-section-header-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="ios-section-title" style={{ margin: 0 }}>Map View</div>
            {formatLastUpdated() ? (
              <div className="ios-last-updated" style={{ margin: 0 }}>Last updated: {formatLastUpdated()}</div>
            ) : null}
          </div>

          <div className={isIdleMode ? 'ios-map-card ios-map-card--idle' : 'ios-map-card'} onClick={() => navigate('/map')}>
            <BusMapView
              interactive={false}
              center={mapVp.center}
              zoom={mapVp.zoom}
              defaultPitch={50}
              busCoord={animatedBus}
              isLive={isLive}
              headingStatus={headingStatus}
              stops={stopsToShow}
              plannedCoords={!isActive ? [] : (direction === 'reverse' ? [...plannedCoords].reverse() : plannedCoords)}
              style={{ height: '100%' }}
            />
            <div className="ios-map-overlay"><div className="ios-tap-pill">Tap to view full map</div></div>
          </div>
          <div style={{ height: 20 }} />
        </div>
      </div>
    </>
  );
}
