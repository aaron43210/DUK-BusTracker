// src/components/IdleChip.jsx
const CLOCK_ICON = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22a9 9 0 1 0 0-18 9 9 0 0 0 0 18z" />
    <path d="M12 8v4l2.5 2.5" />
  </svg>
);

const SIGNAL_ICON = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 12a9 9 0 0 1 18 0M7 12a5 5 0 0 1 10 0M11 12a1 1 0 0 1 2 0" />
  </svg>
);

export default function IdleChip({ status, nextTripTime }) {
  const connecting = status === 'connecting';
  const nextLabel  = nextTripTime ? `Next trip ${nextTripTime}` : '';

  let icon = CLOCK_ICON;
  let text, sub;

  if (connecting) {
    icon = SIGNAL_ICON;
    text = 'Connecting to bus…';
    sub  = 'Trip is scheduled';
  } else if (status === 'weekend') {
    text = 'Weekend — no service';
    sub  = nextLabel;
  } else if (status === 'cancelled') {
    text = 'Trip cancelled';
    sub  = nextLabel;
  } else {
    text = 'Not in Service';
    sub  = nextLabel || 'Waiting for schedule';
  }

  return (
    <div className={`idle-chip${connecting ? ' idle-chip--connecting' : ''}`}>
      <div className="idle-icon">{icon}</div>
      <div>
        <div>{text}</div>
        {sub && <div className="idle-sub">{sub}</div>}
      </div>
    </div>
  );
}
