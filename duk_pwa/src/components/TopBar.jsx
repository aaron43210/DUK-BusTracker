/**
 * TopBar.jsx — DUK Bus Tracker PWA
 * Header bar with hamburger menu, logos, and notification bell with unread badge.
 */
import React from 'react';
import { Menu, ArrowLeft, Bell } from 'lucide-react';
import { useNotifications } from '../App';

export default function TopBar({ onHamburger, onBack, onNotification, showBack = false, title = null }) {
  const { hasUnread } = useNotifications();

  return (
    <header className="topbar">
      <div className="topbar__left">
        {showBack ? (
          <button className="topbar__icon-btn" onClick={onBack} aria-label="Go back">
            <ArrowLeft size={22} />
          </button>
        ) : (
          <button className="topbar__icon-btn" onClick={onHamburger} aria-label="Open menu">
            <Menu size={22} />
          </button>
        )}
      </div>

      {title ? (
        <div className="topbar__center" style={{ gap: 0 }}>
          <span style={{ fontSize: 17, fontWeight: 700, color: 'var(--black)' }}>{title}</span>
        </div>
      ) : (
        <div className="topbar__center">
          <img src="/duk-logo.png" alt="DUK Bus Tracker" className="topbar__logo-img" />
          <img src="/canlab.png" alt="CanLab" className="topbar__logo-img" style={{ transform: 'scale(0.9)' }} />
        </div>
      )}

      <div className="topbar__right">
        {onNotification && (
          <button
            className="topbar__icon-btn topbar__bell-btn"
            onClick={onNotification}
            aria-label={`View notifications${hasUnread ? ' (unread)' : ''}`}
            style={{ position: 'relative' }}
          >
            <Bell size={24} />
            {hasUnread && (
              <span className="topbar__notif-dot" aria-hidden="true" style={{
                position: 'absolute', top: '6px', right: '6px', width: '10px', height: '10px',
                background: '#ef4444', borderRadius: '50%', border: '2px solid var(--white)'
              }} />
            )}
          </button>
        )}
      </div>
    </header>
  );
}
