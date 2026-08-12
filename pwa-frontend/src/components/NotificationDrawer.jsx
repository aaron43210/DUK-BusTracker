import React, { useEffect, useRef } from 'react';
import { X, Bell, AlertTriangle, CheckCircle, Info, Trash2 } from 'lucide-react';
import { useNotifications } from '../App';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Map FCM data.type to a visual icon variant */
function typeFromData(data) {
  if (!data) return 'info';
  if (data.type === 'eta_late') return 'warning';
  if (data.type === 'proximity') return 'success';
  return 'info';
}

function relativeTime(date) {
  const diff = Math.floor((Date.now() - date) / 1000);
  if (diff < 60)  return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  return date.toLocaleDateString();
}

const getIcon = (type) => {
  switch (type) {
    case 'warning': return <AlertTriangle size={18} className="notification-icon--warning" />;
    case 'success': return <CheckCircle   size={18} className="notification-icon--success" />;
    default:        return <Info          size={18} className="notification-icon--info" />;
  }
};

// ── Component ─────────────────────────────────────────────────────────────────

export default function NotificationDrawer({ isOpen, onClose }) {
  const overlayRef = useRef(null);
  const { notifications, clearNotifications } = useNotifications();

  useEffect(() => {
    document.body.style.overflow = isOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const handleOverlayClick = (e) => {
    if (e.target === overlayRef.current) onClose();
  };

  return (
    <>
      <div
        className={`drawer-overlay ${isOpen ? 'drawer-overlay--open' : ''}`}
        ref={overlayRef}
        onClick={handleOverlayClick}
        aria-hidden={!isOpen}
      />
      <div
        className={`notification-drawer ${isOpen ? 'notification-drawer--open' : ''}`}
        role="dialog"
        aria-label="Notifications"
        aria-hidden={!isOpen}
      >
        <div className="notification-drawer__header">
          <div className="notification-drawer__title">
            <Bell size={20} />
            <span>Notifications</span>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            {notifications.length > 0 && (
              <button
                className="topbar__icon-btn"
                onClick={clearNotifications}
                aria-label="Clear all notifications"
                title="Clear all"
              >
                <Trash2 size={18} />
              </button>
            )}
            <button className="drawer__close" onClick={onClose} aria-label="Close notifications">
              <X size={24} />
            </button>
          </div>
        </div>

        <div className="notification-drawer__content">
          {notifications.length > 0 ? (
            <div className="notification-list">
              {notifications.map((notif) => {
                const type = typeFromData(notif.data) ;
                return (
                  <div key={notif.id} className="notification-item notification-item--unread">
                    <div className="notification-item__icon-wrapper">
                      {getIcon(type)}
                    </div>
                    <div className="notification-item__details">
                      <div className="notification-item__header">
                        <h4 className="notification-item__title">{notif.title}</h4>
                        <span className="notification-item__time">{relativeTime(notif.time)}</span>
                      </div>
                      <p className="notification-item__msg">{notif.body}</p>
                    </div>
                    <div className="notification-item__unread-dot" />
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="notification-empty">
              <Bell size={48} className="notification-empty__icon" />
              <p className="notification-empty__text">No new notifications yet.</p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '8px' }}>
                You'll see bus alerts here in real time.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
