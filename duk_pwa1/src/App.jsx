/**
 * App.jsx — DUK Bus Tracker PWA 2
 * Universal device support. Unified Route+Map toggle view.
 */
import React, { useState, useCallback, createContext, useContext, useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';

import { getToken, clearToken, getUIPref, setUIPref } from './storage';
import { clearApiToken } from './api';
import { ToastContainer } from './components/Toast';
import { AppProvider } from './context';
import useBusTracking from './hooks/useBusTracking';

import './liquid-glass.css';

import ProfileSetup from './pages/ProfileSetup';
import RouteView from './pages/RouteView';
import Settings from './pages/Settings';
import Help from './pages/Help';

const loadFirebase = () => import('./firebase');

// Toast context
export const ToastContext = createContext(null);
export function useToast() { return useContext(ToastContext); }

// Notification context
export const NotificationContext = createContext({ notifications: [], addNotification: () => { }, clearNotifications: () => { }, hasUnread: false, markRead: () => { }, checkNotifications: async () => {} });
export function useNotifications() { return useContext(NotificationContext); }

// ViewMode context — controls Route vs Map tab from anywhere (e.g. DrawerMenu)
export const ViewModeContext = createContext({ viewMode: 'route', setViewMode: () => {} });
export function useViewMode() { return useContext(ViewModeContext); }

function ViewModeProvider({ children }) {
  const [viewMode, setViewMode] = useState('route');
  return (
    <ViewModeContext.Provider value={{ viewMode, setViewMode }}>
      {children}
    </ViewModeContext.Provider>
  );
}

// UI Mode Context
export const UIContext = createContext({ isUI2: false, toggleUI2: () => {} });
export function useUI() { return useContext(UIContext); }

function UIProvider({ children }) {
  const [isUI2, setIsUI2] = useState(true);

  const toggleUI2 = useCallback(() => {}, []);

  useEffect(() => {
    if (isUI2) {
      document.body.classList.add('theme-liquid-glass');
    } else {
      document.body.classList.remove('theme-liquid-glass');
    }
  }, [isUI2]);

  return (
    <UIContext.Provider value={{ isUI2, toggleUI2 }}>
      {children}
    </UIContext.Provider>
  );
}

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

// Auth gate
function RequireAuth({ children }) {
  const token = getToken();
  const location = useLocation();
  if (!token) return <Navigate to="/" state={{ from: location }} replace />;
  return children;
}

function RedirectIfAuthed({ children }) {
  const token = getToken();
  if (token) return <Navigate to="/route" replace />;
  return children;
}

// Network Gate
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

  if (!offline || import.meta.env.DEV) return children;

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(240,245,250,0.85)',
      backdropFilter: 'blur(24px) saturate(180%)',
      WebkitBackdropFilter: 'blur(24px) saturate(180%)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', padding: '32px 24px', textAlign: 'center',
    }}>
      <div style={{
        background: 'rgba(255,255,255,0.6)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        borderRadius: '24px', padding: '40px 28px', maxWidth: '360px', width: '100%',
        border: '1px solid rgba(255,255,255,0.8)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.10)',
      }}>
        <div style={{ marginBottom: '24px' }}>
          <img src="/duk-logo.png" alt="DUK Logo" style={{ height: '48px', objectFit: 'contain' }} />
        </div>
        <h2 style={{ fontSize: '20px', fontWeight: '700', marginBottom: '10px', color: '#111' }}>
          No Internet Connection
        </h2>
        <p style={{ fontSize: '14px', color: '#6b7280', lineHeight: '1.6', marginBottom: '28px' }}>
          The bus tracker needs a live connection to show real-time bus locations and schedules.
        </p>
        <button
          onClick={() => setOffline(!navigator.onLine)}
          style={{
            width: '100%', padding: '14px',
            background: 'rgba(0,122,255,0.85)',
            backdropFilter: 'blur(10px)',
            color: '#fff', border: 'none', borderRadius: '14px',
            fontSize: '15px', fontWeight: '600', cursor: 'pointer',
          }}
        >
          Retry
        </button>
      </div>
    </div>
  );
}

// Splash Context
export const SplashContext = createContext({ setSplashReady: () => { }, isAppReady: false, showSplash: true });

function SplashProvider({ children }) {
  const [showSplash, setShowSplash] = useState(true);
  const [isAppReady, setIsAppReady] = useState(false);
  const mountTime = useRef(Date.now());

  const setSplashReady = () => {
    setIsAppReady(true);
    const elapsed = Date.now() - mountTime.current;
    const delay = Math.max(0, 1000 - elapsed);
    setTimeout(() => setShowSplash(false), delay);
  };

  useEffect(() => {
    const fallbackTimer = setTimeout(() => {
      setIsAppReady(true);
      setShowSplash(false);
    }, 1000);
    return () => clearTimeout(fallbackTimer);
  }, []);

  return (
    <SplashContext.Provider value={{ setSplashReady, isAppReady, showSplash }}>
      {children}
    </SplashContext.Provider>
  );
}

