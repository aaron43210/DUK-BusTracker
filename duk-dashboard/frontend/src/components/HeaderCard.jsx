// src/components/HeaderCard.jsx
import StatusPill from './StatusPill';

const BUS_SVG = (
  <svg viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="1.9"
       strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 17a3 3 0 0 0 3 3v2h3v-2h2v2h3v-2a3 3 0 0 0 3-3V8a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v9z" />
    <path d="M5.5 11h13M5.5 14.5h13" />
  </svg>
);

export default function HeaderCard({ status, lateByMinutes, isActive, isDemo, clock }) {
  return (
    <div className="header-card">
      <div className="hc-logo">{BUS_SVG}</div>
      <div className="hc-text">
        <div className="hc-title">DUK Bus Tracker</div>
        <div className="hc-sub">
          Live dashboard · Trivandrum
          <span className="hc-clock">{clock} IST</span>
        </div>
      </div>
      <div className="hc-right">
        {isActive && lateByMinutes > 2 && (
          <span className="chip-late">Delayed {lateByMinutes}m</span>
        )}
        {isDemo && <span className="chip-demo">Demo</span>}
        <StatusPill status={status} />
      </div>
    </div>
  );
}
