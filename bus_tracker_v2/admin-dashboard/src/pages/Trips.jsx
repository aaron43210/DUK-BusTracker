// src/pages/Trips.jsx
import React, { useState, useEffect } from 'react';
import {
  getTrips, updateTripStatus, createTrip,
  ensureTodayTrips, cancelAdvanceTrip, revokeCancelTrip, revokeRangeCancel,
} from '../api.js';
import { useToast } from '../App.jsx';

// ── IST helpers ───────────────────────────────────────────────────────────────
function nowIST() {
  return new Date(Date.now() + 5.5 * 3600_000); // UTC → IST
}
function totalMinsIST() {
  const ist = nowIST();
  return ist.getUTCHours() * 60 + ist.getUTCMinutes();
}
function todayIST() {
  return nowIST().toISOString().split('T')[0];
}
function isTimeWindowPassed(isoDate, dir) {
  const today = todayIST();
  if (isoDate < today) return true;
  if (isoDate > today) return false;
  const mins = totalMinsIST();
  if (dir === 'forward') {
    return mins >= 11 * 60; // Morning window ends at 11:00 AM (660 mins)
  }
  if (dir === 'reverse') {
    return mins >= 20 * 60 + 30; // Evening window ends at 8:30 PM (1230 mins)
  }
  return false;
}

// ── Action visibility rules ───────────────────────────────────────────────────

// Checks if actions (Late, Cancel, etc.) are allowed for this trip
function isTripInActiveWindow(trip) {

  // Do not allow actions if the trip is already completed
  if (trip.status === 'completed') return false;

  // Convert trip date to a Date object
  const tripDate = new Date(trip.date + 'T00:00:00');

  // Get today's date (time set to 00:00:00 for comparison)
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // Allow actions only for today's trips
  if (tripDate.getTime() !== today.getTime()) return false;

  // Get current IST time in minutes
  const mins = totalMinsIST();

  // Check if this is a morning (forward) trip
  const isMorning =
    trip.direction === 'forward' ||
    trip.direction === 'morning' ||
    trip.direction === 'Morning';

  // Check if current time is within the allowed time window
  return isMorning
    ? (mins >= 6 * 60 && mins <= 11 * 60)        // Morning: 6:00 AM – 11:00 AM
    : (mins >= 11 * 60 && mins <= 20 * 60 + 30); // Evening: 11:00 AM – 8:30 PM
}

// Checks whether action buttons should be shown
function isActionVisible(trip) {

  // Hide buttons if trip is completed, cancelled, or already on trip
  if (['completed', 'cancelled', 'on_trip'].includes(trip.status))
    return false;

  // Otherwise, check the time window
  return isTripInActiveWindow(trip);
}

// Finds the status of a trip using its date and direction
function getTripStatus(trips, isoDate, direction) {
  const t = trips.find(t => t.date === isoDate && t.direction === direction);
  return t ? t.status : null;
}

// ── Smart trip log — All upcoming scheduled + 5 most recent past, always show cancelled ────────
function buildLogRows(trips) {
  const today = new Date(); today.setHours(0, 0, 0, 0);

  const upcoming = []; // scheduled / waiting — future or today
  const past = []; // completed, on_trip  — past
  const always = []; // cancelled or special-service — always shown

  for (const t of trips) {
    const d = new Date(t.date + 'T00:00:00');
    // Cancelled trips always appear (could be pre-cancelled upcoming or past)
    if (t.status === 'cancelled') { always.push(t); continue; }
    if (t.status === 'completed') past.push(t);
    else upcoming.push(t);
  }

  // All upcoming trips (so none are hidden) + 5 most recent completed past trips
  const sliced = [
    ...upcoming,
    ...past.slice(0, 5),
  ];

  // Merge always-show rows without duplicates, then sort by date desc
  const ids = new Set(sliced.map(t => t.id));
  for (const t of always) if (!ids.has(t.id)) sliced.push(t);

  sliced.sort((a, b) => {
    if (b.date !== a.date) return b.date.localeCompare(a.date);
    return b.id - a.id;
  });
  return sliced;
}

// ── Status badge ──────────────────────────────────────────────────────────────
function statusBadge(s) {
  const map = {
    active: 'badge-green',
    waiting: 'badge-blue',
    scheduled: 'badge-blue',
    on_trip: 'badge-green',
    completed: 'badge-gray',
    offline: 'badge-gray',
    cancelled: 'badge-red',
    late: 'badge-yellow',
    stop_change: 'badge-yellow',
  };

  if (s === 'on_trip') {
    return (
      <span className="badge badge-green" style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
        <span style={{
          width: '7px', height: '7px', borderRadius: '50%',
          background: '#16a34a', display: 'inline-block',
          animation: 'pulse 1.5s infinite',
        }} />
        On Trip
      </span>
    );
  }

  const label = s === 'stop_change' ? 'Stop Change' : s;
  return <span className={`badge ${map[s] || 'badge-gray'}`}>{label}</span>;
}

