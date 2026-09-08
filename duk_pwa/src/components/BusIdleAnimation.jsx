/**
 * BusIdleAnimation.jsx
 * Lightweight CSS animation replacing the external GIF.
 */
import React from 'react';

export default function BusIdleAnimation({ nextTripTime, isUnscheduled }) {
  return (
    <div
      className="bus-idle-wrap"
      style={{
        display: 'flex',
        justifyContent: 'center',
        background: '#ffffff',
        borderRadius: '20px',
        boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
        overflow: 'hidden',
        marginBottom: '24px'
      }}
    >
      <img
        src="/bus_animation.gif"
        alt="Bus Animation"
        style={{
          width: '100%',
          borderRadius: '16px',
          objectFit: 'cover',
          mixBlendMode: 'multiply'
        }}
      />
    </div>
  );
}
