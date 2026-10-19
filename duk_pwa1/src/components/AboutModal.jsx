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
          <img src="./duk-logo.webp" alt="DUK Logo" width={285} height={136} loading="lazy" decoding="async" style={{ height: '60px', width: 'auto', objectFit: 'contain', marginBottom: '16px' }} />
          <h2 className="modal-title">About the Project</h2>
        </div>
        <p className="about-text" style={{ margin: '0 auto', lineHeight: '1.6' }}>
          Developed By{' '}
          <a href="https://www.linkedin.com/in/muhammed-khaise/" target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none', color: 'inherit' }}>
            <strong style={{ whiteSpace: 'nowrap' }}>A Muhammed Khaise</strong>
          </a>
          {' '}and{' '}
          <a href="https://www.linkedin.com/in/aaronr35/" target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none', color: 'inherit' }}>
            <strong style={{ whiteSpace: 'nowrap' }}>Aaron R</strong>
          </a><br />
          as a project supervised by{' '}
          <a href="https://duk.ac.in/personnel/john-eric-steephen/" target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none', color: 'inherit' }}>
            <strong style={{ whiteSpace: 'nowrap' }}>Dr. John Eric Stephen</strong>
          </a><br />
          with collaboration with{' '}
          <a href="https://sites.google.com/iiitmk.ac.in/hed" target="_blank" rel="noopener noreferrer" style={{ textDecoration: 'none', color: 'inherit' }}>
            <strong style={{ whiteSpace: 'nowrap' }}>CAN LAB, DIGITAL UNIVERSITY KERALA</strong>
          </a>.
        </p>
      </div>
    </div>
  );
}
