/**
 * Settings.jsx — DUK Bus Tracker PWA
 * User settings: profile display, boarding stop, trip preference, proximity alerts.
 */
import React, { useState, useEffect, useContext } from 'react';
import { useNavigate } from 'react-router-dom';
import { User, MapPin, Bell, LogOut, Sun, Moon, Info, Check } from 'lucide-react';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import { getUser, saveUser, clearSession } from '../storage';
import { updatePreferences, getStops } from '../api';
import { EMAIL_DOMAINS } from '../timetable';
import { useToast, SplashContext } from '../App';

const StopSelectModal = ({ isOpen, title, stops, selectedStopId, onSelect, onClose, showNone = false, noneLabel = "Not Set" }) => {
  if (!isOpen) return null;

  return (
    <div className="otp-overlay" role="dialog" aria-modal="true" style={{ zIndex: 2000 }}>
      <div className="otp-backdrop" onClick={onClose} />
      <div className={`otp-sheet ${isOpen ? 'otp-sheet--open' : ''}`} style={{ maxHeight: '80vh', display: 'flex', flexDirection: 'column', paddingBottom: 16 }}>
        <div className="otp-sheet__handle" />
        <div className="otp-sheet__header" style={{ paddingBottom: 16 }}>
          <div className="otp-sheet__title" style={{ fontSize: 20, fontWeight: 700 }}>{title}</div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, paddingRight: 4 }}>
          {showNone && (
            <button
              onClick={() => onSelect(null)}
              style={{
                textAlign: 'left',
                padding: '14px 16px',
                borderRadius: 12,
                fontSize: 15,
                fontWeight: !selectedStopId ? 700 : 500,
                background: !selectedStopId ? 'var(--mint-lighter)' : 'var(--bg-gray)',
                color: !selectedStopId ? 'var(--mint-text)' : 'var(--black)',
                border: 'none',
                cursor: 'pointer',
                marginBottom: 8
              }}
            >
              {noneLabel}
            </button>
          )}
          {stops.map(stop => (
            <button
              key={stop.id}
              onClick={() => onSelect(stop)}
              style={{
                textAlign: 'left',
                padding: '14px 16px',
                borderRadius: 12,
                fontSize: 15,
                fontWeight: stop.id === selectedStopId ? 700 : 500,
                background: stop.id === selectedStopId ? 'var(--mint-lighter)' : 'var(--bg-gray)',
                color: stop.id === selectedStopId ? 'var(--mint-text)' : 'var(--black)',
                border: 'none',
                cursor: 'pointer'
              }}
            >
              {stop.name}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default function Settings() {
  const navigate = useNavigate();
  const showToast = useToast();
  const { setSplashReady } = useContext(SplashContext);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [user, setUser] = useState(null);
  const [stops, setStops] = useState([]);
  const [notifEnabled, setNotifEnabled] = useState(false);
  const [alertEnabled, setAlertEnabled] = useState(false);
  const [boardingAlertStopId, setBoardingAlertStopId] = useState(null);
  const [destinationAlertStopId, setDestinationAlertStopId] = useState(null);
  const [boardingAlertEdit, setBoardingAlertEdit] = useState(false);
  const [destinationAlertEdit, setDestinationAlertEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  const [boardingEdit, setBoardingEdit] = useState(false);
  const [destinationEdit, setDestinationEdit] = useState(false);
  const [hasCustomDest, setHasCustomDest] = useState(false);

  useEffect(() => {
    const u = getUser();
    setUser(u);
    if (u) {
      if (u.destination_stop_id) setHasCustomDest(true);
      if (typeof u.proximity_alert_enabled !== 'undefined') setAlertEnabled(u.proximity_alert_enabled);
      if (typeof u.boarding_alert_stop_id !== 'undefined') setBoardingAlertStopId(u.boarding_alert_stop_id);
      if (typeof u.destination_alert_stop_id !== 'undefined') setDestinationAlertStopId(u.destination_alert_stop_id);
    }
    // Fetch live stops
    getStops()
      .then(data => { if (Array.isArray(data) && data.length) setStops(data); })
      .catch(() => { })
      .finally(() => setSplashReady());
  }, [setSplashReady]);

  const currentStopName = stops.find(s => s.id === user?.boarding_stop_id)?.name || 'Not set';
  const currentDestName = stops.find(s => s.id === user?.destination_stop_id)?.name || 'Not set';

  const handleBoardingChange = async (stop) => {
    setBoardingEdit(false);
    const updated = { ...user, boarding_stop_id: stop.id };
    saveUser(updated);
    setUser(updated);
    setSaving(true);
    try {
      await updatePreferences({ boarding_stop_id: stop.id });
      showToast(`Boarding stop updated to ${stop.name}`, 'success');
    } catch {
      showToast('Could not save boarding stop.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDestinationChange = async (stop) => {
    setDestinationEdit(false);
    const updated = { ...user, destination_stop_id: stop.id };
    saveUser(updated);
    setUser(updated);
    setSaving(true);
    try {
      await updatePreferences({ destination_stop_id: stop.id });
      showToast(`Destination stop updated to ${stop.name}`, 'success');
    } catch {
      showToast('Could not save destination stop.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleCustomDestToggle = async (val) => {
    setHasCustomDest(val);
    setSaving(true);
    try {
      if (!val) {
        const updated = { ...user, destination_stop_id: null, destination_alert_stop_id: null };
        saveUser(updated);
        setUser(updated);
        await updatePreferences({ destination_alert_stop_id: null });
      }
    } catch {
      showToast('Failed to save preference.', 'error');
    } finally {
      setSaving(false);
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
      });
      showToast(val ? 'Smart Alerts enabled' : 'Smart Alerts disabled', 'success');
    } catch {
      showToast('Could not update alert setting.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleBoardingAlertChange = async (stop) => {
    setBoardingAlertEdit(false);
    const stopId = stop ? stop.id : null;
    setBoardingAlertStopId(stopId);
    const updated = { ...user, boarding_alert_stop_id: stopId };
    saveUser(updated);
    setUser(updated);
    setSaving(true);
    try {
      await updatePreferences({ boarding_alert_stop_id: stopId });
      showToast(stop ? `Catch the Bus trigger updated to ${stop.name}` : 'Catch the Bus alert disabled', 'success');
    } catch {} finally { setSaving(false); }
  };

  const handleDestinationAlertChange = async (stop) => {
    setDestinationAlertEdit(false);
    const stopId = stop ? stop.id : null;
    setDestinationAlertStopId(stopId);
    const updated = { ...user, destination_alert_stop_id: stopId };
    saveUser(updated);
    setUser(updated);
    setSaving(true);
    try {
      await updatePreferences({ destination_alert_stop_id: stopId });
      showToast(stop ? `Get-Off trigger updated to ${stop.name}` : 'Get-Off alert disabled', 'success');
    } catch {} finally { setSaving(false); }
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

              <div className="settings-row__content">
                <div className="settings-row__label">Name</div>
                <div className="settings-row__value">{user?.name || '—'}</div>
              </div>
            </div>

            <div className="settings-row">

              <div className="settings-row__content">
                <div className="settings-row__label">Email</div>
                <div className="settings-row__value" style={{ fontSize: '15px', wordBreak: 'break-all' }}>
                  {user?.email || 'Not set'}
                </div>
              </div>
            </div>

            <div className="settings-row" style={{ alignItems: 'flex-start' }}>

              <div className="settings-row__content">
                <div className="settings-row__label">Boarding Stop</div>
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
              </div>
            </div>

            <div className="settings-row">
              <div className="settings-row__content">
                <div className="settings-row__label">Different Destination Point</div>
              </div>
              <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={hasCustomDest}
                  onChange={e => handleCustomDestToggle(e.target.checked)}
                  disabled={saving}
                  style={{ display: 'none' }}
                />
                <div style={{
                  width: 24,
                  height: 24,
                  borderRadius: 6,
                  border: `2px solid ${hasCustomDest ? 'var(--mint-dark)' : '#ccc'}`,
                  backgroundColor: hasCustomDest ? 'var(--mint-dark)' : 'transparent',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'all 0.2s ease',
                  boxShadow: hasCustomDest ? '0 2px 5px rgba(0,0,0,0.1)' : 'none'
                }}>
                  {hasCustomDest && <Check size={16} color="white" strokeWidth={3} />}
                </div>
              </label>
            </div>

            {hasCustomDest && (
              <div className="settings-row" style={{ alignItems: 'flex-start' }}>
                <div className="settings-row__content">
                  <div className="settings-row__label">Destination Stop</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
                    <div className="settings-row__value">{currentDestName}</div>
                    <button
                      onClick={() => setDestinationEdit(true)}
                      style={{
                        fontSize: 12, fontWeight: 600, color: 'var(--mint-dark)',
                        textDecoration: 'underline',
                      }}
                    >
                      Change
                    </button>
                  </div>
                </div>
              </div>
            )}

          </div>

          {/* ── Trip Alerts ── */}
          <div className="settings-section-label">Trip Alerts</div>
          <div className="settings-card">

            <div className="settings-row">

              <div className="settings-row__content">
                <div className="settings-row__label">Enable Smart Alerts</div>
                <div className="settings-row__value">
                  {alertEnabled ? 'Enabled' : 'Disabled'}
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

            {alertEnabled && (
              <>
                <div className="settings-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 8 }}>
                  <div className="settings-row__content" style={{ width: '100%' }}>
                    <div className="settings-row__label">Catch the Bus Alert</div>
                    <div className="settings-row__value" style={{ fontSize: 14, fontWeight: 500, color: 'var(--gray)', marginTop: 2 }}>
                      Notify me when the bus reaches:
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
                      <div className="settings-row__value">
                        {stops.find(s => s.id === boardingAlertStopId)?.name || 'Not set'}
                      </div>
                      <button
                        onClick={() => setBoardingAlertEdit(true)}
                        style={{
                          fontSize: 12, fontWeight: 600, color: 'var(--mint-dark)',
                          textDecoration: 'underline',
                        }}
                        disabled={saving}
                      >
                        Change
                      </button>
                    </div>
                  </div>
                </div>

                <div className="settings-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 8 }}>
                  <div className="settings-row__content" style={{ width: '100%' }}>
                    <div className="settings-row__label">Get-Off Reminder</div>
                    <div className="settings-row__value" style={{ fontSize: 14, fontWeight: 500, color: 'var(--gray)', marginTop: 2 }}>
                      Remind me to get off when the bus reaches:
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
                      <div className="settings-row__value">
                        {stops.find(s => s.id === destinationAlertStopId)?.name || 'Not set'}
                      </div>
                      <button
                        onClick={() => setDestinationAlertEdit(true)}
                        style={{
                          fontSize: 12, fontWeight: 600, color: 'var(--mint-dark)',
                          textDecoration: 'underline',
                        }}
                        disabled={saving}
                      >
                        Change
                      </button>
                    </div>
                  </div>
                </div>
              </>
            )}

          </div>

          {/* ── Notifications ── */}
          <div className="settings-section-label">Notifications</div>
          <div className="settings-card">

            <div className="settings-row">

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



          </div>

          {/* ── App Info ── */}
          <div style={{ textAlign: 'center', marginTop: 32, marginBottom: -8, color: '#aaa', fontSize: 13 }}>
            Version 1.0.0 PWA
          </div>

          {/* Logout — subtle, text-based */}
          <button
            id="logout-btn"
            onClick={handleLogout}
            style={{ 
              marginTop: 24, 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center', 
              gap: 8, 
              background: 'transparent', 
              border: 'none', 
              color: '#888', 
              fontSize: 16, 
              fontWeight: 500,
              cursor: 'pointer',
              padding: '12px',
              width: '100%'
            }}
          >
            <LogOut size={18} />
            Log Out
          </button>

        </div>
      </div>

      <StopSelectModal
        isOpen={boardingEdit}
        title="Select Boarding Stop"
        stops={stops}
        selectedStopId={user?.boarding_stop_id}
        onSelect={handleBoardingChange}
        onClose={() => setBoardingEdit(false)}
      />
      <StopSelectModal
        isOpen={destinationEdit}
        title="Select Destination Stop"
        stops={stops}
        selectedStopId={user?.destination_stop_id}
        onSelect={handleDestinationChange}
        onClose={() => setDestinationEdit(false)}
      />
      <StopSelectModal
        isOpen={boardingAlertEdit}
        title="Select Trigger Stop for Catching Bus"
        stops={stops}
        selectedStopId={boardingAlertStopId}
        onSelect={handleBoardingAlertChange}
        onClose={() => setBoardingAlertEdit(false)}
        showNone={true}
        noneLabel="Disable this alert"
      />
      <StopSelectModal
        isOpen={destinationAlertEdit}
        title="Select Trigger Stop for Getting Off"
        stops={stops}
        selectedStopId={destinationAlertStopId}
        onSelect={handleDestinationAlertChange}
        onClose={() => setDestinationAlertEdit(false)}
        showNone={true}
        noneLabel="Disable this alert"
      />
    </>
  );
}