// ── Human-readable date ───────────────────────────────────────────────────────
function friendlyDate(isoDate) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
  const d = new Date(isoDate + 'T00:00:00');

  if (d.getTime() === today.getTime()) return 'Today';
  if (d.getTime() === yesterday.getTime()) return 'Yesterday';
  if (d.getTime() === tomorrow.getTime()) return 'Tomorrow';
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function Modal({ open, onClose, title, children, footer }) {
  return (
    <div className={`modal-overlay ${open ? 'open' : ''}`}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="modal-close" onClick={onClose}>&#x2715;</button>
        </div>
        {children}
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

// ── Trips page ────────────────────────────────────────────────────────────────
export default function Trips() {
  const showToast = useToast();

  const [trips, setTrips] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isWeekend, setIsWeekend] = useState(false);

  // Mark Late modal
  const [lateModal, setLateModal] = useState(false);
  const [lateTripId, setLateTripId] = useState(null);
  const [lateMinutes, setLateMinutes] = useState('');
  const [lateReason, setLateReason] = useState('');

  // Cancel modal
  const [cancelModal, setCancelModal] = useState(false);
  const [cancelTripId, setCancelTripId] = useState(null);
  const [cancelReason, setCancelReason] = useState('');

  // Stop Change modal
  const [stopModal, setStopModal] = useState(false);
  const [stopTripId, setStopTripId] = useState(null);
  const [stopMessage, setStopMessage] = useState('');

  // Special Service modal
  const [specialModal, setSpecialModal] = useState(false);
  const [direction, setDirection] = useState('forward');
  const [tripDate, setTripDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [createReturn, setCreateReturn] = useState(false);

  // Pre-Cancel modal state
  const [preCancelModal, setPreCancelModal] = useState(false);
  const [pcMode, setPcMode] = useState('single'); // 'single' | 'range'
  const [pcDate, setPcDate] = useState(() => new Date().toISOString().split('T')[0]);
  const [pcDateFrom, setPcDateFrom] = useState(() => new Date().toISOString().split('T')[0]);
  const [pcDateTo, setPcDateTo] = useState(() => new Date().toISOString().split('T')[0]);
  const [pcDirection, setPcDirection] = useState('forward');
  const [pcAlsoReturn, setPcAlsoReturn] = useState(false);
  const [pcReason, setPcReason] = useState('');

  // Revoke Cancel modal (single)
  const [revokeModal, setRevokeModal] = useState(false);
  const [revokeTripId, setRevokeTripId] = useState(null);
  const [revokeReason, setRevokeReason] = useState('');

  // Revoke Range modal
  const [revokeRangeModal, setRevokeRangeModal] = useState(false);
  const [revokeRangeTripIds, setRevokeRangeTripIds] = useState([]);  // trip IDs user selected
  const [revokeRangeCandidates, setRevokeRangeCandidates] = useState([]); // all cancelled trips
  const [revokeRangeReason, setRevokeRangeReason] = useState('');
  const [revokeRangeDateFrom, setRevokeRangeDateFrom] = useState('');
  const [revokeRangeDateTo, setRevokeRangeDateTo] = useState('');

  const [submitting, setSubmitting] = useState(false);

  async function fetchTrips() {
    try {
      const data = await getTrips();
      setTrips(data);
    } catch (err) {
      showToast(err.message || 'Failed to load trips', 'error');
    } finally {
      setLoading(false);
    }
  }

  // Runs only once when the Trips page loads
  useEffect(() => {
    // Function to initialize the page
    async function init() {
      try {
        // Create today's trips if they don't already exist
        const result = await ensureTodayTrips();

        // Check whether today is a weekend
        if (result.weekend)
          setIsWeekend(true);
      } catch (_) {
        // Ignore any errors (not critical)
      }

      // Fetch all trips from the backend
      await fetchTrips();
    }

    // Call the initialization function
    init();
  }, []); // Empty array → run only once when the component is mounted

  // ── Mark Late ─────────────────────────────────────────────────────────────
  async function submitLate() {
    const mins = parseInt(lateMinutes, 10);
    if (!mins || mins < 1) { showToast('Enter the number of minutes late', 'error'); return; }
    setSubmitting(true);
    try {
      await updateTripStatus({ trip_id: lateTripId, status: 'late', late_by_minutes: mins, reason: lateReason.trim() || undefined });
      showToast('Trip marked late — users notified');
      setLateModal(false); fetchTrips();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  }

  // ── Cancel ────────────────────────────────────────────────────────────────
  async function submitCancel() {
    if (!cancelReason.trim()) { showToast('Enter a cancellation reason', 'error'); return; }
    setSubmitting(true);
    try {
      await updateTripStatus({ trip_id: cancelTripId, status: 'cancelled', cancellation_reason: cancelReason.trim() });
      showToast('Trip cancelled — users notified');
      setCancelModal(false); fetchTrips();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  }

  // ── Stop Change ───────────────────────────────────────────────────────────
  async function submitStopChange() {
    if (!stopMessage.trim()) { showToast('Enter a message about the stop change', 'error'); return; }
    setSubmitting(true);
    try {
      await updateTripStatus({ trip_id: stopTripId, status: 'stop_change', message: stopMessage.trim() });
      showToast('Stop change notification sent');
      setStopModal(false); fetchTrips();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  }

  // ── Special Service ───────────────────────────────────────────────────────
  async function submitSpecialService() {
    if (!tripDate) { showToast('Please select a date', 'error'); return; }
    setSubmitting(true);
    try {
      await createTrip(direction, tripDate, createReturn);
      showToast('Special service trip(s) created');
      setSpecialModal(false); fetchTrips();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  }

  // ── Pre-Cancel ────────────────────────────────────────────────────────────
  async function submitPreCancel() {
    setSubmitting(true);
    try {
      if (pcMode === 'single') {
        if (!pcDate) { showToast('Please select a date', 'error'); setSubmitting(false); return; }
        await cancelAdvanceTrip({
          trip_date: pcDate, direction: pcDirection,
          also_cancel_return: pcAlsoReturn, reason: pcReason.trim(),
        });
        showToast('Trip(s) cancelled — users notified');
      } else {
        // Range mode — loop dates from pcDateFrom to pcDateTo
        if (!pcDateFrom || !pcDateTo) { showToast('Please select both From and To dates', 'error'); setSubmitting(false); return; }
        const from = new Date(pcDateFrom + 'T00:00:00');
        const to = new Date(pcDateTo + 'T00:00:00');
        if (from > to) { showToast('From date must be before To date', 'error'); setSubmitting(false); return; }

        // Build list of all dates in range
        const datesToCancel = [];
        const formatter = new Intl.DateTimeFormat('en-CA'); // en-CA defaults to YYYY-MM-DD
        for (let d = new Date(from); d <= to; d.setDate(d.getDate() + 1)) {
          datesToCancel.push(formatter.format(d));
        }

        const results = await Promise.allSettled(
          datesToCancel.map(dt =>
            cancelAdvanceTrip({
              trip_date: dt, direction: pcDirection,
              also_cancel_return: pcAlsoReturn, reason: pcReason.trim()
            })
          )
        );
        const ok = results.filter(r => r.status === 'fulfilled').length;
        const fail = results.length - ok;
        showToast(`${ok} date(s) cancelled${fail ? `, ${fail} failed` : ''} — users notified`);
      }
      setPreCancelModal(false); fetchTrips();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  }

  // ── Revoke Cancel (single) ─────────────────────────────────────────────────
  async function submitRevoke() {
    setSubmitting(true);
    try {
      await revokeCancelTrip({ trip_id: revokeTripId, reason: revokeReason.trim() });
      showToast('Trip restored — users notified');
      setRevokeModal(false); fetchTrips();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  }

  // ── Revoke Range ──────────────────────────────────────────────────────────
  function openRevokeRange() {
    // Gather all currently-cancelled trips as candidates
    const cancelled = trips.filter(t => t.status === 'cancelled');
    setRevokeRangeCandidates(cancelled);
    setRevokeRangeTripIds([]);
    setRevokeRangeReason('');
    setRevokeRangeDateFrom('');
    setRevokeRangeDateTo('');
    setRevokeRangeModal(true);
  }

  // Filter candidates when date range is set
  const filteredCandidates = revokeRangeCandidates.filter(t => {
    if (revokeRangeDateFrom && t.date < revokeRangeDateFrom) return false;
    if (revokeRangeDateTo && t.date > revokeRangeDateTo) return false;
    return true;
  });

  async function submitRevokeRange() {
    if (!revokeRangeTripIds.length) { showToast('Select at least one trip to restore', 'error'); return; }
    setSubmitting(true);
    try {
      const res = await revokeRangeCancel({ trip_ids: revokeRangeTripIds, reason: revokeRangeReason.trim() });
      showToast(`${res.restored?.length ?? 0} trip(s) restored — users notified`);
      setRevokeRangeModal(false); fetchTrips();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setSubmitting(false); }
  }

  // ── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner"></div>
        <p>Loading trips…</p>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div>

      {/* Page header */}
      <div className="page-header">
        <div>
          <div className="page-title">Trip Management</div>
          <div className="page-sub">
            {isWeekend
              ? 'Weekend — use Special Service to create a trip if needed'
              : "From Monday to Friday, Morning and Evening trips are auto-scheduled"}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button className="btn btn-danger btn-sm" onClick={() => {
            const tomorrow = new Date(Date.now() + 86400000).toISOString().split('T')[0];
            setPreCancelModal(true);
            setPcMode('single');
            setPcDate(tomorrow);
            setPcDateFrom(tomorrow);
            setPcDateTo(tomorrow);
            setPcDirection('forward');
            setPcAlsoReturn(false);
            setPcReason('');
          }}>
            Pre-Cancel
          </button>
          <button className="btn btn-ghost btn-sm" onClick={openRevokeRange}
            disabled={!trips.some(t => t.status === 'cancelled')}>
            Revoke Range
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => {
            const today = todayIST();
            const morningPassed = isTimeWindowPassed(today, 'forward');
            setSpecialModal(true);
            setTripDate(today);
            setDirection(morningPassed ? 'reverse' : 'forward');
            setCreateReturn(false);
          }}>
            Special Service
          </button>
        </div>
      </div>

      {/* Trips table */}
      <div className="card">
        <div className="card-title">Trip Log — Upcoming Scheduled Trips & Recent History</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Direction</th>
                <th>Status</th>
                <th>Delay</th>
                <th>Notes</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {buildLogRows(trips).map(t => (
                <tr key={t.id}>
                  <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                    {friendlyDate(t.date)}
                  </td>
                  <td>{t.direction === 'forward' ? 'Morning' : 'Evening'}</td>
                  <td>{statusBadge(t.status)}</td>
                  <td className="text-muted">
                    {t.late_by_minutes != null ? `${t.late_by_minutes} min` : '—'}
                  </td>
                  <td style={{
                    maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap', color: 'var(--text-muted)', fontSize: '12px',
                  }}>
                    {t.cancellation_reason || '—'}
                  </td>
                  <td>
                    {(() => {
                      if (t.status === 'completed') {
                        return <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>—</span>;
                      }
                      if (t.status === 'cancelled') {
                        return (
                          <button className="btn btn-ghost btn-sm" onClick={() => { setRevokeTripId(t.id); setRevokeReason(''); setRevokeModal(true); }}>
                            Revoke
                          </button>
                        );
                      }
                      return (
                        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                          <button className="btn btn-warn btn-sm" onClick={() => { setLateTripId(t.id); setLateMinutes(''); setLateReason(''); setLateModal(true); }}>
                            Mark Late
                          </button>
                          <button className="btn btn-ghost btn-sm" onClick={() => { setStopTripId(t.id); setStopMessage(''); setStopModal(true); }}>
                            Stop Change
                          </button>
                          <button className="btn btn-danger btn-sm" onClick={() => { setCancelTripId(t.id); setCancelReason(''); setCancelModal(true); }}>
                            Cancel
                          </button>
                        </div>
                      );
                    })()}
                  </td>
                </tr>
              ))}
              {trips.length === 0 && (
                <tr>
                  <td colSpan="6" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '40px' }}>
                    No trips found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>


      {/* ── Mark Late modal ── */}
      <Modal open={lateModal} onClose={() => setLateModal(false)} title="Mark Trip as Late"
        footer={<>
          <button className="btn btn-ghost" onClick={() => setLateModal(false)}>Cancel</button>
          <button className="btn btn-warn" onClick={submitLate} disabled={submitting}>
            {submitting ? 'Saving…' : 'Confirm & Notify'}
          </button>
        </>}
      >
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
          Users will receive a push notification about the delay.
        </p>
        <div className="form-group">
          <label className="form-label">Delay in minutes</label>
          <input type="number" className="form-input" placeholder="e.g. 15" min="1" max="120"
            value={lateMinutes} onChange={e => setLateMinutes(e.target.value)} autoFocus />
        </div>
        <div className="form-group">
          <label className="form-label">Reason (optional)</label>
          <input type="text" className="form-input" placeholder="e.g. Heavy traffic at Pattom junction"
            value={lateReason} onChange={e => setLateReason(e.target.value)} />
        </div>
      </Modal>

      {/* ── Cancel modal ── */}
      {(() => {
        const trip = trips.find(t => t.id === cancelTripId);
        const isOnTrip = trip && trip.status === 'on_trip';
        return (
          <Modal open={cancelModal} onClose={() => setCancelModal(false)} title="Cancel Trip"
            footer={<>
              <button className="btn btn-ghost" onClick={() => setCancelModal(false)}>Go back</button>
              <button className="btn btn-danger" onClick={submitCancel} disabled={submitting}>
                {submitting ? 'Cancelling…' : 'Cancel Trip & Notify All'}
              </button>
            </>}
          >
            {isOnTrip && (
              <div style={{ padding: '12px', backgroundColor: 'var(--bg-warn)', color: '#854d0e', borderRadius: '0', marginBottom: '16px', fontSize: '13px' }}>
                <strong>Warning:</strong> This bus is currently on its route! Cancelling it will immediately stop live tracking and notify all users.
              </div>
            )}
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              All users will be notified with the reason below.
            </p>
            <div className="form-group">
              <label className="form-label">Reason for cancellation</label>
              <textarea className="form-textarea" placeholder="e.g. Vehicle breakdown, driver unavailable…"
                value={cancelReason} onChange={e => setCancelReason(e.target.value)} />
            </div>
          </Modal>
        );
      })()}

      {/* ── Stop Change modal ── */}
      <Modal open={stopModal} onClose={() => setStopModal(false)} title="Report Stop Change"
        footer={<>
          <button className="btn btn-ghost" onClick={() => setStopModal(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={submitStopChange} disabled={submitting}>
            {submitting ? 'Sending…' : 'Send Notification'}
          </button>
        </>}
      >
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
          Describe the stop change — all users will receive this as a push notification.
        </p>
        <div className="form-group">
          <label className="form-label">Stop change message</label>
          <textarea className="form-textarea"
            placeholder="e.g. Kesavadasapuram stop temporarily shifted 200m north due to road works"
            value={stopMessage} onChange={e => setStopMessage(e.target.value)} autoFocus />
        </div>
      </Modal>

      {/* ── Special Service modal ── */}
      {(() => {
        const morningPassed = isTimeWindowPassed(tripDate, 'forward');
        const eveningPassed = isTimeWindowPassed(tripDate, 'reverse');
        const forwardExists = getTripStatus(trips, tripDate, 'forward') !== null;
        const reverseExists = getTripStatus(trips, tripDate, 'reverse') !== null;

        const forwardDisabled = forwardExists || morningPassed;
        const reverseDisabled = reverseExists || eveningPassed;

        const isCurrentDisabled = direction === 'forward' ? forwardDisabled : reverseDisabled;
        const allDisabled = forwardDisabled && reverseDisabled;

        return (
          <Modal open={specialModal} onClose={() => setSpecialModal(false)} title="Create Special Service"
            footer={<>
              <button className="btn btn-ghost" onClick={() => setSpecialModal(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={submitSpecialService}
                disabled={submitting || isCurrentDisabled}>
                {submitting ? 'Creating…' : 'Create Trip'}
              </button>
            </>}
          >
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              Use this for <strong>weekends or public holidays</strong> when the bus runs outside the regular schedule.
              If a trip already exists or its scheduled time window has passed, it cannot be created.
            </p>
            <div className="form-group">
              <label className="form-label">Date</label>
              <input type="date" className="form-input" value={tripDate}
                min={todayIST()}
                onChange={e => {
                  const newDate = e.target.value;
                  setTripDate(newDate);
                  if (isTimeWindowPassed(newDate, 'forward') && !isTimeWindowPassed(newDate, 'reverse')) {
                    setDirection('reverse');
                  }
                }} />
            </div>
            <div className="form-group">
              <label className="form-label">Direction</label>
              <select className="form-select" value={direction} onChange={e => setDirection(e.target.value)}>
                <option value="forward" disabled={forwardDisabled}>
                  Morning — Central Poly to DUK
                  {forwardExists
                    ? ` (${getTripStatus(trips, tripDate, 'forward')})`
                    : morningPassed
                      ? ' (time passed — after 11:00 AM)'
                      : ''}
                </option>
                <option value="reverse" disabled={reverseDisabled}>
                  Evening — DUK to Central Poly
                  {reverseExists
                    ? ` (${getTripStatus(trips, tripDate, 'reverse')})`
                    : eveningPassed
                      ? ' (time passed — after 8:30 PM)'
                      : ''}
                </option>
              </select>
            </div>

            {allDisabled && (
              <p style={{ fontSize: '12px', color: 'var(--danger)', marginTop: '8px', padding: '8px 12px', background: 'rgba(239, 68, 68, 0.08)', borderRadius: '0' }}>
                All trip slots for this date have either already completed or already exist. Please select an upcoming date.
              </p>
            )}

            {!allDisabled && forwardExists && direction === 'forward' && (
              <p style={{ fontSize: '12px', color: 'var(--danger)', marginTop: '6px' }}>
                A trip already exists for this date and direction ({getTripStatus(trips, tripDate, direction)}). Use Pre-Cancel or Revoke to manage it.
              </p>
            )}

            {!allDisabled && reverseExists && direction === 'reverse' && (
              <p style={{ fontSize: '12px', color: 'var(--danger)', marginTop: '6px' }}>
                A trip already exists for this date and direction ({getTripStatus(trips, tripDate, direction)}). Use Pre-Cancel or Revoke to manage it.
              </p>
            )}

            {direction === 'forward' && !forwardDisabled && (
              <div style={{ marginTop: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input type="checkbox" id="create_return_cb" checked={createReturn}
                  onChange={e => setCreateReturn(e.target.checked)}
                  disabled={reverseDisabled}
                  style={{ width: '16px', height: '16px', cursor: reverseDisabled ? 'not-allowed' : 'pointer' }} />
                <label htmlFor="create_return_cb"
                  style={{
                    fontSize: '14px', cursor: reverseDisabled ? 'not-allowed' : 'pointer',
                    color: reverseDisabled ? 'var(--text-muted)' : 'inherit'
                  }}>
                  Also schedule the return Evening trip
                  {reverseExists ? ' (Evening already exists)' : eveningPassed ? ' (Evening time passed)' : ''}
                </label>
              </div>
            )}
          </Modal>
        );
      })()}

      {/* ── Pre-Cancel modal ── */}
      <Modal open={preCancelModal} onClose={() => setPreCancelModal(false)} title="Pre-Cancel Trip"
        footer={<>
          <button className="btn btn-ghost" onClick={() => setPreCancelModal(false)}>Go back</button>
          <button className="btn btn-danger" onClick={submitPreCancel} disabled={submitting}>
            {submitting ? 'Cancelling…' : `Cancel ${pcMode === 'range' ? 'Range' : 'Trip'} & Notify`}
          </button>
        </>}
      >
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '12px' }}>
          Cancel trip(s) in advance. Users will be notified immediately and receive
          reminders the day before and on the day of the trip.
        </p>

        {/* Mode toggle */}
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          {['single', 'range'].map(m => (
            <button key={m}
              className={`btn btn-sm ${pcMode === m ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setPcMode(m)}
              style={{ textTransform: 'capitalize' }}>{m === 'single' ? 'Single Date' : 'Date Range'}</button>
          ))}
        </div>

        {pcMode === 'single' ? (
          <div className="form-group">
            <label className="form-label">Date</label>
            <input type="date" className="form-input" value={pcDate}
              min={new Date(Date.now() + 86400000).toISOString().split('T')[0]}
              onChange={e => setPcDate(e.target.value)} />
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <div className="form-group">
              <label className="form-label">From</label>
              <input type="date" className="form-input" value={pcDateFrom}
                min={new Date(Date.now() + 86400000).toISOString().split('T')[0]}
                onChange={e => setPcDateFrom(e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">To</label>
              <input type="date" className="form-input" value={pcDateTo}
                min={pcDateFrom || new Date(Date.now() + 86400000).toISOString().split('T')[0]}
                onChange={e => setPcDateTo(e.target.value)} />
            </div>
          </div>
        )}

        <div className="form-group">
          <label className="form-label">Direction</label>
          <select className="form-select" value={pcDirection} onChange={e => setPcDirection(e.target.value)}>
            <option value="forward">Morning — Central Poly to DUK</option>
            <option value="reverse">Evening — DUK to Central Poly</option>
          </select>
        </div>
        {pcDirection === 'forward' && (
          <div style={{ margin: '4px 0 12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <input type="checkbox" id="pc_return_cb" checked={pcAlsoReturn}
              onChange={e => setPcAlsoReturn(e.target.checked)}
              style={{ width: '16px', height: '16px', cursor: 'pointer' }} />
            <label htmlFor="pc_return_cb" style={{ fontSize: '14px', cursor: 'pointer' }}>
              Also cancel the Evening return trip{pcMode === 'range' ? 's in this range' : ' on this date'}
            </label>
          </div>
        )}
        <div className="form-group">
          <label className="form-label">Reason (optional)</label>
          <input type="text" className="form-input" placeholder="e.g. Public holiday, driver on leave…"
            value={pcReason} onChange={e => setPcReason(e.target.value)} />
        </div>
      </Modal>

      {/* ── Revoke Cancel modal ── */}
      <Modal open={revokeModal} onClose={() => setRevokeModal(false)} title="Revoke Cancellation"
        footer={<>
          <button className="btn btn-ghost" onClick={() => setRevokeModal(false)}>Go back</button>
          <button className="btn btn-primary" onClick={submitRevoke} disabled={submitting}>
            {submitting ? 'Revoking…' : 'Revoke & Notify Users'}
          </button>
        </>}
      >
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
          This will restore the trip to Scheduled status and send a push notification
          letting all users know the service is back on.
        </p>
        <div className="form-group">
          <label className="form-label">Reason / message to users (optional)</label>
          <input type="text" className="form-input"
            placeholder="e.g. Service back on as planned"
            value={revokeReason} onChange={e => setRevokeReason(e.target.value)} autoFocus />
        </div>
      </Modal>

      {/* ── Revoke Range modal ── */}
      <Modal open={revokeRangeModal} onClose={() => setRevokeRangeModal(false)} title="Revoke Cancelled Trips (Range)"
        footer={<>
          <button className="btn btn-ghost" onClick={() => setRevokeRangeModal(false)}>Go back</button>
          <button className="btn btn-primary" onClick={submitRevokeRange}
            disabled={submitting || !revokeRangeTripIds.length}>
            {submitting ? 'Revoking…' : `Revoke ${revokeRangeTripIds.length} Trip(s)`}
          </button>
        </>}
      >
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '12px' }}>
          Filter by date range, then select which cancelled trips to restore. A single
          push notification will be sent to all users.
        </p>
        {/* Date filter */}
        {(() => {
          let minCancelDate = '';
          let maxCancelDate = '';
          if (revokeRangeCandidates.length > 0) {
            const dates = revokeRangeCandidates.map(t => t.date).sort();
            minCancelDate = dates[0];
            maxCancelDate = dates[dates.length - 1];
          }
          return (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '12px' }}>
              <div className="form-group">
                <label className="form-label">Filter From</label>
                <input type="date" className="form-input" value={revokeRangeDateFrom}
                  min={minCancelDate} max={maxCancelDate}
                  onChange={e => { setRevokeRangeDateFrom(e.target.value); setRevokeRangeTripIds([]); }} />
              </div>
              <div className="form-group">
                <label className="form-label">Filter To</label>
                <input type="date" className="form-input" value={revokeRangeDateTo}
                  min={revokeRangeDateFrom || minCancelDate} max={maxCancelDate}
                  onChange={e => { setRevokeRangeDateTo(e.target.value); setRevokeRangeTripIds([]); }} />
              </div>
            </div>
          );
        })()}
        {/* Select all */}
        {filteredCandidates.length > 0 && (
          <div style={{ marginBottom: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              {filteredCandidates.length} cancelled trip(s) found
            </span>
            <button className="btn btn-ghost btn-sm" onClick={() => {
              const allIds = filteredCandidates.map(t => t.id);
              setRevokeRangeTripIds(allIds.length === revokeRangeTripIds.length ? [] : allIds);
            }}>
              {filteredCandidates.length === revokeRangeTripIds.length ? 'Deselect All' : 'Select All'}
            </button>
          </div>
        )}
        {/* Trip checkboxes */}
        <div style={{ maxHeight: '240px', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: '0', padding: '8px' }}>
          {filteredCandidates.length === 0 ? (
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', textAlign: 'center', padding: '12px' }}>No cancelled trips in this range.</p>
          ) : filteredCandidates.map(t => (
            <label key={t.id} style={{
              display: 'flex', alignItems: 'center', gap: '10px',
              padding: '8px', borderRadius: '6px', cursor: 'pointer',
              background: revokeRangeTripIds.includes(t.id) ? 'var(--primary-light, rgba(99,102,241,0.08))' : 'transparent'
            }}>
              <input type="checkbox"
                checked={revokeRangeTripIds.includes(t.id)}
                onChange={e => setRevokeRangeTripIds(prev =>
                  e.target.checked ? [...prev, t.id] : prev.filter(id => id !== t.id)
                )}
                style={{ width: '15px', height: '15px' }} />
              <span style={{ fontSize: '13px', fontWeight: 600 }}>{friendlyDate(t.date)}</span>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                {t.direction === 'forward' ? 'Morning' : 'Evening'}
              </span>
              {t.cancellation_reason && (
                <span style={{
                  fontSize: '11px', color: 'var(--text-muted)', marginLeft: 'auto',
                  maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                }}>
                  {t.cancellation_reason}
                </span>
              )}
            </label>
          ))}
        </div>
        <div className="form-group" style={{ marginTop: '12px' }}>
          <label className="form-label">Reason / message to users (optional)</label>
          <input type="text" className="form-input"
            placeholder="e.g. Service reinstated — all systems normal"
            value={revokeRangeReason} onChange={e => setRevokeRangeReason(e.target.value)} />
        </div>
      </Modal>
    </div>
  );
}

/* =============================================================================
                          TRIPS.JSX - LINE BY LINE EXPLANATION
===============================================================================

Lines 1 - Imports
------------------------------------------------------------------------------
Imports all required libraries and components.

Imports:

• React
• useState
• useEffect
• API functions
• Toast notification hook
• CSS

===============================================================================
Helper Functions
===============================================================================

===============================================================================
nowIST()
===============================================================================

Returns the current date and time in Indian Standard Time (IST).

Used for:

• Comparing trip times
• Checking active trip windows

===============================================================================
totalMinsIST()
===============================================================================

Converts the current IST time into total minutes.

Example:

08:30 AM

↓

510 minutes

Used for:

• Time comparisons
• Morning/Evening trip validation

===============================================================================
isTripInActiveWindow()
===============================================================================

Checks whether a trip is currently inside its valid operating window.

Steps:

• Ignore completed trips.
• Check whether the trip belongs to today.
• Convert current IST into minutes.
• Identify Morning or Evening trip.
• Compare current time with allowed time window.
• Return true or false.

===============================================================================
isActionVisible()
===============================================================================

Determines whether action buttons should be displayed.

Hides actions when:

• Completed
• Cancelled
• On Trip

Otherwise,

Uses isTripInActiveWindow().

Returns:

• true
• false

===============================================================================
getTripStatus()
===============================================================================

Finds a trip using:

• Date
• Direction

Returns:

• Active
• Waiting
• Scheduled
• On Trip
• Completed
• Cancelled
• Late
• Stop Change

Returns null if no trip is found.

===============================================================================
statusBadge()
===============================================================================

Converts trip status into a coloured badge.

Supported Status:

• Active
• Waiting
• Scheduled
• On Trip
• Completed
• Cancelled
• Late
• Stop Change

Special case:

• On Trip shows a blinking green indicator.

Returns a JSX badge.

===============================================================================
friendlyDate()
===============================================================================

Converts ISO date into user-friendly text.

Example:

Today

Yesterday

Tomorrow

Otherwise

Displays

DD Mon

Example:

29 Jul

===============================================================================
Modal Component
===============================================================================

Reusable popup component.

Contains:

• Modal header
• Title
• Close button
• Body
• Footer

Used by:

• Mark Late
• Cancel
• Stop Change
• Special Service
• Pre-Cancel
• Revoke Cancel

===============================================================================
Trips Component
===============================================================================

Starts the Trips page.

Creates:

• Toast hook

React States:

Page-level

• Trips
• Loading
• Weekend status

Mark Late Modal

• Open state
• Trip ID
• Late minutes
• Late reason

Cancel Modal

• Open state
• Trip ID
• Cancellation reason

Stop Change Modal

• Open state
• Trip ID
• Stop change message

Special Service Modal

• Open state
• Direction
• Trip date
• Return trip option

Pre-Cancel Modal

• Open state
• Date
• Direction
• Return option
• Reason

Revoke Cancel Modal

• Open state
• Trip ID
• Reason

Shared State

• Submitting

===============================================================================
fetchTrips()
===============================================================================

Downloads all trips from the backend.

Steps:

• Calls getTrips().
• Saves trips into React state.
• Shows error toast if API fails.
• Stops loading spinner.

===============================================================================
Initialization useEffect()
===============================================================================

Runs only once after page loads.

Performs:

• Creates today's trips automatically.
• Detects weekends.
• Loads all trips.

===============================================================================
submitLate()
===============================================================================

Marks a trip as late.

Steps:

• Validate late minutes.
• Disable submit button.
• Update backend.
• Notify users.
• Close modal.
• Reload trips.
• Enable button.

===============================================================================
submitCancel()
===============================================================================

Cancels a trip.

Steps:

• Validate reason.
• Disable submit button.
• Update backend.
• Notify users.
• Close modal.
• Reload trips.
• Enable button.

===============================================================================
submitStopChange()
===============================================================================

Creates a stop change notification.

Steps:

• Validate message.
• Disable submit button.
• Update backend.
• Notify users.
• Close modal.
• Reload trips.
• Enable button.

===============================================================================
submitSpecialService()
===============================================================================

Creates a special service trip.

Steps:

• Validate date.
• Disable submit button.
• Create trip.
• Optionally create return trip.
• Close modal.
• Reload trips.
• Enable button.

===============================================================================
submitPreCancel()
===============================================================================

Cancels a future trip.

Steps:

• Validate date.
• Disable submit button.
• Cancel trip.
• Notify users.
• Close modal.
• Reload trips.
• Enable button.

===============================================================================
submitRevoke()
===============================================================================

Restores a cancelled trip.

Steps:

• Disable submit button.
• Restore trip.
• Notify users.
• Close modal.
• Reload trips.
• Enable button.

===============================================================================
Loading Screen
===============================================================================

Displays loading animation while trips are being downloaded.

Shows:

• Spinner
• Loading message

===============================================================================
Trips Page UI
===============================================================================

Builds the main Trips interface.

Displays:

• Page title
• Weekday trips
• Weekend notice
• Morning trips
• Evening trips
• Current status badges
• Action buttons

Provides actions for:

• Mark Late
• Cancel Trip
• Stop Change
• Special Service
• Pre-Cancel
• Revoke Cancel

===============================================================================
Modal Dialogs
===============================================================================

Displays popup forms for user actions.

Includes:

• Mark Late form
• Cancel Trip form
• Stop Change form
• Special Service form
• Pre-Cancel form
• Revoke Cancel form

Each modal:

• Accepts user input.
• Calls backend API.
• Displays success/error toast.
• Refreshes trip list.

===============================================================================
OVERALL EXECUTION FLOW
===============================================================================

Trips Page Starts
        │
        ▼
Create React States
        │
        ▼
Initialize Page
        │
        ▼
Create Today's Trips
        │
        ▼
Fetch Trips
        │
        ▼
Display Trips
        │
        ▼
Admin Selects Action
        │
        ├── Mark Late
        ├── Cancel Trip
        ├── Stop Change
        ├── Special Service
        ├── Pre-Cancel
        └── Revoke Cancel
        │
        ▼
Call Backend API
        │
        ▼
Update Database
        │
        ▼
Notify Users
        │
        ▼
Refresh Trips
        │
        ▼
Update UI

===============================================================================
*/