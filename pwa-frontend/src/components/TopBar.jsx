/**
 * TopBar.jsx — DUK Bus Tracker PWA
 * Header bar with hamburger menu, title, and optional action button.
 */
import React from 'react';
import { Menu, ArrowLeft, RefreshCw, Bell } from 'lucide-react';

export default function TopBar({ onHamburger, title = 'DUK Bus Tracker', onBack, onRefresh, onNotification, showBack = false }) {
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
        <img src="/buslogo.png" alt="DUK Bus Tracker" className="topbar__logo-img" />
        <img src="/canlab.png" alt="CanLab" className="topbar__logo-img" />
      </div>

      <div className="topbar__right">
        {onNotification && (
          <button className="topbar__icon-btn" onClick={onNotification} aria-label="View notifications">
            <Bell size={24} />
          </button>
        )}
      </div>
    </header>
  );
}
