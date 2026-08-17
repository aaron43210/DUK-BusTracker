// src/components/StatusPill.jsx
import { STATUS_LABEL } from '../utils/format';

export default function StatusPill({ status, label }) {
  return (
    <span className={`pill pill--${status || 'offline'}`}>
      <span className="dot" />
      {label || STATUS_LABEL[status] || 'Unknown'}
    </span>
  );
}
