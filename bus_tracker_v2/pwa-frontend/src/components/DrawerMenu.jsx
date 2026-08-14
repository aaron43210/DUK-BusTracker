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

import AboutModal from './AboutModal';
import SuggestionsModal from './SuggestionsModal';
import { HelpCircle, Info } from 'lucide-react';

export default function DrawerMenu({ isOpen, onClose }) {
  const navigate = useNavigate();
  const overlayRef = useRef(null);

  const [user, setUser] = useState(null);
  const [isAboutOpen, setIsAboutOpen] = useState(false);
  const [isSuggestionsOpen, setIsSuggestionsOpen] = useState(false);

  // Load user when drawer opens
  useEffect(() => {
    if (isOpen) {
      setUser(getUser());
    }
  }, [isOpen]);

  // Trap body scroll while open
  useEffect(() => {
    if (isOpen || isAboutOpen || isSuggestionsOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen, isAboutOpen, isSuggestionsOpen]);

  const handleLogout = useCallback(() => {
    clearSession();
    onClose();
    navigate('/', { replace: true });
  }, [navigate, onClose]);

  const handleOverlayClick = useCallback((e) => {
    if (e.target === overlayRef.current) onClose();
  }, [onClose]);

  return (
    <>
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
              <img src="/duk-logo.png" alt="DUK Logo" style={{ height: '48px', objectFit: 'contain' }} />
            </div>
            <button className="drawer__close" onClick={onClose} aria-label="Close menu">
              <X size={20} />
            </button>
          </div>

          {/* Profile card */}
          <div className="drawer__profile">
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
              onClick={() => { onClose(); navigate('/help'); }}
            >
              <HelpCircle size={18} className="drawer__nav-icon" />
              <span>Help</span>
            </button>

            <button
              className="drawer__nav-item"
              onClick={() => { onClose(); setIsSuggestionsOpen(true); }}
            >
              <MessageSquare size={18} className="drawer__nav-icon" />
              <span>Send Feedback</span>
            </button>

            <button
              className="drawer__nav-item"
              onClick={() => { onClose(); setIsAboutOpen(true); }}
            >
              <Info size={18} className="drawer__nav-icon" />
              <span>About</span>
            </button>
          </nav>

          {/* Footer */}
          <div className="drawer__footer">
            <span className="drawer__version">DUK Bus Tracker v1.0 PWA</span>
          </div>
        </aside>
      </div>

      <AboutModal isOpen={isAboutOpen} onClose={() => setIsAboutOpen(false)} />
      <SuggestionsModal isOpen={isSuggestionsOpen} onClose={() => setIsSuggestionsOpen(false)} />
    </>
  );
}
