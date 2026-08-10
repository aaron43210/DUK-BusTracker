/**
 * Settings.jsx — DUK Bus Tracker PWA
 * User settings: profile display, boarding stop, trip preference, proximity alerts.
 */
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { User, MapPin, Bell, LogOut } from 'lucide-react';
import TopBar      from '../components/TopBar';
import DrawerMenu  from '../components/DrawerMenu';
import { getUser, saveUser, clearSession } from '../storage';
import { updatePreferences, getStops } from '../api';
import { DEFAULT_BUS_STOPS } from '../timetable';
import { useToast } from '../App';

export default function Settings() {
  const navigate  = useNavigate();
  const showToast = useToast();

  const [drawerOpen,    setDrawerOpen]    = useState(false);
  const [user,          setUser]          = useState(null);
  const [stops,         setStops]         = useState(DEFAULT_BUS_STOPS);
  const [notifEnabled,  setNotifEnabled]  = useState(false);
  const [alertEnabled,  setAlertEnabled]  = useState(false);
  const [alertType,     setAlertType]     = useState('time');
  const [alertValue,    setAlertValue]    = useState(5);
  const [saving,        setSaving]        = useState(false);
  const [boardingEdit,  setBoardingEdit]  = useState(false);

  useEffect(() => {
    const u = getUser();
    setUser(u);
    // Fetch live stops
    getStops()
      .then(data => { if (Array.isArray(data) && data.length) setStops(data); })
      .catch(() => {});
  }, []);

  const currentStopName = stops.find(s => s.id === user?.boarding_stop_id)?.name || '—';

  const handleBoardingChange = async (stop) => {
    setBoardingEdit(false);
    const updated = { ...user, boarding_stop_id: stop.id };
    saveUser(updated);
    setUser(updated);
    try {
      await updatePreferences({ proximity_alert_for: 'source' });
      showToast(`Boarding stop updated to ${stop.name}`, 'success');
    } catch {
      showToast('Preference saved locally.', 'info');
    }
  };

  const handleNotifToggle = async (val) => {
    setNotifEnabled(val);
    setSaving(true);
    try {
      await updatePreferences({ notifications_on: val });
      showToast(val ? 'Notifications enabled' : 'Notifications disabled', 'success');
    } catch {
      showToast('Could not update preference.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleAlertToggle = async (val) => {
    setAlertEnabled(val);
    setSaving(true);
    try {
      await updatePreferences({
        proximity_alert_enabled: val,
        proximity_alert_type:    alertType,
        proximity_alert_value:   alertValue,
      });
      showToast(val ? 'Proximity alert enabled' : 'Proximity alert disabled', 'success');
    } catch {
      showToast('Could not update alert setting.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleLogout = () => {
    clearSession();
    navigate('/', { replace: true });
  };

  return (
    <>
      <TopBar
        showBack
        onBack={() => navigate('/route')}
        title="Settings"
        onHamburger={() => setDrawerOpen(true)}
      />
      <DrawerMenu isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} />

      <div className="screen">
        <div className="settings-screen">
          <h1 className="settings-title">Settings</h1>

          {/* ── Profile ── */}
          <div className="settings-section-label">Profile</div>
          <div className="settings-card">

            <div className="settings-row">
              <div className="settings-row__icon"><User size={18} /></div>
              <div className="settings-row__content">
                <div className="settings-row__label">Name</div>
                <div className="settings-row__value">{user?.name || '—'}</div>
              </div>
            </div>

            <div className="settings-row">
              <div className="settings-row__icon" style={{ fontSize: '16px' }}>✉️</div>
              <div className="settings-row__content">
                <div className="settings-row__label">Email</div>
                <div className="settings-row__value" style={{ fontSize: '13px', wordBreak: 'break-all' }}>
                  {user?.email || '—'}
                </div>
              </div>
            </div>

            <div className="settings-row" style={{ alignItems: 'flex-start' }}>
              <div className="settings-row__icon"><MapPin size={18} /></div>
              <div className="settings-row__content">
                <div className="settings-row__label">Boarding Stop</div>
                {boardingEdit ? (
                  <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {stops.map(stop => (
                      <button
                        key={stop.id}
                        onClick={() => handleBoardingChange(stop)}
                        style={{
                          textAlign: 'left',
                          padding: '8px 12px',
                          borderRadius: 10,
                          fontSize: 14,
                          fontWeight: stop.id === user?.boarding_stop_id ? 700 : 500,
                          background: stop.id === user?.boarding_stop_id ? 'var(--mint-lighter)' : 'var(--bg-gray)',
                          color: stop.id === user?.boarding_stop_id ? 'var(--mint-text)' : 'var(--black)',
                        }}
                      >
                        {stop.name}
                      </button>
                    ))}
                    <button
                      onClick={() => setBoardingEdit(false)}
                      style={{ color: 'var(--med-gray)', fontSize: 13, marginTop: 4 }}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
                    <div className="settings-row__value">{currentStopName}</div>
                    <button
                      onClick={() => setBoardingEdit(true)}
                      style={{
                        fontSize: 12, fontWeight: 600, color: 'var(--mint-dark)',
                        textDecoration: 'underline',
                      }}
                    >
                      Change
                    </button>
                  </div>
                )}
              </div>
            </div>

          </div>

          {/* ── Preferences ── */}
          <div className="settings-section-label">Preferences</div>
          <div className="settings-card">

            <div className="settings-row">
              <div className="settings-row__icon">
                {tripType === 'Morning' ? <Sun size={18} /> : <Moon size={18} />}
              </div>
              <div className="settings-row__content">
                <div className="settings-row__label">Default Trip</div>
                <div style={{ marginTop: 6 }}>
                  <div className="trip-toggle">
                    <button
                      className={`trip-toggle__btn${tripType === 'Morning' ? ' trip-toggle__btn--active' : ''}`}
                      onClick={() => setTripType('Morning')}
                    >Morning</button>
                    <button
                      className={`trip-toggle__btn${tripType === 'Evening' ? ' trip-toggle__btn--active' : ''}`}
                      onClick={() => setTripType('Evening')}
                    >Evening</button>
                  </div>
                </div>
              </div>
            </div>

          </div>

          {/* ── Notifications ── */}
          <div className="settings-section-label">Notifications</div>
          <div className="settings-card">

            <div className="settings-row">
              <div className="settings-row__icon"><Bell size={18} /></div>
              <div className="settings-row__content">
                <div className="settings-row__label">Push Notifications</div>
                <div className="settings-row__value">{notifEnabled ? 'Enabled' : 'Disabled'}</div>
              </div>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={notifEnabled}
                  onChange={e => handleNotifToggle(e.target.checked)}
                  disabled={saving}
                />
                <span className="toggle__slider" />
              </label>
            </div>

            <div className="settings-row">
              <div className="settings-row__icon">📍</div>
              <div className="settings-row__content">
                <div className="settings-row__label">Proximity Alert</div>
                <div className="settings-row__value">
                  {alertEnabled
                    ? `${alertValue} ${alertType === 'time' ? 'min' : alertType === 'distance' ? 'm' : 'stops'} before arrival`
                    : 'Disabled'
                  }
                </div>
              </div>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={alertEnabled}
                  onChange={e => handleAlertToggle(e.target.checked)}
                  disabled={saving}
                />
                <span className="toggle__slider" />
              </label>
            </div>

          </div>

          {/* ── App Info ── */}
          <div className="settings-section-label">App</div>
          <div className="settings-card">
            <div className="settings-row">
              <div className="settings-row__icon"><Info size={18} /></div>
              <div className="settings-row__content">
                <div className="settings-row__label">Version</div>
                <div className="settings-row__value">1.0.0 PWA</div>
              </div>
            </div>
          </div>

          {/* Logout */}
          <button
            id="logout-btn"
            className="btn btn--danger"
            onClick={handleLogout}
            style={{ marginTop: 8 }}
          >
            <LogOut size={18} />
            Sign Out
          </button>

        </div>
      </div>
    </>
  );
}
