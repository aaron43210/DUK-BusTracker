// src/utils/format.js
const IST_OPTS_FULL  = { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' };
const IST_OPTS_SHORT = IST_OPTS_FULL; // same options, post-processed

export function fmtClock(date) {
  return date.toLocaleTimeString('en-IN', IST_OPTS_FULL);
}

export function fmtClockShort(date) {
  return date.toLocaleTimeString('en-IN', IST_OPTS_SHORT)
    .replace(' ', '')
    .toLowerCase();
}

export function agoFromIso(iso) {
  if (!iso) return '—';
  const diff = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 3)  return 'just now';
  if (diff < 60) return `${Math.round(diff)}s ago`;
  return `${Math.round(diff / 60)}m ago`;
}

export const STATUS_LABEL = {
  active:    'On Trip',
  connecting:'Connecting',
  waiting:   'Scheduled',
  weekend:   'Weekend',
  offline:   'Not in Service',
  completed: 'Trip Completed',
  cancelled: 'Cancelled',
};

const EVENING = 'evening';

export function tripDestination(tripName) {
  return tripName?.toLowerCase().includes(EVENING)
    ? 'Central Polytechnic'
    : 'Digital University Kerala';
}

export function tripOrigin(tripName) {
  return tripName?.toLowerCase().includes(EVENING)
    ? 'Digital University Kerala'
    : 'Central Polytechnic';
}

export function isReverseDirection(tripName) {
  return Boolean(tripName?.toLowerCase().includes(EVENING));
}
