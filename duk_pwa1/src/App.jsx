/**
 * App.jsx — DUK Bus Tracker PWA 2
 * Universal device support. Unified Route+Map toggle view.
 */
import React, { useState, useCallback, createContext, useContext, useEffect, lazy, Suspense } from 'react';
import { HashRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';

import { getToken, clearSession, getUser } from './storage';
import { clearApiToken, getMyNotifications, proactiveRefresh } from './api';
import { ToastContainer } from './components/Toast';
import { AppProvider } from './context';

import './liquid-glass.css';

import ProfileSetup from './pages/ProfileSetup';

// Login is the first screen for new users, so it ships in the main chunk.
// Everything behind auth is split out and precached by the service worker.
const RouteView = lazy(() => import('./pages/RouteView'));
const Settings = lazy(() => import('./pages/Settings'));
const Help = lazy(() => import('./pages/Help'));
const OnboardingModal = lazy(() => import('./components/OnboardingModal'));

const loadFirebase = () => import('./firebase');

// Firebase isn't needed for the first paint, so wait for the window load
// event before fetching it rather than competing with the page's own assets.
const loadFirebaseAfterLoad = () => new Promise(resolve => {
  if (document.readyState === 'complete') resolve();
  else window.addEventListener('load', resolve, { once: true });
}).then(loadFirebase);

// Toast context
export const ToastContext = createContext(null);
export function useToast() { return useContext(ToastContext); }

// Notification context
export const NotificationContext = createContext({ notifications: [], addNotification: () => { }, clearNotifications: () => { }, hasUnread: false, markRead: () => { }, checkNotifications: async () => { } });
export function useNotifications() { return useContext(NotificationContext); }

// ViewMode context — controls Route vs Map tab from anywhere (e.g. DrawerMenu)
export const ViewModeContext = createContext({ viewMode: 'route', setViewMode: () => { } });
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
export const UIContext = createContext({ isUI2: false });
export function useUI() { return useContext(UIContext); }

function UIProvider({ children }) {
  const isUI2 = false;

  useEffect(() => {
    if (isUI2) {
      document.body.classList.add('theme-liquid-glass');
    } else {
      document.body.classList.remove('theme-liquid-glass');
    }
  }, [isUI2]);

  return (
    <UIContext.Provider value={{ isUI2 }}>
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
          <img src="./duk-logo.webp" alt="DUK Logo" width={285} height={136} style={{ height: '48px', width: 'auto', objectFit: 'contain' }} />
        </div>
        <h2 style={{ fontSize: '20px', fontWeight: '700', marginBottom: '10px', color: '#111' }}>
          No Internet Connection
        </h2>
        <p style={{ fontSize: '14px', color: '#6b7280', lineHeight: '1.6', marginBottom: '28px' }}>
          The bus tracker needs a live connection to show real-time bus locations and schedules.
        </p>
        <button
          onClick={() => { if (navigator.onLine) setOffline(false); }}
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

  // Hide the splash as soon as the page says it's ready — no minimum display
  // time, since holding it up delays the first real paint (LCP). Stable
  // identity so pages that list it as an effect dependency don't re-run.
  const setSplashReady = useCallback(() => {
    setIsAppReady(true);
    setShowSplash(false);
  }, []);

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
  // Re-render on navigation so logging in or out re-evaluates the token below.
  useLocation();
  const isAuthed = !!getToken();

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

  // Foreground push only matters once there's an FCM token, i.e. after login,
  // so logged-out visitors never download Firebase.
  useEffect(() => {
    if (!isAuthed) return;
    let unsub = () => { };
    let cancelled = false;
    loadFirebaseAfterLoad().then(({ onForegroundMessage }) => {
      if (cancelled) return;
      unsub = onForegroundMessage((msg) => {
        addNotification(msg);
        if (msg && msg.title) showToast(msg.title, 'info');
      });
    }).catch(() => { });
    return () => { cancelled = true; unsub(); };
  }, [isAuthed, addNotification, showToast]);

  const checkNotifications = useCallback(() => {
    const token = getToken();
    if (!token) return Promise.resolve();
    return getMyNotifications().then(data => {
      if (data && data.notifications && data.notifications.length > 0) {
        const lastRead = parseInt(localStorage.getItem('last_read_time') || '0', 10);
        const hasNew = data.notifications.some(n => new Date(n.time).getTime() > lastRead);
        if (hasNew) setHasUnread(true);
      }
    }).catch(() => { });
  }, []);

  useEffect(() => {
    let intervalId = null;

    const startPolling = () => {
      if (!intervalId) {
        checkNotifications();
        intervalId = setInterval(checkNotifications, 15000);
      }
    };

    const stopPolling = () => {
      if (intervalId) {
        clearInterval(intervalId);
        intervalId = null;
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        startPolling();
      } else {
        stopPolling();
      }
    };

    if (document.visibilityState === 'visible') {
      startPolling();
    }

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      stopPolling();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
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
      clearSession();  // wipe access + refresh token + cached user, not just the access token
      showToast('Your session expired. Please log in again.', 'error');
      window.location.replace(window.location.pathname);
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

function AppShell() {
  const { showSplash } = useContext(SplashContext);
  const [user, setUser] = useState(() => getUser());

  // Track localStorage changes in case other tabs/components update it
  useEffect(() => {
    const handleStorage = () => setUser(getUser());
    window.addEventListener('storage', handleStorage);
    // Custom event to trigger re-check when we update it in the same window
    const handleLocalUpdate = () => handleStorage();
    window.addEventListener('user-updated', handleLocalUpdate);
    return () => {
      window.removeEventListener('storage', handleStorage);
      window.removeEventListener('user-updated', handleLocalUpdate);
    };
  }, []);

  const needsOnboarding = getToken() && user && (
    !user.name ||
    user.name === user.email.split('@')[0] ||
    !user.boarding_stop_id
  );

  useEffect(() => {
    if (getToken()) {
      loadFirebaseAfterLoad().then(({ refreshFcmToken }) => refreshFcmToken()).catch(() => { });
    }
  }, []);

  // Proactive token refresh on app resume — runs when the user brings the app
  // to the foreground (tab visible again, phone unlocked). This ensures the
  // access token is always fresh before any request fires, eliminating the
  // reactive 401→refresh race that causes logouts on mobile networks.
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && getToken()) {
        proactiveRefresh().catch(() => {});
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    // Also check on mount (cold start / PWA launch from home screen)
    if (getToken()) proactiveRefresh().catch(() => {});
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  return (
    <div className="app-shell">
      <ApiErrorHandler />
      <Suspense fallback={null}>
        <Routes>
          <Route path="/" element={<RedirectIfAuthed><ProfileSetup /></RedirectIfAuthed>} />
          <Route path="/route" element={<RequireAuth><RouteView /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
          <Route path="/help" element={<RequireAuth><Help /></RequireAuth>} />
          {/* Redirect old /map deep links to /route */}
          <Route path="/map" element={<Navigate to="/route" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>

      {/* Own boundary so loading the modal chunk doesn't blank the page behind it */}
      {needsOnboarding && (
        <Suspense fallback={null}>
          <OnboardingModal
            user={user}
            onComplete={(updatedUser) => {
              setUser(updatedUser);
              window.dispatchEvent(new Event('user-updated'));
            }}
          />
        </Suspense>
      )}

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
      <HashRouter>
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
      </HashRouter>
    </UIProvider>
  );
}
