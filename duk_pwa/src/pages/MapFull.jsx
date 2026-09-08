import React, { useState, useEffect, useRef, useCallback, useContext } from 'react';
import { useNavigate } from 'react-router-dom';
import { Crosshair, Plus, Minus, ArrowLeft } from 'lucide-react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import useBusTracking from '../hooks/useBusTracking';
import BusMapView from '../components/BusMapView';
import { SplashContext } from '../App';
import { useAppContext } from '../context';
import { getUser } from '../storage';
import { haversineDistKm, getMapViewport } from '../timetable';

export default function MapFull() {
  const { locationName } = useBusTracking();
  const navigate = useNavigate();
  const { setSplashReady } = useContext(SplashContext);
  const { state } = useAppContext();
  
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedStop, setSelectedStop] = useState(null);
  const [autoCenter, setAutoCenter] = useState(true);
  const [is3D, setIs3D] = useState(false);
  const mapRef = useRef(null);

  const {
    tripState, stops, plannedCoords, eta, isLive, animatedBus, busPosition
  } = state;

  const user = getUser();

  useEffect(() => {
    if (tripState || stops.length > 0) {
      setSplashReady();
    }
  }, [tripState, stops.length, setSplashReady]);

  const recenter = () => {
    const target = animatedBus || (busPosition?.lon && busPosition?.lat ? [busPosition.lon, busPosition.lat] : null);
    if (target) {
      mapRef.current?.easeTo({ center: target, zoom: 14, duration: 600 });
      setAutoCenter(true);
    } else if (stops.length) {
      const vp = getMapViewport(stops, null);
      mapRef.current?.easeTo({ center: vp.center, zoom: vp.zoom, duration: 600 });
    }
  };

  const zoomIn = () => mapRef.current?.zoomIn({ duration: 300 });
  const zoomOut = () => mapRef.current?.zoomOut({ duration: 300 });
  const toggle3D = () => {
    if (is3D) {
      mapRef.current?.easeTo({ pitch: 0, bearing: 0, duration: 800 });
      setIs3D(false);
    } else {
      mapRef.current?.easeTo({ pitch: 60, bearing: 45, duration: 1000 });
      setIs3D(true);
    }
  };

  const tripName = (typeof tripState?.trip === 'string' ? tripState.trip : '').toLowerCase();
  const direction = tripName.includes('morning') ? 'forward'
    : tripName.includes('evening') ? 'reverse'
      : new Date().getHours() >= 14 ? 'reverse' : 'forward';

  const tripStatus = tripState?.status ?? 'offline';
  const isActive = tripStatus === 'active' || tripStatus === 'connecting';

  const headingStatus = React.useMemo(() => {
    const visitedStops = state.history?.visitedStops || {};
    const stopsFiltered = stops.filter(s => direction === 'forward' ? !!s.morning_time : !!s.evening_time);
    const timeToMins = (t) => {
      if (!t) return 9999;
      const [time, ampm] = t.trim().split(' ');
      let [h, m] = time.split(':').map(Number);
      if (ampm === 'PM' && h !== 12) h += 12;
      if (ampm === 'AM' && h === 12) h = 0;
      return h * 60 + m;
    };
    const stopsToShow = direction === 'forward'
      ? [...stopsFiltered].sort((a, b) => timeToMins(a.morning_time) - timeToMins(b.morning_time))
      : [...stopsFiltered].sort((a, b) => timeToMins(a.evening_time) - timeToMins(b.evening_time));

    const currentNextIdx = stopsToShow.findIndex(s => !visitedStops[s.name]);
    const nextStop = currentNextIdx >= 0 ? stopsToShow[currentNextIdx] : null;

    if (isActive && !tripName.includes('unscheduled') && nextStop) return `Heading to ${nextStop.name.split(',')[0]}`;
    if (isActive && tripName.includes('unscheduled')) return 'Unscheduled Route';
    return locationName ? `At ${locationName}` : 'Not in Service';
  }, [stops, direction, tripName, isActive, state.history?.visitedStops]);

  const StopCard = ({ stop }) => {
    const distKm = animatedBus && stop.lat && stop.lon
      ? haversineDistKm(animatedBus[0], animatedBus[1], Number(stop.lon), Number(stop.lat))
      : null;
    const scheduled = direction === 'forward' ? (stop.morning_time || '—') : (stop.evening_time || '—');
    return (
      <div className="stop-card-overlay">
        <div className="stop-card__handle" />
        <button className="stop-card__close" onClick={() => setSelectedStop(null)}>×</button>
        <div className="stop-card__name">{stop.name}</div>
        <div className="stop-card__row">
          <span className="stop-card__row-icon"></span>
          <div>
            <div className="stop-card__row-label">Scheduled arrival</div>
            <div className="stop-card__row-value">{scheduled}</div>
          </div>
        </div>
        {distKm != null && (
          <div className="stop-card__row">
            <span className="stop-card__row-icon"></span>
            <div>
              <div className="stop-card__row-label">Distance from bus</div>
              <div className="stop-card__row-value">
                {distKm < 1 ? `${Math.round(distKm * 1000)}m` : `${distKm.toFixed(1)}km`}
              </div>
            </div>
          </div>
        )}
        {eta?.eta_minutes != null && stop.id === user?.boarding_stop_id && (
          <div className="stop-card__row">
            <span className="stop-card__row-icon">⏱</span>
            <div>
              <div className="stop-card__row-label">Predicted ETA</div>
              <div className="stop-card__row-value">~{Math.round(eta.eta_minutes)} min</div>
            </div>
          </div>
        )}
      </div>
    );
  };

  const initialVp = getMapViewport(stops, animatedBus);

  return (
    <div className="map-full" style={{ position: 'absolute', inset: 0 }}>
      <button 
        className="map-ctrl-btn" 
        style={{ 
          position: 'absolute', 
          top: 'max(env(safe-area-inset-top, 44px), 16px)', 
          left: '16px', 
          zIndex: 10, 
          borderRadius: '22px', 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center' 
        }} 
        onClick={() => navigate('/route')}
      >
        <ArrowLeft size={24} color="#1f2937" />
      </button>

      <div className="ios-map-toast-container">
        <div className="ios-map-toast">
          <span className="ios-map-toast-text">{headingStatus}</span>
        </div>
      </div>

      <BusMapView
        interactive={true}
        center={initialVp.center}
        zoom={initialVp.zoom}
        busCoord={animatedBus}
        isLive={isLive}
        headingStatus={headingStatus}
        stops={stops}
        plannedCoords={plannedCoords}
        onStopClick={(s) => setSelectedStop(s)}
        onMapLoad={(map) => { mapRef.current = map; map.on('dragstart', () => setAutoCenter(false)); }}
        style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
      />

      <div className="map-controls" style={{ bottom: selectedStop ? '240px' : '24px', position: 'absolute', right: '16px', zIndex: 10 }}>
        <button className={`map-ctrl-btn ${autoCenter ? 'map-ctrl-btn--active' : ''}`} onClick={recenter}>
          <Crosshair size={22} color={autoCenter ? '#2563eb' : '#6b7280'} />
        </button>
        <div className="map-ctrl-divider" />
        <button className="map-ctrl-btn" onClick={zoomIn}>
          <Plus size={20} color="#1f2937" />
        </button>
        <div className="map-ctrl-divider" />
        <button className="map-ctrl-btn" onClick={zoomOut}>
          <Minus size={20} color="#1f2937" />
        </button>
        <div className="map-ctrl-divider" />
        <button className="map-ctrl-btn" onClick={toggle3D} style={{ fontWeight: '800', fontSize: '13px', color: is3D ? '#2563eb' : '#1f2937' }}>
          3D
        </button>
      </div>

      {selectedStop && <StopCard stop={selectedStop} />}
    </div>
  );
}
