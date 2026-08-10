/**
 * App.jsx — DUK Bus Tracker PWA
 * Root component: Router, Auth gate, Toast context, Offline detection.
 */
import React, { useState, useCallback, createContext, useContext, useEffect } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
} from 'react-router-dom';

import { getToken } from './storage';
import { ToastContainer } from './components/Toast';

import ProfileSetup from './pages/ProfileSetup';
import OtpVerify    from './pages/OtpVerify';
import RouteView    from './pages/RouteView';
import MapFull      from './pages/MapFull';
import Settings     from './pages/Settings';

// ── Toast context ──────────────────────────────────────────────────────────
export const ToastContext = createContext(null);
export function useToast() { return useContext(ToastContext); }

function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const showToast = useCallback((msg, type = 'success') => {
    const id = Date.now();
    setToasts(prev => {
      if (prev.some(t => t.msg === msg)) return prev;
      return [...prev, { id, msg, type }];
    });
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 5000);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={showToast}>
      {children}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </ToastContext.Provider>
  );
}

// ── Auth gate ──────────────────────────────────────────────────────────────
function RequireAuth({ children }) {
  const token = getToken();
  const location = useLocation();
  if (!token) {
    return <Navigate to="/" state={{ from: location }} replace />;
  }
  return children;
}

function RedirectIfAuthed({ children }) {
  const token = getToken();
  if (token) return <Navigate to="/route" replace />;
  return children;
}

// ── Offline detection ──────────────────────────────────────────────────────
function OfflineBanner() {
  const [offline, setOffline] = useState(!navigator.onLine);
  useEffect(() => {
    const on  = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener('online',  on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);
  if (!offline) return null;
  return <div className="offline-banner">⚡ You're offline — live data unavailable</div>;
}

// ── App Shell wrapper ──────────────────────────────────────────────────────
function AppShell() {
  return (
    <div className="app-shell">
      <OfflineBanner />
      <Routes>
        {/* Public */}
        <Route path="/" element={<RedirectIfAuthed><ProfileSetup /></RedirectIfAuthed>} />
        <Route path="/otp" element={<OtpVerify />} />

        {/* Protected */}
        <Route path="/route"    element={<RequireAuth><RouteView /></RequireAuth>} />
        <Route path="/map"      element={<RequireAuth><MapFull /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}

// ── Device Gate (Blocks Android & enforces iOS PWA installation) ────────────
function DeviceGate({ children }) {
  const userAgent = navigator.userAgent || navigator.vendor || window.opera;
  const isAndroid = /Android/i.test(userAgent);
  const isIos = /iPad|iPhone|iPod/.test(userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
  
  if (isAndroid) {
    return (
      <div className="app-shell" style={{ justifyContent: 'center', alignItems: 'center', padding: '24px', textAlign: 'center', background: '#fff' }}>
        <img src="/duk-logo.png" alt="DUK Logo" style={{ width: '120px', marginBottom: '24px', objectFit: 'contain' }} />
        <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '12px' }}>Android App Available</h2>
        <p style={{ color: '#666', fontSize: '15px', lineHeight: '1.5', marginBottom: '32px' }}>
          We noticed you're on an Android device. To get the best experience, please download our dedicated native Android app!
        </p>
        <button 
          style={{
            background: '#2563eb', color: '#fff', border: 'none', borderRadius: '12px',
            padding: '16px 24px', fontSize: '16px', fontWeight: '600', width: '100%'
          }}
          onClick={() => alert('Link to Google Play Store coming soon!')}
        >
          Download Android App
        </button>
      </div>
    );
  }

  // If on Desktop/Laptops (Not iOS, Not Android)
  if (!isIos && !isLocalhost) {
    return (
      <div className="app-shell" style={{ justifyContent: 'center', alignItems: 'center', padding: '24px', textAlign: 'center', background: '#fff' }}>
        <img src="/duk-logo.png" alt="DUK Logo" style={{ width: '120px', marginBottom: '24px', objectFit: 'contain' }} />
        <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '12px' }}>Mobile App Only</h2>
        <p style={{ color: '#666', fontSize: '16px', lineHeight: '1.5', marginBottom: '32px' }}>
          The Bus Tracker is only available as a mobile application.<br/><br/>
          Please open this link on your <b>iPhone or iPad</b> to install it.
        </p>
      </div>
    );
  }

  // If on iPhone but opened in regular Safari (not installed)
  if (isIos && !isStandalone && !isLocalhost) {
    return (
      <div className="app-shell" style={{ justifyContent: 'center', alignItems: 'center', padding: '24px', textAlign: 'center', background: '#fff' }}>
        <img src="/duk-logo.png" alt="DUK Logo" style={{ width: '120px', marginBottom: '24px', objectFit: 'contain' }} />
        <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '12px' }}>Install Required</h2>
        <p style={{ color: '#666', fontSize: '16px', lineHeight: '1.5', marginBottom: '32px' }}>
          This app is designed to run natively on your iPhone. <br/><br/>
          Tap the <b>Share</b> icon below and select <br/><b>"Add to Home Screen"</b> to use it.
        </p>
        <div style={{ fontSize: '40px', marginTop: '16px', animation: 'floatIn 1s infinite alternate' }}>👇</div>
      </div>
    );
  }

  return children;
}

// ── Root ───────────────────────────────────────────────────────────────────
export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <DeviceGate>
          <AppShell />
        </DeviceGate>
      </ToastProvider>
    </BrowserRouter>
  );
}
