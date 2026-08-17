/**
 * Toast.jsx — DUK Bus Tracker PWA
 * Global in-app notification toast system.
 * Usage: import { useToast } from '../App'; showToast('message', 'success')
 */
import React, { useEffect, useState } from 'react';

export function ToastItem({ toast, onRemove }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Trigger entrance animation
    const t = setTimeout(() => setVisible(true), 10);
    return () => clearTimeout(t);
  }, []);

  const handleRemove = () => {
    setVisible(false);
    setTimeout(onRemove, 300);
  };

  return (
    <div
      className={`toast toast--${toast.type} ${visible ? 'toast--visible' : ''}`}
      onClick={handleRemove}
      role="alert"
    >
      <span className="toast__msg">{toast.msg}</span>
    </div>
  );
}

export function ToastContainer({ toasts, onRemove }) {
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} onRemove={() => onRemove(t.id)} />
      ))}
    </div>
  );
}
