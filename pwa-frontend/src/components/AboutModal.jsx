import React from 'react';
import { X } from 'lucide-react';

export default function AboutModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content about-modal" onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">
          <X size={20} />
        </button>
        <div className="about-brand">
          <img src="/duk-logo.png" alt="DUK Logo" style={{ height: '60px', objectFit: 'contain', marginBottom: '16px' }} />
          <h2 className="modal-title">About the Project</h2>
        </div>
        <p className="about-text">
          Developed By <strong>A Muhammed khais e</strong> and <strong>AARON R</strong> as a project supervised by <strong>Dr. John Eric Stephen</strong> with collaboration with <strong>CAN LAB DIGITAL UNIVERSITY KERALA</strong>.
        </p>
      </div>
    </div>
  );
}
