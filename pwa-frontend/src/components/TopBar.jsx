/**
 * TopBar.jsx — DUK Bus Tracker PWA
 * Header bar with hamburger menu, logos, and notification bell with unread badge.
 */
import React from 'react';
import { Menu, ArrowLeft, Bell } from 'lucide-react';
import { useNotifications } from '../App';

export default function TopBar({ onHamburger, onBack, onNotification, showBack = false }) {
  const { notifications } = useNotifications();
  const unreadCount = notifications.length;

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

      <div className="topbar__center">
        <img src="/duk-logo.png" alt="DUK Bus Tracker" className="topbar__logo-img" />
        <img src="/canlab.png" alt="CanLab" className="topbar__logo-img" />
      </div>

      <div className="topbar__right">
        {onNotification && (
          <button
            className="topbar__icon-btn topbar__bell-btn"
            onClick={onNotification}
            aria-label={`View notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ''}`}
            style={{ position: 'relative' }}
          >
            <Bell size={24} />
            {unreadCount > 0 && (
              <span className="topbar__notif-badge" aria-hidden="true">
                {unreadCount > 9 ? '9+' : unreadCount}
              </span>
            )}
          </button>
        )}
      </div>
    </header>
  );
}
