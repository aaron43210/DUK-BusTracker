// src/pages/Suggestions.jsx
// ─────────────────────────────────────────────────────────────────────────────
// User Suggestions — displays feedback submitted by app users.
// The admin can read each suggestion and delete ones that are resolved.
// ─────────────────────────────────────────────────────────────────────────────

import React, { useState, useEffect } from 'react';
import { getSuggestions, deleteSuggestion, updateSuggestion } from '../api.js';
import { useToast } from '../App.jsx';

export default function Suggestions() {
  const showToast = useToast();

  const [items, setItems] = useState([]); // array of suggestion objects
  const [loading, setLoading] = useState(true);
  
  // Modal state
  const [respondItem, setRespondItem] = useState(null);
  const [statusVal, setStatusVal] = useState('approved');
  const [responseMsg, setResponseMsg] = useState('');
  const [saving, setSaving] = useState(false);

  // deleteId: tracks which row is currently being deleted (for per-row loading state)
  // Using per-row tracking so only that row's button shows a loading indicator
  const [deleteId, setDeleteId] = useState(null);

  // fetchSuggestions: GET /admin/api/suggestions — returns up to 100 suggestions
  async function fetchSuggestions() {
    try {
      const data = await getSuggestions();
      setItems(data);
    } catch (err) {
      showToast(err.message || 'Failed to load suggestions', 'error');
    } finally {
      setLoading(false);
    }
  }

  // Fetch once on mount
  useEffect(() => { fetchSuggestions(); }, []);

  // handleDelete: confirm → DELETE /admin/api/suggestions/:id → update local state
  async function handleDelete(id) {
    // window.confirm: native browser confirmation dialog
    if (!window.confirm('Remove this suggestion? This cannot be undone.')) return;

    setDeleteId(id); // mark this row as deleting → its button shows loading text
    try {
      await deleteSuggestion(id);
      showToast('Suggestion removed');
      // Remove from local state without re-fetching the whole list:
      // filter() returns a new array excluding the deleted item
      setItems(prev => prev.filter(s => s.id !== id));
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setDeleteId(null); // clear the per-row loading state
    }
  }

  // fmt: formats a database timestamp to a readable local date-time string
  function fmt(str) {
    // 'en-IN' locale: "24 Jul 2026, 08:45 PM" format
    return new Date(str).toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  // Handle saving the admin response
  async function handleSaveResponse() {
    if (!respondItem) return;
    setSaving(true);
    try {
      await updateSuggestion(respondItem.id, {
        status: statusVal,
        admin_response: responseMsg
      });
      showToast('Response saved and user notified');
      setItems(prev => prev.map(s => {
        if (s.id === respondItem.id) {
          return { ...s, status: statusVal, admin_response: responseMsg };
        }
        return s;
      }));
      setRespondItem(null);
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setSaving(false);
    }
  }

  // ── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner"></div>
        <p>Loading suggestions…</p>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">User Suggestions</div>
          <div className="page-sub">
            {items.length} suggestion{items.length !== 1 ? 's' : ''} submitted via the app
          </div>
        </div>

      </div>

      {/* Empty state */}
      {items.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px 24px' }}>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text)', marginBottom: '6px' }}>
            No suggestions yet
          </div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
            Suggestions submitted from the mobile app will appear here.
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: '60px' }}>ID</th>
                  <th style={{ width: '130px' }}>Submitted</th>
                  <th style={{ width: '130px' }}>User</th>
                  <th>Suggestion</th>
                  <th style={{ width: '100px' }}>Status</th>
                  <th style={{ width: '140px' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {/* .map() renders one row per suggestion */}
                {items.map(s => (
                  <tr key={s.id}>
                    <td><code>#{s.id}</code></td>
                    <td style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {fmt(s.created_at)}
                    </td>
                    <td style={{ fontSize: '12px', color: 'var(--text-muted)', wordBreak: 'break-all' }}>
                      {s.user_email || 'Anonymous'}
                      {s.trip && <div style={{marginTop: '4px'}}>Trip: {s.trip}</div>}
                    </td>
                    <td style={{ lineHeight: '1.6' }}>
                      <div style={{fontWeight: 500, color: 'var(--text)'}}>{s.suggestion}</div>
                      {s.admin_response && (
                        <div style={{ marginTop: '8px', padding: '8px', background: 'var(--surface2)', borderRadius: '0', fontSize: '12px', color: 'var(--text-muted)' }}>
                          <strong>Admin Reply:</strong> {s.admin_response}
                        </div>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${
                        s.status === 'approved' ? 'badge-green' : 
                        s.status === 'rejected' ? 'badge-red' : 
                        s.status === 'will_consider' ? 'badge-blue' : 
                        'badge-gray'
                      }`}>
                        {s.status === 'will_consider' ? 'Will Consider' : s.status || 'Pending'}
                      </span>
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: '6px', flexDirection: 'column' }}>
                        {s.status !== 'approved' && s.status !== 'rejected' && (
                          <button
                            className="btn btn-primary btn-sm"
                            onClick={() => {
                              setRespondItem(s);
                              setStatusVal(s.status === 'pending' ? 'approved' : s.status);
                              setResponseMsg(s.admin_response || '');
                            }}
                          >
                            Respond
                          </button>
                        )}
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => handleDelete(s.id)}
                          disabled={deleteId === s.id}
                        >
                          {deleteId === s.id ? '…' : 'Remove'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      
      {/* Response Modal */}
      {respondItem && (
        <div className="modal-overlay open">
          <div className="modal">
            <div className="modal-header">
              <div className="modal-title">Respond to Suggestion #{respondItem.id}</div>
              <button className="modal-close" onClick={() => setRespondItem(null)}>✕</button>
            </div>
            
            <div className="form-group" style={{marginTop: '16px'}}>
              <label className="form-label">Update Status</label>
              <select 
                className="form-input" 
                value={statusVal} 
                onChange={e => setStatusVal(e.target.value)}
              >
                <option value="approved">Approved (Final)</option>
                <option value="rejected">Rejected (Final)</option>
                <option value="will_consider">Will Consider</option>
              </select>
            </div>
            
            <div className="form-group">
              <label className="form-label">Message to User (Optional)</label>
              <textarea 
                className="form-input" 
                rows="4"
                placeholder="Write a response..."
                value={responseMsg}
                onChange={e => setResponseMsg(e.target.value)}
              ></textarea>
              <p style={{fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px'}}>
                This message will be sent as a push notification to {respondItem.user_email || 'the user'}.
              </p>
            </div>
            
            <div className="modal-footer" style={{marginTop: '24px', display: 'flex', gap: '8px', justifyContent: 'flex-end'}}>
              <button className="btn btn-ghost" onClick={() => setRespondItem(null)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSaveResponse} disabled={saving}>
                {saving ? 'Saving...' : 'Save & Notify'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
