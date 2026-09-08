/**
 * ProfileSetup.jsx — DUK Bus Tracker PWA
 * Screen 1: Name + email + boarding stop selection.
 * Shows OTP modal as a popup on the same screen after submit.
 */
import React, { useState, useEffect, useRef, useContext, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronUp, ChevronRight } from 'lucide-react';
import { register, getStops } from '../api';
import { EMAIL_DOMAINS } from '../timetable';
import { useToast, SplashContext } from '../App';
import OtpModal from '../components/OtpModal';

export default function ProfileSetup() {
  const navigate = useNavigate();
  const showToast = useToast();
  const { setSplashReady } = useContext(SplashContext);

  useEffect(() => {
    // This page doesn't depend on async data for its initial render
    setSplashReady();
  }, [setSplashReady]);

  const [name, setName] = useState('');
  const [emailPrefix, setEmailPrefix] = useState('');
  const [domain, setDomain] = useState(EMAIL_DOMAINS[0]);
  const [stops, setStops] = useState([]);
  const [selectedStop, setSelectedStop] = useState(null);
  const [dropOpen, setDropOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [otpOpen, setOtpOpen] = useState(false);
  const dropRef = useRef(null);
  const domainDropRef = useRef(null);
  const [domainDropOpen, setDomainDropOpen] = useState(false);

  const [stopsError, setStopsError] = useState(false);

  const fetchStops = useCallback(() => {
    setStopsError(false);
    getStops()
      .then(data => {
        if (Array.isArray(data) && data.length) {
          setStops(data);
        } else {
          setStopsError(true);
        }
      })
      .catch(() => { setStopsError(true); });
  }, []);

  useEffect(() => {
    fetchStops();
  }, [fetchStops]);

  // Close both dropdowns on outside click
  useEffect(() => {
    const handler = (e) => {
      if (dropRef.current && !dropRef.current.contains(e.target)) setDropOpen(false);
      if (domainDropRef.current && !domainDropRef.current.contains(e.target)) setDomainDropOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Validation
  const nameOk = name.trim().length > 0;
  const prefixOk = emailPrefix.trim().length > 0 && !/[^a-zA-Z0-9._-]/.test(emailPrefix.trim());
  const emailErr = emailPrefix.length > 0 && !prefixOk;
  const stopOk = selectedStop !== null;
  const formOk = nameOk && prefixOk && stopOk;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!formOk || submitting) return;
    setSubmitting(true);
    const email = `${emailPrefix.trim()}${domain}`;
    try {
      await register(name.trim(), email, selectedStop?.id);
      setOtpOpen(true); // show OTP modal — no navigation
    } catch (err) {
      showToast(err.message || 'Registration failed. Try again.', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="setup-screen">

      {/*  DUK Logo  */}
      <div className="setup-logo-wrap">
        <img
          src="/duk-logo.png"
          alt="Digital University Kerala"
          className="setup-duk-logo"
          onError={e => { e.target.style.display = 'none'; }}
        />
      </div>

      {/*  Card  */}
      <form className="setup-card" onSubmit={handleSubmit} noValidate>

        <div className="setup-card__header">
          <h1 className="setup-card__title">Set up your profile</h1>
          <p className="setup-card__subtitle">Use your university email to verify access</p>
        </div>

        {/* Full Name */}
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

        {/* University Email */}
        <div className="setup-field">
          <label className="setup-label" htmlFor="email-prefix">University Email</label>
          <div className={`setup-email-row${emailErr ? ' setup-input--error' : ''}`}>
            <input
              id="email-prefix"
              className="setup-input setup-input--email"
              type="text"
              placeholder="yourname"
              value={emailPrefix}
              onChange={e => setEmailPrefix(e.target.value)}
              autoCapitalize="none"
              autoComplete="username"
              inputMode="email"
            />
            {/* Custom domain dropdown */}
            <div className="setup-domain-dropdown" ref={domainDropRef}>
              <button
                type="button"
                className={`setup-domain-btn${domainDropOpen ? ' setup-domain-btn--open' : ''}`}
                onClick={() => setDomainDropOpen(o => !o)}
                aria-haspopup="listbox"
                aria-expanded={domainDropOpen}
              >
                <span>{domain}</span>
                <span className="setup-domain-chevron">
                  {domainDropOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                </span>
              </button>

              {domainDropOpen && (
                <ul className="setup-domain-list" role="listbox">
                  {EMAIL_DOMAINS.map(d => (
                    <li
                      key={d}
                      role="option"
                      aria-selected={domain === d}
                      className={`setup-domain-item${domain === d ? ' setup-domain-item--active' : ''}`}
                      onClick={() => { setDomain(d); setDomainDropOpen(false); }}
                    >
                      <span>{d}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
          {emailErr && (
            <span className="setup-error">Only letters, numbers, dots, underscores and hyphens.</span>
          )}
        </div>

        {/* Boarding Stop */}
        <div className="setup-field">
          <label className="setup-label">Boarding Stop</label>
          <div className="setup-dropdown" ref={dropRef}>
            {/* Stops dropdown — NetworkGate guarantees we're online so stops will always load */}
            {stops.length === 0 ? (
              stopsError ? (
                <div className="setup-input" style={{ color: 'var(--danger)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  Failed to load stops.
                  <button type="button" onClick={fetchStops} style={{ color: 'var(--mint-dark)', fontWeight: 600 }}>Retry</button>
                </div>
              ) : (
                <div className="setup-input" style={{ color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 14, height: 14, border: '2px solid currentColor', borderTopColor: 'transparent', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
                  Fetching stops…
                </div>
              )
            ) : (
              <>
                <button
                  type="button"
                  className={`setup-input setup-dropdown-btn${dropOpen ? ' setup-dropdown-btn--open' : ''}`}
                  onClick={() => setDropOpen(o => !o)}
                  aria-haspopup="listbox"
                  aria-expanded={dropOpen}
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
                  <ul className="setup-dropdown-list" role="listbox">
                    {stops.map(stop => (
                      <li
                        key={stop.id}
                        role="option"
                        aria-selected={selectedStop?.id === stop.id}
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

        {/* Submit */}
        <button
          type="submit"
          id="continue-btn"
          className="setup-submit-btn"
          disabled={!formOk || submitting}
        >
          {submitting
            ? 'Sending…'
            : <><span >Send Verification Code</span><ChevronRight size={16} /></>
          }
        </button>

      </form>

      {/* Developed by CAN Lab */}
      <div style={{ marginTop: '16px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '6px', opacity: 0.7 }}>
        <span style={{ fontSize: '10px', fontWeight: '700', color: 'var(--med-gray)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Developed by</span>
        <img src="/canlab.png" alt="CAN Lab" style={{ height: '65px', objectFit: 'contain' }} />
      </div>

      {/* OTP bottom-sheet modal */}
      <OtpModal
        isOpen={otpOpen}
        email={`${emailPrefix.trim()}${domain}`}
        name={name.trim()}
        boardingStop={selectedStop}
        onClose={() => setOtpOpen(false)}
      />

    </div>
  );
}
