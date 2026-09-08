/**
 * BusIdleAnimation.jsx
 * Lightweight CSS animation replacing the external GIF.
 */
import React from 'react';

export default function BusIdleAnimation({ nextTripTime, isUnscheduled }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginBottom: '24px' }}>
      <div
        className="bus-idle-wrap"
        style={{
          display: 'flex',
          justifyContent: 'center',
          background: '#ffffff',
          borderRadius: '20px',
          boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
          overflow: 'hidden'
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

      <div style={{
        background: '#ffffff',
        padding: '16px',
        borderRadius: '16px',
        boxShadow: '0 2px 12px rgba(0,0,0,0.06)',
        display: 'flex',
        flexDirection: 'column',
        gap: '16px'
      }}>
        {nextTripTime && !isUnscheduled && (
          <div style={{
            textAlign: 'center',
            fontWeight: '600',
            color: '#1f2937',
            fontSize: '16px'
          }}>
            Next Trip at - {nextTripTime}
          </div>
        )}

        <div style={{
          color: '#6b7280',
          padding: '0 8px 8px 8px',
          textAlign: 'center',
          fontSize: '13px',
          fontWeight: '500',
          whiteSpace: 'nowrap'
        }}>
          To know the bus current location, use the Map View.
        </div>
      </div>
    </div>
  );
}
