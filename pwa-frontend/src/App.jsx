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

// ── Root ───────────────────────────────────────────────────────────────────
export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AppShell />
      </ToastProvider>
    </BrowserRouter>
  );
}
