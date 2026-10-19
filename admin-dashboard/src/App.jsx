// src/App.jsx
// ─────────────────────────────────────────────────────────────────────────────
// Root component — wires together:
//   1. React Router (client-side page navigation)
//   2. Auth gate (shows Login if no token, shows Dashboard layout if logged in)
//   3. Toast context (global notification system shared by all pages)
// ─────────────────────────────────────────────────────────────────────────────

import React, { useState, useCallback, createContext, useContext, useEffect, lazy, Suspense } from 'react';
// useState    → holds state values that trigger re-renders when changed
// useCallback → memoises a function so it doesn't get recreated on every render
// createContext → creates a "context" (global state accessible by any child component)
// useContext  → lets child components read from a context without prop drilling

import {
  BrowserRouter,  // wraps the whole app; reads the browser URL to decide which page to show
  Routes,         // container for <Route> definitions
  Route,          // maps a URL path to a component
  NavLink,        // like <a> but adds an "active" CSS class automatically on the current page
  useNavigate,    // hook to imperatively navigate (used in logout button)
  Navigate,       // hook to redirect unknown paths
} from 'react-router-dom';

import { setToken, clearToken, getToken, logout } from './api.js'; // session management from our API module



import dukLogo from './assets/duk_logo.webp';
import canlabLogo from './assets/canlab.webp';

// Login and Maintenance are tiny and can be the first screen, so they stay in
// the main bundle. Dashboard pages are split per route: a visit to /trips only
// downloads the Trips chunk instead of every page up front.
import Login from './pages/Login.jsx';
import Maintenance from './pages/Maintenance.jsx';
const Dashboard = lazy(() => import('./pages/Dashboard.jsx'));
const Trips = lazy(() => import('./pages/Trips.jsx'));
const Stops = lazy(() => import('./pages/Stops.jsx'));
const Broadcast = lazy(() => import('./pages/Broadcast.jsx'));
const Suggestions = lazy(() => import('./pages/Suggestions.jsx'));
const RouteHistory = lazy(() => import('./pages/RouteHistory.jsx'));

// ── Toast context ─────────────────────────────────────────────────────────────
// createContext() creates a "channel" that any component in the tree can subscribe to.
// We use it to give every page access to the showToast() function without passing it
// as a prop through every intermediate component ("prop drilling").
export const ToastContext = createContext(null);

// useToast: custom hook — lets any component call useToast() to get showToast
export function useToast() {
  return useContext(ToastContext); // reads the nearest ToastContext.Provider above it
}

