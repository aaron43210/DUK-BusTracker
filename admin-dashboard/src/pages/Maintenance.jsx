import React, { useState } from 'react';


export default function Maintenance({ setServerIsDown }) {
  const [isRetrying, setIsRetrying] = useState(false);

  const handleRetry = async () => {
    setIsRetrying(true);
    try {
      // Ping the server to see if it's back up using raw fetch to avoid apiFetch interceptors
      const res = await fetch('/api/v1/latest');
      if (res.ok || res.status === 401 || res.status === 403) {
        // If we got here, the API call succeeded and the server is back!
        // Even if unauthorized, it means the server is reachable
        setServerIsDown(false);
      }
    } catch (err) {
      // Still down
    } finally {
      setTimeout(() => setIsRetrying(false), 800); // little delay for animation
    }
  };

  return (
    <div className="maintenance-container">
      <div className="maintenance-content">
        <div className="maintenance-icon">
          {/* Animated SVG icon */}
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
            <line x1="12" y1="9" x2="12" y2="13"></line>
            <line x1="12" y1="17" x2="12.01" y2="17"></line>
          </svg>
        </div>
        <h1 className="maintenance-title">System Offline</h1>
        <p className="maintenance-subtitle">
          The DUK Bus Tracker server is currently unreachable or under maintenance. 
          Our team has been notified.
        </p>
        
        <button 
          className={`maintenance-btn ${isRetrying ? 'retrying' : ''}`} 
          onClick={handleRetry}
          disabled={isRetrying}
        >
          {isRetrying ? 'Checking Connection...' : 'Retry Connection'}
        </button>
        
        <div className="maintenance-status">
          <div className="maintenance-status-dot"></div>
          <span>Waiting for server response</span>
        </div>
      </div>
    </div>
  );
}
