import React, { useEffect, useRef, useState } from 'react';
import { X, Bell, AlertTriangle, CheckCircle, Info, Trash2 } from 'lucide-react';
import { useNotifications } from '../App';
import { getMyNotifications, markNotificationRead } from '../api';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Map FCM data.type to a visual icon variant */
function typeFromData(data) {
  if (!data) return 'info';
  if (data.type === 'eta_late') return 'warning';
  if (data.type === 'proximity') return 'success';
  if (data.type === 'suggestion') return 'success';
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
  const { notifications: contextNotifications, clearNotifications, markRead } = useNotifications();
  const [historyNotifications, setHistoryNotifications] = useState([]);
  const [dismissedIds, setDismissedIds] = useState(() => {
    try {
      const saved = localStorage.getItem('dismissed_notifications');
      return saved ? JSON.parse(saved) : [];
    } catch (e) {
      return [];
    }
  });

  const handleDismiss = async (id) => {
    setDismissedIds(prev => {
      const next = [...prev, id];
      localStorage.setItem('dismissed_notifications', JSON.stringify(next));
      return next;
    });
    // If it's a numeric ID, it came from the backend database, so mark it read there
    if (typeof id === 'number') {
      try {
        await markNotificationRead(id);
      } catch (e) {
        console.error('Failed to mark notification read', e);
      }
    }
  };

  useEffect(() => {
    if (isOpen && markRead) {
      markRead();
    }
  }, [isOpen, markRead]);

  useEffect(() => {
    document.body.style.overflow = isOpen ? 'hidden' : '';
    if (isOpen) {
      getMyNotifications().then(data => {
        if (data && data.notifications) {
          const formatted = data.notifications.map(n => ({
            id: n.id,
            title: n.notification?.title || 'Notification',
            body: n.notification?.body || '',
            time: n.time ? new Date(n.time) : new Date(),
            data: { type: n.type }
          }));
          setHistoryNotifications(formatted);
        }
      }).catch(err => console.error('Failed to load notification history', err));
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const handleOverlayClick = (e) => {
    if (e.target === overlayRef.current) onClose();
  };

  // Merge contextual (live) notifications and historical ones, avoiding duplicates by ID
  const seenIds = new Set(contextNotifications.map(cn => cn.id));
  let allNotifications = [...contextNotifications];
  
  historyNotifications.forEach(hn => {
    if (!seenIds.has(hn.id)) {
      allNotifications.push(hn);
      seenIds.add(hn.id);
    }
  });
  
  // Filter out dismissed notifications using Set for O(1) lookups
  const dismissedSet = new Set(dismissedIds);
  allNotifications = allNotifications.filter(n => !dismissedSet.has(n.id));
  allNotifications.sort((a, b) => b.time - a.time);

  const handleClearAll = () => {
    const idsToDismiss = allNotifications.map(n => n.id);
    setDismissedIds(prev => {
      const next = [...new Set([...prev, ...idsToDismiss])];
      localStorage.setItem('dismissed_notifications', JSON.stringify(next));
      return next;
    });
    clearNotifications();
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div
        className="modal-content notification-modal"
        role="dialog"
        aria-label="Notifications"
        onClick={e => e.stopPropagation()}
        style={{ padding: '24px', display: 'flex', flexDirection: 'column', maxHeight: '80vh' }}
      >
        <button className="modal-close" onClick={onClose} aria-label="Close notifications">
          <X size={20} />
        </button>
        
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
          <h2 className="modal-title" style={{ marginBottom: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Bell size={20} /> Notifications
          </h2>
          {allNotifications.length > 0 && (
            <button
              className="btn btn--primary"
              onClick={handleClearAll}
              style={{ padding: '6px 12px', fontSize: '12px', width: 'auto', marginRight: '36px' }}
            >
              Clear all
            </button>
          )}
        </div>

        <div className="notification-drawer__content" style={{ overflowY: 'auto', padding: '0 4px', margin: '0 -4px' }}>
          {allNotifications.length > 0 ? (
            <div className="notification-list">
              {allNotifications.map((notif) => {
                const type = typeFromData(notif.data);
                return (
                  <div key={notif.id} className="notification-item notification-item--unread">
                    <div className="notification-item__details" style={{ flex: 1, marginRight: '12px' }}>
                      <div className="notification-item__header">
                        <h4 className="notification-item__title">{notif.title}</h4>
                        <span className="notification-item__time">{relativeTime(notif.time)}</span>
                      </div>
                      <p className="notification-item__msg">{notif.body}</p>
                    </div>
                    <button 
                      onClick={() => handleDismiss(notif.id)}
                      style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'flex-start', padding: '4px' }}
                      aria-label="Remove notification"
                    >
                      <X size={16} />
                    </button>
                    <div className="notification-item__unread-dot" />
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="notification-empty" style={{ padding: '40px 0' }}>
              <Bell size={48} className="notification-empty__icon" />
              <p className="notification-empty__text">No new notifications yet.</p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '8px' }}>
                You'll see bus alerts here in real time.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