// ── Toast state + provider ────────────────────────────────────────────────────
function ToastProvider({ children }) {
  // toasts: array of { id, msg, type } objects; new ones are appended, old ones removed
  const [toasts, setToasts] = useState([]);

  // showToast: adds a toast to the array; auto-removes it after 5 seconds
  // useCallback so the function reference stays stable
  const showToast = useCallback((msg, type = 'success') => {
    const id = Date.now(); // unique id using timestamp

    setToasts(prev => {
      // If a toast with the exact same message is already visible, ignore the new one
      if (prev.some(t => t.msg === msg)) return prev;
      // Otherwise, append the new toast
      return [...prev, { id, msg, type }];
    });

    setTimeout(() => {
      // After 5 seconds, remove this specific toast by filtering it out
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 5000); // 5 000 ms = 5 seconds
  }, []); // empty dependency array

  return (
    // ToastContext.Provider makes showToast available to all child components via useToast()
    <ToastContext.Provider value={showToast}>
      {children} {/* render all child components (the whole app) */}

      {/* Toast container: fixed bottom-right corner, renders all active toasts */}
      <div className="toast-container">
        {toasts.map(t => (
          // Each toast gets a unique key so React can efficiently update the list
          <div key={t.id} className={`toast ${t.type}`}>
            {/* Icon: text prefix for success or error */}
            <span className="toast-icon">{t.type === 'success' ? 'Success:' : 'Error:'}</span>
            <span className="toast-msg">{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ── Auth state ────────────────────────────────────────────────────────────────
// The real credential is the HttpOnly `admin_session` cookie set by /admin/login,
// which JavaScript cannot read. We keep only a non-sensitive boolean marker in
// sessionStorage for client-side route gating:
// - Survives a tab reload so admins stay on the dashboard; the cookie is the
//   authority and the server enforces it (a 401/403 clears both and redirects).
// - Cleared explicitly on logout.
function App() {
  const [token, setTokenState] = useState(() => getToken());
  const [isServerDown, setIsServerDown] = useState(false);

  useEffect(() => {
    const handleServerError = () => setIsServerDown(true);
    window.addEventListener('server-error', handleServerError);
    return () => window.removeEventListener('server-error', handleServerError);
  }, []);

  // handleLogin: called by Login.jsx after a successful /admin/login response.
  // The JWT is already in the HttpOnly cookie; `t` is just a truthy marker.
  function handleLogin(t) {
    setToken(t);       // flip the api.js auth flag → gates client-side routing
    setTokenState(t);  // store in React state → triggers re-render to show the dashboard
  }

  // handleLogout: revoke the session server-side (clears cookie + blocklists JTI),
  // then clear local state and navigate back to login.
  function handleLogout() {
    logout();          // best-effort server-side revoke + cookie clear
    clearToken();      // wipe the api.js auth flag
    setTokenState(''); // wipe from React state → triggers re-render to show Login
  }

  return (
    // BrowserRouter reads the current URL and makes it available to child Routes/NavLink
    <BrowserRouter basename={import.meta.env.VITE_ADMIN_BASE_PATH || '/duk/admin2026/'}>
      {/* ToastProvider wraps everything so any page can call showToast() */}
      <ToastProvider>
        {/* Conditional render:
            - If no token → show the Login page (full screen, no sidebar)
            - If token present → show the dashboard shell with sidebar + Routes */}
        {isServerDown ? (
          <Maintenance setServerIsDown={setIsServerDown} />
        ) : !token ? (
          <Login onLogin={handleLogin} />
        ) : (
          <DashboardShell onLogout={handleLogout} />
        )}
      </ToastProvider>
    </BrowserRouter>
  );
}

// ── Dashboard shell ───────────────────────────────────────────────────────────
// Renders the two-column layout: sidebar on the left, page content on the right.
// Only shown when the user is authenticated.
function DashboardShell({ onLogout }) {
  const navigate = useNavigate(); // lets us programmatically change the URL
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // logout: clear token then navigate to root (which triggers the Login page to appear)
  function logout() {
    onLogout();     // clears token in App state
    navigate('/');  // change URL to / (no-op visually since App now shows Login)
  }

  // Closes mobile menu when clicking a nav link
  function handleNavClick() {
    setMobileMenuOpen(false);
  }

  return (
    <div className="shell"> {/* two-column flex container from index.css */}

      {/* Mobile overlay (closes menu when clicked) */}
      <div
        className={`sidebar-overlay ${mobileMenuOpen ? 'mobile-open' : ''}`}
        onClick={() => setMobileMenuOpen(false)}
      ></div>

      {/* ── Sidebar ── */}
      <aside className={`sidebar ${mobileMenuOpen ? 'mobile-open' : ''}`}>

        {/* Brand / logo block */}
        <div className="brand" style={{ padding: '0 8px 20px', justifyContent: 'center' }}>
          <img
            src={dukLogo}
            alt="DUK Logo"
            width={285}
            height={136}
            decoding="async"
            style={{
              width: '100%',       /* Fill the available sidebar width */
              maxWidth: '180px',   /* Don't let it get too huge */
              height: 'auto',      /* Keep original proportions */
              display: 'block'
            }}
          />
        </div>

        {/* Navigation links */}
        <nav className="nav">
          {/* NavLink automatically adds .active class when its path matches the URL */}
          <NavLink to="/" className="nav-item" end onClick={handleNavClick}>
            <span className="nav-icon">▣</span> Dashboard
          </NavLink>
          <NavLink to="/trips" className="nav-item" onClick={handleNavClick}>
            <span className="nav-icon">↻</span> Trip Management
          </NavLink>
          <NavLink to="/stops" className="nav-item" onClick={handleNavClick}>
            <span className="nav-icon">◎</span> Stops &amp; Routes
          </NavLink>
          <NavLink to="/broadcast" className="nav-item" onClick={handleNavClick}>
            <span className="nav-icon">▷</span> Broadcast
          </NavLink>
          <NavLink to="/suggestions" className="nav-item" onClick={handleNavClick}>
            <span className="nav-icon">◆</span> Suggestions
          </NavLink>
          <NavLink to="/route-history" className="nav-item" onClick={handleNavClick}>
            <span className="nav-icon">◷</span> Route History
          </NavLink>
        </nav>

        {/* Footer: server status + logout */}
        <div className="sidebar-footer">
          <div style={{ padding: '0 8px 12px', display: 'flex', justifyContent: 'center' }}>
            <img
              src={canlabLogo}
              alt="CANLab Logo"
              width={250}
              height={141}
              loading="lazy"
              decoding="async"
              style={{ width: '100%', maxWidth: '125px', height: 'auto', display: 'block', transform: 'translateX(-12px)' }}
            />
          </div>
          {/* Logout button — ghost style, full sidebar width */}
          <button
            className="btn btn-ghost btn-sm"
            style={{ width: '100%', justifyContent: 'center', fontSize: '12px' }}
            onClick={logout}
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* ── Main content area ── */}
      <main className="content">
        {/* Mobile Header Toggle (Only visible on small screens via CSS media query) */}
        <div className="mobile-header-row">
          <button className="mobile-menu-btn" aria-label="Open menu" onClick={() => setMobileMenuOpen(true)}>
            ☰
          </button>
          <span style={{ fontWeight: 600, color: 'var(--text-main)' }}>DUK Bus Tracker</span>
        </div>

        {/* Routes: React Router renders whichever component matches the current URL.
            Suspense shows a spinner while a route's chunk downloads. */}
        <Suspense fallback={<div className="loading-center"><div className="spinner"></div></div>}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/trips" element={<Trips />} />
          <Route path="/stops" element={<Stops />} />
          <Route path="/broadcast" element={<Broadcast />} />
          <Route path="/suggestions" element={<Suggestions />} />
          <Route path="/route-history" element={<RouteHistory />} />
          {/* Catch-all: any unknown URL redirects to the dashboard */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}

export default App; // Vite/main.jsx imports this as the root component
