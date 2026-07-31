// src/pages/Broadcast.jsx
// ─────────────────────────────────────────────────────────────────────────────
// Broadcast Notification — compose a push message and send it to all
// registered DUK Bus app users via Firebase Cloud Messaging (FCM).
// ─────────────────────────────────────────────────────────────────────────────

import React, { useState } from 'react';
// Only useState needed — no data to fetch on mount for this page
import { Link } from 'react-router-dom';

import { sendBroadcast } from '../api.js'; // POST /admin/api/broadcast
import { useToast } from '../App.jsx';

export default function Broadcast() {
  const showToast = useToast();

  // Controlled form state
  const [title, setTitle] = useState(''); // notification title (displayed on phone lock screen)
  const [body, setBody] = useState(''); // notification message body text

  // result: null = not sent yet; API response object = show delivery stats
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false); // disables button while in-flight

  async function handleSend() {
    // Validate both fields — FCM requires a non-empty title and body
    if (!title.trim() || !body.trim()) {
      showToast('Title and message body are required', 'error');
      return;
    }
    setSubmitting(true);
    setResult(null); // clear previous result before sending a new one

    try {
      // sendBroadcast() POST /admin/api/broadcast → returns { sent, failed, mode }
      const data = await sendBroadcast(title.trim(), body.trim());
      setResult(data); // show the result stats card
      showToast(`Notification sent to ${data.sent ?? 0} user${data.sent !== 1 ? 's' : ''}`);
      setTitle(''); // clear the form so the next message starts fresh
      setBody('');
    } catch (err) {
      showToast(err.message || 'Broadcast failed', 'error');
    } finally {
      setSubmitting(false); // always re-enable the button
    }
  }

  return (
    <div>
      {/* Page header */}
      <div className="page-header">
        <div>
          <div className="page-title">Broadcast</div>
          <div className="page-sub">Send a push notification to all registered users</div>
        </div>
      </div>

      {/* Two-column layout: compose form + guidelines */}
      <div className="col-2" style={{ alignItems: 'start' }}>

        {/* Left: compose form */}
        <div>
          <div className="card">
            <div className="card-title">Compose Notification</div>

            {/* Title field */}
            <div className="form-group">
              <label className="form-label">Title</label>
              <input
                type="text"
                className="form-input"
                placeholder="e.g. Bus Running Late"
                value={title}
                onChange={e => setTitle(e.target.value)} // update state on keystroke
                maxLength={100}                          // FCM title character limit
              />
              {/* Right-aligned character counter */}
              <div style={{ fontSize: '11px', color: 'var(--text-light)', textAlign: 'right', marginTop: '4px' }}>
                {title.length} / 100
              </div>
            </div>

            {/* Body field */}
            <div className="form-group">
              <label className="form-label">Message</label>
              <textarea
                className="form-textarea"
                placeholder="e.g. The evening bus will depart 15 minutes late today."
                value={body}
                onChange={e => setBody(e.target.value)}
                maxLength={300}
              />
              <div style={{ fontSize: '11px', color: 'var(--text-light)', textAlign: 'right', marginTop: '4px' }}>
                {body.length} / 300
              </div>
            </div>

            {/* Preview — shown as soon as the user starts typing */}
            {(title || body) && (
              <div style={{
                background: 'var(--surface2)',
                border: '1px solid var(--border)',
                borderRadius: '8px',
                padding: '14px 16px',
                marginBottom: '18px',
              }}>
                <div style={{ fontSize: '11px', color: 'var(--text-light)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Preview
                </div>
                {/* Simulate how the notification looks on a device */}
                <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text)', marginBottom: '3px' }}>
                  {title || '—'}
                </div>
                <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                  {body || '—'}
                </div>
              </div>
            )}

            {/* Send button — full width, large */}
            <button
              className="btn btn-primary"
              onClick={handleSend}
              disabled={submitting}
              style={{ width: '100%', height: '42px', justifyContent: 'center' }}
            >
              {submitting ? 'Sending…' : 'Send to All Users'}
            </button>
          </div>

          {/* Delivery result — only shown after a successful send */}
          {result && (
            <div className="card">
              <div className="card-title">Delivery Report</div>
              <div className="col-2">
                {/* Delivered count */}
                <div style={{ textAlign: 'center', padding: '12px' }}>
                  <div style={{ fontSize: '36px', fontWeight: 800, color: 'var(--success)' }}>
                    {result.sent ?? 0}
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>Delivered</div>
                </div>
                {/* Failed count */}
                <div style={{ textAlign: 'center', padding: '12px' }}>
                  <div style={{ fontSize: '36px', fontWeight: 800, color: result.failed ? 'var(--danger)' : 'var(--text-muted)' }}>
                    {result.failed ?? 0}
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>Failed</div>
                </div>
              </div>
              {/* Show a warning when FCM is not yet configured in the backend .env */}
              {result.mode === 'placeholder' && (
                <div style={{
                  background: '#fef3c7',
                  border: '1px solid #fde68a',
                  borderRadius: '6px',
                  padding: '10px 14px',
                  fontSize: '13px',
                  color: 'var(--warning)',
                  marginTop: '12px',
                }}>
                  FCM is not configured. The notification was logged but not actually sent to devices.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right: guidelines card */}
        <div className="card">
          <div className="card-title">Guidelines</div>
          <ul style={{ paddingLeft: '18px', fontSize: '13px', color: 'var(--text-muted)', lineHeight: '2.0' }}>
            <li>Keep the title under 50 characters for full display on the lock screen.</li>
            <li>Trip-specific updates (late, cancelled) are sent automatically from the <Link to="/trips" style={{ color: 'var(--accent)' }}>Trips</Link> page.</li>
            <li>Avoid sending more than one broadcast per hour to prevent notification fatigue.</li>

          </ul>

          <hr className="divider" />

          {/* Quick-fill examples */}
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px', fontWeight: 600 }}>
            Quick examples
          </div>
          {[
            { t: 'Bus Running Late', b: 'The morning bus will be approximately 15 minutes late today.' },
            { t: 'Schedule Change', b: 'Tomorrow\'s evening bus will depart at 5:00 PM instead of 5:30 PM.' },
            { t: 'Service Notice', b: 'Regular bus service resumes from Monday. Thank you for your patience.' },
          ].map((ex, i) => (
            /* Clicking a quick example fills the form fields */
            <div
              key={i}
              onClick={() => { setTitle(ex.t); setBody(ex.b); }}
              style={{
                background: 'var(--surface2)',
                border: '1px solid var(--border)',
                borderRadius: '6px',
                padding: '10px 12px',
                marginBottom: '8px',
                cursor: 'pointer',
                transition: 'border-color 0.15s',
              }}
              onMouseEnter={e => e.currentTarget.style.borderColor = 'var(--border-md)'}
              onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
            >
              <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '2px' }}>{ex.t}</div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{ex.b}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
