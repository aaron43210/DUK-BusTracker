/**
 * Settings.jsx — DUK Bus Tracker PWA
 * User settings: profile display, boarding stop, trip preference, proximity alerts.
 */
import React, { useState, useEffect, useContext } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut, X } from 'lucide-react';
import TopBar from '../components/TopBar';
import DrawerMenu from '../components/DrawerMenu';
import { getUser, saveUser, clearSession, getAlertPreferences, saveAlertPreferences, saveAlertStash, getAlertStash } from '../storage';
import { updatePreferences, getStops, logoutApi } from '../api';

import { useToast, SplashContext } from '../App';

// Firebase is loaded lazily so it stays out of the main bundle. Settings warms
// the chunk on mount, so by the time the user taps a toggle the call below is
// synchronous and the permission prompt still fires inside the tap gesture.
let firebaseModule = null;
const loadFirebase = () => import('../firebase').then(m => (firebaseModule = m));
const requestAndSaveFcmToken = () => (
  firebaseModule
    ? firebaseModule.requestAndSaveFcmToken()
    : loadFirebase().then(m => m.requestAndSaveFcmToken())
).catch(() => { });

const StopSelectModal = ({ isOpen, title, stops, selectedStopId, onSelect, onClose, showNone = false, noneLabel = "Not Set" }) => {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose} style={{ zIndex: 2000 }}>
      <div className="modal-content" onClick={e => e.stopPropagation()} style={{ maxHeight: '80vh', display: 'flex', flexDirection: 'column', width: '90%', maxWidth: '400px' }}>
        <div style={{ padding: '24px 24px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 className="modal-title" style={{ margin: 0, fontSize: '18px' }}>{title}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close" style={{ right: 24, top: 24 }}>
            <X size={20} />
          </button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {showNone && (
            <button
              onClick={() => onSelect(null)}
              style={{
                textAlign: 'left',
                padding: '14px 16px',
                borderRadius: 12,
                fontSize: 13,
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
                fontSize: 13,
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

  const [boardingAlertStopId, setBoardingAlertStopId] = useState(null);
  const [destinationAlertStopId, setDestinationAlertStopId] = useState(null);
  const [boardingAlertEdit, setBoardingAlertEdit] = useState(false);
  const [destinationAlertEdit, setDestinationAlertEdit] = useState(false);
  const [saving, setSaving] = useState(false);
  const [boardingEdit, setBoardingEdit] = useState(false);
  const [destinationEdit, setDestinationEdit] = useState(false);

  const [vibrateEnabled, setVibrateEnabled] = useState(true);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [masterAlertsEnabled, setMasterAlertsEnabled] = useState(false);

  // Fine-grained push notification preferences (server-synced)
  const [notifyTripStarted, setNotifyTripStarted] = useState(true);
  const [notifyDelays, setNotifyDelays] = useState(true);
  const [notifyAdminBroadcasts, setNotifyAdminBroadcasts] = useState(true);
  const [savingNotif, setSavingNotif] = useState(false);

  useEffect(() => {
    loadFirebase().catch(() => { });
  }, []);

  useEffect(() => {
    const prefs = getAlertPreferences();
    setVibrateEnabled(prefs.vibrateEnabled);
    setSoundEnabled(prefs.soundEnabled);
    setMasterAlertsEnabled(prefs.masterAlertsEnabled);
  }, []);

  const handleVibrateToggle = (val) => {
    setVibrateEnabled(val);
    saveAlertPreferences({ vibrateEnabled: val, soundEnabled, masterAlertsEnabled });
  };

  const handleSoundToggle = (val) => {
    setSoundEnabled(val);
    saveAlertPreferences({ vibrateEnabled, soundEnabled: val, masterAlertsEnabled });
  };

  const handleMasterAlertsToggle = async (val) => {
    setMasterAlertsEnabled(val);
    saveAlertPreferences({ vibrateEnabled, soundEnabled, masterAlertsEnabled: val });

    if (val) {
      if ('Notification' in window && Notification.permission !== 'granted') {
        requestAndSaveFcmToken();
      }
      // Restore the pickup/drop-off stops that were stashed when the master
      // toggle was last turned off, so the user doesn't lose their selection.
      const stash = getAlertStash();
      if (!boardingAlertStopId && stash.boarding) {
        const bStop = stops.find(s => s.id === stash.boarding) || { id: stash.boarding, name: 'your stop' };
        await handleBoardingAlertChange(bStop);
      }
      if (!destinationAlertStopId && stash.destination) {
        const dStop = stops.find(s => s.id === stash.destination) || { id: stash.destination, name: 'your stop' };
        await handleDestinationAlertChange(dStop);
      }
    } else {
      // Remember the current selections BEFORE clearing them, then null the
      // stops server-side (a null stop = no location alert) to actually
      // suppress push. Re-enabling restores them from the stash above.
      saveAlertStash({ boarding: boardingAlertStopId, destination: destinationAlertStopId });
      if (boardingAlertStopId) await handleBoardingAlertChange(null);
      if (destinationAlertStopId) await handleDestinationAlertChange(null);
    }
  };



  useEffect(() => {
    const u = getUser();
    setUser(u);
    if (u) {
      if (typeof u.boarding_alert_stop_id !== 'undefined') setBoardingAlertStopId(u.boarding_alert_stop_id);
      if (typeof u.destination_alert_stop_id !== 'undefined') setDestinationAlertStopId(u.destination_alert_stop_id);
      // Initialize server-stored notification prefs
      setNotifyTripStarted(u.notify_trip_started !== false);
      setNotifyDelays(u.notify_delays !== false);
      setNotifyAdminBroadcasts(u.notify_admin_broadcasts !== false);
    }
    // Fetch live stops
    getStops()
      .then(data => { if (Array.isArray(data) && data.length) setStops(data); })
      .catch(() => { })
      .finally(() => setSplashReady());
  }, [setSplashReady]);

  const currentStopName = stops.find(s => s.id === user?.boarding_stop_id)?.name || 'Not set';
  const currentDestName = stops.find(s => s.id === (user?.destination_stop_id || user?.boarding_stop_id))?.name || 'Not set';

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

  // handleCustomDestToggle removed

  const handleBoardingAlertToggle = async (val) => {
    if (val) {
      if ('Notification' in window && Notification.permission !== 'granted') {
        requestAndSaveFcmToken();
      }
      setBoardingAlertEdit(true);
    } else {
      await handleBoardingAlertChange(null);
    }
  };

  const handleDestinationAlertToggle = async (val) => {
    if (val) {
      if ('Notification' in window && Notification.permission !== 'granted') {
        requestAndSaveFcmToken();
      }
      setDestinationAlertEdit(true);
    } else {
      await handleDestinationAlertChange(null);
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
      showToast(stop ? `Pick-up notification updated to ${stop.name}` : 'Pick-up notification disabled', 'success');
    } catch { } finally { setSaving(false); }
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
      showToast(stop ? `Drop-off notification updated to ${stop.name}` : 'Drop-off notification disabled', 'success');
    } catch { } finally { setSaving(false); }
  };

  const handleLogout = async () => {
    try {
      await logoutApi();  // Tell backend to revoke JWT + delete refresh tokens
    } catch {
      // Even if the API call fails, we clear the local session
    }
    clearSession();
    navigate('/', { replace: true });
  };

  const handleNotifyToggle = async (field, val, setter) => {
    setter(val);
    if (val && 'Notification' in window && Notification.permission !== 'granted') {
      requestAndSaveFcmToken();
    }
    const updated = { ...user, [field]: val };
    saveUser(updated);
    setUser(updated);
    setSavingNotif(true);
    try {
      await updatePreferences({ [field]: val });
      showToast(val ? 'Notification enabled' : 'Notification disabled', 'success');
    } catch {
      showToast('Could not save preference.', 'error');
    } finally {
      setSavingNotif(false);
    }
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

      <div className="screen" style={{ overflowY: 'auto' }}>
        <div className="settings-screen" style={{ paddingBottom: '120px' }}>
          <h1 className="settings-title">Settings</h1>

          {/*  Profile  */}
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
                <div className="settings-row__value" style={{ wordBreak: 'break-all' }}>
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

          </div>

          {/*  Notifications  */}
          <div className="settings-section-label">Notifications</div>
          <div className="settings-card">
            <div className="settings-row" style={{ alignItems: 'center', justifyContent: 'space-between', borderBottom: 'none' }}>
              <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>Enable all Notifications</div>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={masterAlertsEnabled}
                  onChange={e => handleMasterAlertsToggle(e.target.checked)}
                />
                <span className="toggle__slider" />
              </label>
            </div>
          </div>

          {masterAlertsEnabled && (
            <div className="settings-card" style={{ marginTop: 16 }}>
              <div className="settings-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 2 }}>
                <div style={{ display: 'flex', width: '100%', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ paddingRight: 16, lineHeight: 1.4, fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>
                    Pickup Notification
                  </div>
                  <label className="toggle" style={{ flexShrink: 0 }}>
                    <input
                      type="checkbox"
                      checked={boardingAlertStopId !== null}
                      onChange={e => handleBoardingAlertToggle(e.target.checked)}
                      disabled={saving}
                    />
                    <span className="toggle__slider" />
                  </label>
                </div>
                <div className="settings-row__content" style={{ width: '100%' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
                    <div className="settings-row__value">
                      {boardingAlertStopId ? (stops.find(s => s.id === boardingAlertStopId)?.name || 'Not set') : 'Not Set'}
                    </div>
                    <button
                      onClick={() => {
                        if (!boardingAlertStopId && 'Notification' in window && Notification.permission !== 'granted') {
                          requestAndSaveFcmToken();
                        }
                        setBoardingAlertEdit(true);
                      }}
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

              <div className="settings-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 2 }}>
                <div style={{ display: 'flex', width: '100%', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ paddingRight: 16, lineHeight: 1.4, fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>
                    Drop-off Notification
                  </div>
                  <label className="toggle" style={{ flexShrink: 0 }}>
                    <input
                      type="checkbox"
                      checked={destinationAlertStopId !== null}
                      onChange={e => handleDestinationAlertToggle(e.target.checked)}
                      disabled={saving}
                    />
                    <span className="toggle__slider" />
                  </label>
                </div>
                <div className="settings-row__content" style={{ width: '100%' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
                    <div className="settings-row__value">
                      {destinationAlertStopId ? (stops.find(s => s.id === destinationAlertStopId)?.name || 'Not set') : 'Not Set'}
                    </div>
                    <button
                      onClick={() => {
                        if (!destinationAlertStopId && 'Notification' in window && Notification.permission !== 'granted') {
                          requestAndSaveFcmToken();
                        }
                        setDestinationAlertEdit(true);
                      }}
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

              <div className="settings-row" style={{ alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>Trip Started</div>
                  <div style={{ fontSize: 12, color: 'var(--gray)', marginTop: 2 }}>When the bus begins its journey</div>
                </div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={notifyTripStarted}
                    disabled={savingNotif}
                    onChange={e => handleNotifyToggle('notify_trip_started', e.target.checked, setNotifyTripStarted)}
                  />
                  <span className="toggle__slider" />
                </label>
              </div>

              <div className="settings-row" style={{ alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>Delay Notification</div>
                  <div style={{ fontSize: 12, color: 'var(--gray)', marginTop: 2 }}>When your bus is running late</div>
                </div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={notifyDelays}
                    disabled={savingNotif}
                    onChange={e => handleNotifyToggle('notify_delays', e.target.checked, setNotifyDelays)}
                  />
                  <span className="toggle__slider" />
                </label>
              </div>

              <div className="settings-row" style={{ alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>General Announcements</div>
                  <div style={{ fontSize: 12, color: 'var(--gray)', marginTop: 2 }}>University broadcasts from admin</div>
                </div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={notifyAdminBroadcasts}
                    disabled={savingNotif}
                    onChange={e => handleNotifyToggle('notify_admin_broadcasts', e.target.checked, setNotifyAdminBroadcasts)}
                  />
                  <span className="toggle__slider" />
                </label>
              </div>

              <div className="settings-row" style={{ alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>Vibrate on Notification</div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={vibrateEnabled}
                    onChange={e => handleVibrateToggle(e.target.checked)}
                  />
                  <span className="toggle__slider" />
                </label>
              </div>

              <div className="settings-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 2, borderBottom: 'none' }}>
                <div style={{ display: 'flex', width: '100%', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--black)' }}>Sound on Notification</div>
                  <label className="toggle">
                    <input
                      type="checkbox"
                      checked={soundEnabled}
                      onChange={e => handleSoundToggle(e.target.checked)}
                    />
                    <span className="toggle__slider" />
                  </label>
                </div>
              </div>
            </div>
          )}

          {/*  App Info  */}
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
        title="Choose stop where you need to get alert"
        stops={stops}
        selectedStopId={boardingAlertStopId}
        onSelect={handleBoardingAlertChange}
        onClose={() => setBoardingAlertEdit(false)}
      />
      <StopSelectModal
        isOpen={destinationAlertEdit}
        title="Choose stop where you need to get alert"
        stops={stops}
        selectedStopId={destinationAlertStopId}
        onSelect={handleDestinationAlertChange}
        onClose={() => setDestinationAlertEdit(false)}
      />
    </>
  );
}