// Notification Provider
function NotificationProvider({ children }) {
  const [notifications, setNotifications] = useState([]);
  const [hasUnread, setHasUnread] = useState(false);
  const showToast = useToast();

  const addNotification = useCallback((notif) => {
    setNotifications((prev) => {
      const notifId = notif.data?.id ? parseInt(notif.data.id, 10) : Date.now();
      if (prev.some(n => n.id === notifId)) return prev;
      return [{ id: notifId, ...notif, time: new Date() }, ...prev].slice(0, 50);
    });
    setHasUnread(true);
  }, []);

  const clearNotifications = useCallback(() => {
    setNotifications([]);
    setHasUnread(false);
  }, []);

  const markRead = useCallback(() => {
    setHasUnread(false);
    localStorage.setItem('last_read_time', Date.now().toString());
  }, []);

  useEffect(() => {
    let unsub = () => {};
    loadFirebase().then(({ onForegroundMessage }) => {
      unsub = onForegroundMessage((msg) => {
        addNotification(msg);
        if (msg && msg.title) showToast(msg.title, 'info');
      });
    }).catch(() => {});
    return () => unsub();
  }, [addNotification, showToast]);

  const checkNotifications = useCallback(() => {
    const token = localStorage.getItem('duk_jwt_token');
    if (!token) return Promise.resolve();
    return import('./api').then(({ getMyNotifications }) => {
      return getMyNotifications().then(data => {
        if (data && data.notifications && data.notifications.length > 0) {
          const lastRead = parseInt(localStorage.getItem('last_read_time') || '0', 10);
          const hasNew = data.notifications.some(n => new Date(n.time).getTime() > lastRead);
          if (hasNew) setHasUnread(true);
        }
      }).catch(() => {});
    }).catch(() => {});
  }, []);

  useEffect(() => {
    checkNotifications();
    const intervalId = setInterval(checkNotifications, 15000);
    return () => clearInterval(intervalId);
  }, [checkNotifications]);

  return (
    <NotificationContext.Provider value={{ notifications, addNotification, clearNotifications, hasUnread, markRead, checkNotifications }}>
      {children}
    </NotificationContext.Provider>
  );
}

// Global API error handler
function ApiErrorHandler() {
  const showToast = useToast();

  useEffect(() => {
    const onUnauthorized = () => {
      clearApiToken();
      clearToken();
      showToast('Your session expired. Please log in again.', 'error');
      window.location.replace('/');
    };
    const onServerError = () => showToast('Server error. Please try again shortly.', 'error');
    const onOffline = () => showToast('Connection lost. Retrying...', 'error');

    window.addEventListener('api:unauthorized', onUnauthorized);
    window.addEventListener('api:server-error', onServerError);
    window.addEventListener('offline', onOffline);

    return () => {
      window.removeEventListener('api:unauthorized', onUnauthorized);
      window.removeEventListener('api:server-error', onServerError);
      window.removeEventListener('offline', onOffline);
    };
  }, [showToast]);

  return null;
}

// App Shell
function AppShell() {
  const { showSplash } = useContext(SplashContext);
  useBusTracking();

  useEffect(() => {
    if (getToken()) {
      loadFirebase().then(({ refreshFcmToken }) => refreshFcmToken()).catch(() => {});
    }
  }, []);

  return (
    <div className="app-shell">
      <ApiErrorHandler />
      <Routes>
        <Route path="/" element={<RedirectIfAuthed><ProfileSetup /></RedirectIfAuthed>} />
        <Route path="/route" element={<RequireAuth><RouteView /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
        <Route path="/help" element={<RequireAuth><Help /></RequireAuth>} />
        {/* Redirect old /map deep links to /route */}
        <Route path="/map" element={<Navigate to="/route" replace />} />
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

// Root
export default function App() {
  return (
    <UIProvider>
      <BrowserRouter>
        <AppProvider>
          <ToastProvider>
            <NotificationProvider>
              <SplashProvider>
                <ViewModeProvider>
                  <NetworkGate>
                    <AppShell />
                  </NetworkGate>
                </ViewModeProvider>
              </SplashProvider>
            </NotificationProvider>
          </ToastProvider>
        </AppProvider>
      </BrowserRouter>
    </UIProvider>
  );
}
