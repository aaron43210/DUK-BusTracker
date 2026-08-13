/**
 * App.jsx — DUK Bus Tracker PWA
 * Root component: Router, Auth gate, Toast context, Offline detection.
 */
import React, { useState, useCallback, createContext, useContext, useEffect, useRef } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
} from 'react-router-dom';

import { getToken } from './storage';
import { ToastContainer } from './components/Toast';
import { refreshFcmToken, onForegroundMessage } from './firebase';

import ProfileSetup from './pages/ProfileSetup';
import OtpVerify from './pages/OtpVerify';
import RouteView from './pages/RouteView';
import MapFull from './pages/MapFull';
import Settings from './pages/Settings';
import Help from './pages/Help';

// ── Toast context ──────────────────────────────────────────────────────────
export const ToastContext = createContext(null);
export function useToast() { return useContext(ToastContext); }

// ── Notification context — live in-app push messages ───────────────────────
export const NotificationContext = createContext({ notifications: [], addNotification: () => {}, clearNotifications: () => {} });
export function useNotifications() { return useContext(NotificationContext); }

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

// ── Network Gate (full-screen offline wall) ───────────────────────────────
function NetworkGate({ children }) {
  const [offline, setOffline] = useState(!navigator.onLine);

  useEffect(() => {
    const goOnline = () => setOffline(false);
    const goOffline = () => setOffline(true);
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
    };
  }, []);

  if (!offline) return children;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: '#f9fafb',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      padding: '32px 24px', textAlign: 'center',
    }}>
      <div style={{
        background: '#fff', borderRadius: '16px',
        padding: '40px 28px', maxWidth: '360px', width: '100%',
        boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
      }}>
        <div style={{ marginBottom: '24px' }}>
          <img src="/duk-logo.png" alt="DUK Logo" style={{ height: '48px', objectFit: 'contain' }} />
        </div>
        <h2 style={{ fontSize: '20px', fontWeight: '700', marginBottom: '10px', color: '#111' }}>
          No Internet Connection
        </h2>
        <p style={{ fontSize: '14px', color: '#6b7280', lineHeight: '1.6', marginBottom: '28px' }}>
          The bus tracker needs a live connection to show real-time bus locations and schedules.
          Please check your Wi-Fi or mobile data.
        </p>
        <button
          onClick={() => setOffline(!navigator.onLine)}
          style={{
            width: '100%', padding: '14px',
            background: '#111', color: '#fff',
            border: 'none', borderRadius: '10px',
            fontSize: '15px', fontWeight: '600', cursor: 'pointer',
          }}
        >
          Retry
        </button>
      </div>
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
        <img src="/duk-logo.png" alt="DUK Logo" style={{ width: '280px', marginBottom: '24px', objectFit: 'contain' }} />
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
        <img src="/duk-logo.png" alt="DUK Logo" style={{ width: '280px', marginBottom: '24px', objectFit: 'contain' }} />
        <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '12px' }}>Mobile App Only</h2>
        <p style={{ color: '#666', fontSize: '16px', lineHeight: '1.5', marginBottom: '32px' }}>
          The Bus Tracker is only available as a mobile application.<br /><br />
          Please open this link on your <b>iPhone or iPad</b> to install it.
        </p>
      </div>
    );
  }

  // If on iPhone but opened in regular Safari (not installed)
  if (isIos && !isStandalone && !isLocalhost) {
    return (
      <div className="app-shell" style={{ justifyContent: 'center', alignItems: 'center', padding: '24px', textAlign: 'center', background: '#fff' }}>
        <img src="/duk-logo.png" alt="DUK Logo" style={{ width: '280px', marginBottom: '24px', objectFit: 'contain' }} />
        <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '12px' }}>Install Required</h2>
        <p style={{ color: '#666', fontSize: '16px', lineHeight: '1.5', marginBottom: '32px' }}>
          This app is designed to run natively on your iPhone. <br /><br />
          Tap the <b>Share</b> icon below and select <br /><b>"Add to Home Screen"</b> to use it.
        </p>
        <div style={{ fontSize: '40px', marginTop: '16px', animation: 'floatIn 1s infinite alternate' }}>👇</div>
      </div>
    );
  }

  return children;
}

// ── Splash Context & Provider (Dynamic App Load) ─────────────────────────
export const SplashContext = createContext({ setSplashReady: () => {}, isAppReady: false, showSplash: true });

function SplashProvider({ children }) {
  const [showSplash, setShowSplash] = useState(true);
  const [isAppReady, setIsAppReady] = useState(false);

  const setSplashReady = () => {
    setIsAppReady(true);
    setTimeout(() => {
      setShowSplash(false);
    }, 500); // minimum display time
  };

  useEffect(() => {
    const fallbackTimer = setTimeout(() => {
      setIsAppReady(true);
      setShowSplash(false);
    }, 8000);
    return () => clearTimeout(fallbackTimer);
  }, []);

  return (
    <SplashContext.Provider value={{ setSplashReady, isAppReady, showSplash }}>
      {children}
    </SplashContext.Provider>
  );
}

// ── Notification Provider — wraps app and receives foreground FCM messages ─
function NotificationProvider({ children }) {
  const [notifications, setNotifications] = useState([]);

  const addNotification = useCallback((notif) => {
    setNotifications((prev) => [
      { id: Date.now(), ...notif, time: new Date() },
      ...prev,
    ].slice(0, 50)); // keep max 50
  }, []);

  const clearNotifications = useCallback(() => setNotifications([]), []);

  const showToast = useToast();

  // Listen for foreground FCM messages and push them into the drawer
  useEffect(() => {
    const unsub = onForegroundMessage((msg) => {
      addNotification(msg);
      // Show brief toast so user sees it actively
      if (msg && msg.title) {
        showToast(msg.title, 'info');
      }
    });
    return unsub;
  }, [addNotification, showToast]);

  return (
    <NotificationContext.Provider value={{ notifications, addNotification, clearNotifications }}>
      {children}
    </NotificationContext.Provider>
  );
}

// ── App Shell wrapper ──────────────────────────────────────────────────────
function AppShell() {
  const { showSplash } = useContext(SplashContext);

  // Silently refresh the FCM device token on every app load
  useEffect(() => {
    if (getToken()) refreshFcmToken().catch(() => {});
  }, []);

  return (
    <div className="app-shell">
      <Routes>
        {/* Public */}
        <Route path="/" element={<RedirectIfAuthed><ProfileSetup /></RedirectIfAuthed>} />
        <Route path="/otp" element={<OtpVerify />} />

        {/* Protected */}
        <Route path="/route"    element={<RequireAuth><RouteView /></RequireAuth>} />
        <Route path="/map"      element={<RequireAuth><MapFull /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
        <Route path="/help"     element={<RequireAuth><Help /></RequireAuth>} />

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      {showSplash && (
        <div className="splash-screen" style={{ position: 'absolute', inset: 0, zIndex: 99999 }}>
          <div className="splash-overlay" />
          <div className="splash-content">
            <div className="splash-loader"></div>
            <div className="splash-text">Loading...</div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Root ───────────────────────────────────────────────────────────────────
export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <NotificationProvider>
          <SplashProvider>
            <DeviceGate>
              <NetworkGate>
                <AppShell />
              </NetworkGate>
            </DeviceGate>
          </SplashProvider>
        </NotificationProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
