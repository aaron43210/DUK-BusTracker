// src/App.jsx
import { useEffect, useMemo, useState } from 'react';
import MapView        from './components/MapView';
import { useTripState }    from './hooks/useTripState';
import { useBusPosition }  from './hooks/useBusPosition';
import { useStops }        from './hooks/useStops';
import { fmtClock }        from './utils/format';

// Stable date-string — avoids re-computing on every clock tick
const TODAY = new Date().toLocaleDateString('en-GB');

export default function App() {
  const { nextTripTime, isActive, isConnecting } = useTripState();
  const { position }      = useBusPosition(isActive || isConnecting);
  const { stops, geometry } = useStops();

  const [tab,   setTab]   = useState('map');
  const [clock, setClock] = useState(() => fmtClock(new Date()));

  useEffect(() => {
    const id = setInterval(() => setClock(fmtClock(new Date())), 1_000);
    return () => clearInterval(id);
  }, []);

  // O(1) lookup — stops array is already ordered
  const currentLocationName = useMemo(
    () => stops?.[position?.visited_stops ?? 0]?.name ?? 'Central Polytechnic',
    [stops, position?.visited_stops]
  );

  return (
    <div className="layout-root">
      {/* ── Header ── */}
      <header className="site-header">
        <div className="logo-left">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
            <circle cx="12" cy="10" r="3" />
          </svg>
          <div className="logo-text">
            <span className="logo-bus">BUS</span>
            <span className="logo-tracker">TRACKER</span>
          </div>
          <span className="badge-beta">BETA</span>
        </div>
        <div className="logo-right">CAN LAB</div>
      </header>

      {/* ── Status Bar ── */}
      <div className="update-bar">
        <span className="update-dot" />
        LAST UPDATE : {TODAY}, {clock}
      </div>

      {/* ── Tabs ── */}
      <div className="tab-container">
        <div className="tab-switcher">
          <button
            className={`tab-btn${tab === 'route' ? ' active' : ''}`}
            onClick={() => setTab('route')}
          >
            🚌 Route View
          </button>
          <button
            className={`tab-btn${tab === 'map' ? ' active' : ''}`}
            onClick={() => setTab('map')}
          >
            🗺️ Map View
          </button>
        </div>
      </div>

      {/* ── Main Content ── */}
      <main className="main-content">
        {tab === 'map' && (
          <div className="map-view-wrapper">
            <div className="location-card-wrapper">
              <div className="location-card">
                <div className="kicker">CURRENT LOCATION</div>
                <div className="loc-val">{currentLocationName}</div>
              </div>
            </div>
            <div className="map-canvas-container">
              <MapView
                stops={stops}
                geometry={geometry}
                position={position}
              />
            </div>
            <div className="powered-by">POWERED BY <b>CAN LAB</b></div>
          </div>
        )}

        {tab === 'route' && (
          <div className="route-view-wrapper">
            <div className="next-trip-card">
              <div className="next-trip-kicker">Next Scheduled Trip Is At</div>
              <div className="next-trip-time">{nextTripTime ?? '07:30 AM'}</div>
            </div>
            <div className="powered-by">POWERED BY <b>CAN LAB</b></div>
          </div>
        )}
      </main>
    </div>
  );
}
