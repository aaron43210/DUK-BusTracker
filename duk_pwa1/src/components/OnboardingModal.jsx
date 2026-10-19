import React, { useState, useEffect, useRef } from 'react';
import { ChevronDown, ChevronUp, ChevronRight } from 'lucide-react';
import { getStops, apiFetch } from '../api';
import { saveUser, getUser, clearSession } from '../storage';
import { useToast } from '../App';
import { clearApiToken } from '../api';

export default function OnboardingModal({ user, onComplete }) {
  const showToast = useToast();
  const [stops, setStops] = useState([]);
  const [selectedStop, setSelectedStop] = useState(null);
  const [name, setName] = useState(user?.name && user?.name !== user?.email?.split('@')[0] ? user.name : '');
  const [dropOpen, setDropOpen] = useState(false);
  const [stopsError, setStopsError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  
  const dropRef = useRef(null);

  const fetchStops = () => {
    setStopsError(false);
    getStops()
      .then(data => {
        if (Array.isArray(data) && data.length) {
          setStops(data);
        } else {
          setStopsError(true);
        }
      })
      .catch(() => setStopsError(true));
  };

  useEffect(() => {
    fetchStops();
  }, []);

  useEffect(() => {
    const handler = (e) => {
      if (dropRef.current && !dropRef.current.contains(e.target)) setDropOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Trap body scroll
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  const formOk = name.trim().length > 0 && selectedStop !== null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!formOk || submitting) return;
    setSubmitting(true);
    
    try {
      const res = await apiFetch('/api/v1/auth/profile', {
        method: 'PATCH',
        body: JSON.stringify({ name: name.trim(), boarding_stop_id: selectedStop.id })
      });
      saveUser(res.user);
      
      // Request push token here (iOS requires direct user gesture)
      import('../firebase').then(m => m.requestAndSaveFcmToken()).catch(() => {});
      
      onComplete(res.user);
    } catch (err) {
      showToast(err.message || 'Failed to save profile. Try again.', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleLogout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await apiFetch('/api/v1/auth/logout', { method: 'POST', silent: true });
    } catch (e) {} // ignore errors on logout
    clearApiToken();
    clearSession();  // clears access + refresh token + cached user in one call
    window.location.replace(window.location.pathname);
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100000,
      background: 'rgba(240,245,250,0.9)',
      backdropFilter: 'blur(24px) saturate(180%)',
      WebkitBackdropFilter: 'blur(24px) saturate(180%)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', padding: '24px',
    }}>
      <form 
        onSubmit={handleSubmit}
        style={{
          background: 'rgba(255,255,255,0.7)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          borderRadius: '24px', padding: '40px 28px', maxWidth: '400px', width: '100%',
          border: '1px solid rgba(255,255,255,0.8)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.10)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '8px', color: '#111' }}>
            Complete your profile
          </h2>
          <p style={{ fontSize: '14px', color: '#6b7280', lineHeight: '1.6' }}>
            Welcome! Please provide your name and default boarding stop to continue.
          </p>
        </div>

        <div className="setup-field">
          <label className="setup-label" htmlFor="name">Full Name</label>
          <input
            id="name"
            className="setup-input"
            type="text"
            placeholder="Enter your name"
            value={name}
            onChange={e => setName(e.target.value)}
            autoComplete="name"
            maxLength={60}
          />
        </div>

        <div className="setup-field">
          <label className="setup-label">Boarding Stop</label>
          <div className="setup-dropdown" ref={dropRef}>
            {stops.length === 0 ? (
              stopsError ? (
                <div className="setup-input" style={{ color: 'var(--danger)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  Failed to load stops.
                  <button type="button" onClick={fetchStops} style={{ color: 'var(--mint-dark)', fontWeight: 600 }}>Retry</button>
                </div>
              ) : (
                <div className="setup-input" style={{ color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 14, height: 14, border: '2px solid currentColor', borderTopColor: 'transparent', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
                  Fetching stops...
                </div>
              )
            ) : (
              <>
                <button
                  type="button"
                  className={`setup-input setup-dropdown-btn${dropOpen ? ' setup-dropdown-btn--open' : ''}`}
                  onClick={() => setDropOpen(o => !o)}
                >
                  {selectedStop
                    ? <span>{selectedStop.name}</span>
                    : <span className="setup-placeholder">Select your boarding stop</span>
                  }
                  <span className="setup-dropdown-chevron">
                    {dropOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </span>
                </button>

                {dropOpen && (
                  <ul className="setup-dropdown-list">
                    {stops.map(stop => (
                      <li
                        key={stop.id}
                        className={`setup-dropdown-item${selectedStop?.id === stop.id ? ' setup-dropdown-item--active' : ''}`}
                        onClick={() => { setSelectedStop(stop); setDropOpen(false); }}
                      >
                        <span className="setup-dropdown-item__name">{stop.name}</span>
                        {stop.desc && <span className="setup-dropdown-item__desc">{stop.desc}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '24px' }}>
          <button
            type="submit"
            className="setup-submit-btn"
            disabled={!formOk || submitting}
            style={{ width: '100%' }}
          >
            {submitting ? 'Saving...' : 'Save Profile'}
          </button>
          
          <button
            type="button"
            onClick={handleLogout}
            disabled={loggingOut || submitting}
            style={{
              width: '100%', padding: '14px',
              background: 'transparent', color: 'var(--danger)',
              border: 'none', fontSize: '15px', fontWeight: '600',
              cursor: 'pointer'
            }}
          >
            {loggingOut ? 'Logging out...' : 'Logout'}
          </button>
        </div>
      </form>
    </div>
  );
}
