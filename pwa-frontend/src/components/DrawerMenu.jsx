/**
 * DrawerMenu.jsx — DUK Bus Tracker PWA
 * Animated side drawer that slides from the left.
 * Shows user profile and allows submitting suggestions.
 */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { X, User, MapPin, Settings, Send, LogOut, MessageSquare, Bus } from 'lucide-react';
import { getUser, clearSession } from '../storage';
import { submitSuggestion } from '../api';
import { useToast } from '../App';

export default function DrawerMenu({ isOpen, onClose }) {
  const navigate   = useNavigate();
  const showToast  = useToast();
  const overlayRef = useRef(null);

  const [user, setUser]           = useState(null);
  const [suggestion, setSuggestion] = useState('');
  const [showSugg, setShowSugg]   = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Load user when drawer opens
  useEffect(() => {
    if (isOpen) {
      setUser(getUser());
    }
  }, [isOpen]);

  // Trap body scroll while open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const handleLogout = useCallback(() => {
    clearSession();
    onClose();
    navigate('/', { replace: true });
  }, [navigate, onClose]);

  const handleSuggestionSubmit = useCallback(async () => {
    if (!suggestion.trim()) return;
    setSubmitting(true);
    try {
      await submitSuggestion(suggestion.trim(), '', '');
      setSuggestion('');
      setShowSugg(false);
      showToast('Suggestion submitted! Thank you 🙏', 'success');
    } catch (err) {
      showToast('Failed to send suggestion. Try again.', 'error');
    } finally {
      setSubmitting(false);
    }
  }, [suggestion, showToast]);

  const handleOverlayClick = useCallback((e) => {
    if (e.target === overlayRef.current) onClose();
  }, [onClose]);

  return (
    <div
      className={`drawer-overlay ${isOpen ? 'drawer-overlay--open' : ''}`}
      ref={overlayRef}
      onClick={handleOverlayClick}
      aria-hidden={!isOpen}
    >
      <aside className={`drawer ${isOpen ? 'drawer--open' : ''}`} role="dialog" aria-modal="true" aria-label="Menu">

        {/* Header */}
        <div className="drawer__header">
          <div className="drawer__brand">
            <span className="drawer__brand-icon">🚌</span>
            <span className="drawer__brand-name">DUK Bus</span>
          </div>
          <button className="drawer__close" onClick={onClose} aria-label="Close menu">
            <X size={20} />
          </button>
        </div>

        {/* Profile card */}
        <div className="drawer__profile">
          <div className="drawer__avatar">
            {user?.name ? user.name.charAt(0).toUpperCase() : '?'}
          </div>
          <div className="drawer__profile-info">
            <span className="drawer__profile-name">{user?.name || '—'}</span>
            <span className="drawer__profile-email">{user?.email || '—'}</span>
          </div>
        </div>

        {/* Nav items */}
        <nav className="drawer__nav">
          <button
            className="drawer__nav-item"
            onClick={() => { onClose(); navigate('/settings'); }}
          >
            <Settings size={18} className="drawer__nav-icon" />
            <span>Settings</span>
          </button>

          <button
            className="drawer__nav-item"
            onClick={() => setShowSugg(s => !s)}
          >
            <MessageSquare size={18} className="drawer__nav-icon" />
            <span>Send Feedback</span>
          </button>

          {showSugg && (
            <div className="drawer__suggestion-box">
              <textarea
                className="drawer__suggestion-input"
                placeholder="Share your thoughts, issues or ideas..."
                maxLength={500}
                value={suggestion}
                onChange={e => setSuggestion(e.target.value)}
                rows={4}
              />
              <div className="drawer__suggestion-footer">
                <span className="drawer__suggestion-count">{suggestion.length}/500</span>
                <button
                  className="drawer__suggestion-send"
                  onClick={handleSuggestionSubmit}
                  disabled={submitting || !suggestion.trim()}
                >
                  {submitting ? 'Sending…' : <><Send size={14} /> Send</>}
                </button>
              </div>
            </div>
          )}
        </nav>

        {/* Footer */}
        <div className="drawer__footer">
          <button className="drawer__logout" onClick={handleLogout}>
            <LogOut size={16} />
            <span>Sign Out</span>
          </button>
          <span className="drawer__version">DUK Bus Tracker v1.0 PWA</span>
        </div>

      </aside>
    </div>
  );
}
