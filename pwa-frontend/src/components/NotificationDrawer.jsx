import React, { useEffect, useRef } from 'react';
import { X, Bell, AlertTriangle, CheckCircle, Info } from 'lucide-react';

const MOCK_NOTIFICATIONS = [
  {
    id: 1,
    title: 'Bus Arriving Soon',
    message: 'Your bus to Digital University Kerala will arrive at your stop in 5 minutes.',
    time: '2 mins ago',
    type: 'info',
    read: false,
  },
  {
    id: 2,
    title: 'Running Late',
    message: 'The evening bus is currently delayed by 10 minutes due to heavy traffic.',
    time: '1 hour ago',
    type: 'warning',
    read: true,
  },
  {
    id: 3,
    title: 'Trip Started',
    message: 'The morning trip from Central Polytechnic has just started.',
    time: 'Yesterday',
    type: 'success',
    read: true,
  },
];

const getIcon = (type) => {
  switch (type) {
    case 'warning': return <AlertTriangle size={18} className="notification-icon--warning" />;
    case 'success': return <CheckCircle size={18} className="notification-icon--success" />;
    default: return <Info size={18} className="notification-icon--info" />;
  }
};

export default function NotificationDrawer({ isOpen, onClose }) {
  const overlayRef = useRef(null);

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
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
          <button className="drawer__close" onClick={onClose} aria-label="Close notifications">
            <X size={24} />
          </button>
        </div>

        <div className="notification-drawer__content">
          {MOCK_NOTIFICATIONS.length > 0 ? (
            <div className="notification-list">
              {MOCK_NOTIFICATIONS.map((notif) => (
                <div key={notif.id} className={`notification-item ${!notif.read ? 'notification-item--unread' : ''}`}>
                  <div className="notification-item__icon-wrapper">
                    {getIcon(notif.type)}
                  </div>
                  <div className="notification-item__details">
                    <div className="notification-item__header">
                      <h4 className="notification-item__title">{notif.title}</h4>
                      <span className="notification-item__time">{notif.time}</span>
                    </div>
                    <p className="notification-item__msg">{notif.message}</p>
                  </div>
                  {!notif.read && <div className="notification-item__unread-dot" />}
                </div>
              ))}
            </div>
          ) : (
            <div className="notification-empty">
              <Bell size={48} className="notification-empty__icon" />
              <p className="notification-empty__text">You have no new notifications.</p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
