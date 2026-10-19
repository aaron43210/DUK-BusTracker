/**
 * storage.js
 * Safe localStorage helpers.
 */

const TOKEN_KEY = 'duk_jwt_token';
const REFRESH_TOKEN_KEY = 'duk_refresh_token';
const USER_KEY = 'duk_user_data';

function safeGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Ignore storage quota/private-mode errors.
  }
}

function safeRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    // Ignore.
  }
}

const TOKEN_EXPIRY_KEY = 'duk_jwt_exp';

export function saveToken(token) {
  safeSet(TOKEN_KEY, token);
  // Decode expiry from JWT payload so we can proactively refresh before expiry
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    if (payload.exp) safeSet(TOKEN_EXPIRY_KEY, String(payload.exp));
  } catch {
    // ignore malformed token
  }
}

export function getToken() {
  return safeGet(TOKEN_KEY);
}

/** Returns seconds until token expires (negative if already expired). */
export function getTokenSecondsLeft() {
  const exp = parseInt(safeGet(TOKEN_EXPIRY_KEY) || '0', 10);
  if (!exp) return -1;
  return exp - Math.floor(Date.now() / 1000);
}

export function clearToken() {
  safeRemove(TOKEN_KEY);
  safeRemove(TOKEN_EXPIRY_KEY);
}

export function saveRefreshToken(token) {
  safeSet(REFRESH_TOKEN_KEY, token);
}

export function getRefreshToken() {
  return safeGet(REFRESH_TOKEN_KEY);
}

export function clearRefreshToken() {
  safeRemove(REFRESH_TOKEN_KEY);
}

export function saveUser(user) {
  safeSet(USER_KEY, JSON.stringify(user || null));
  window.dispatchEvent(new Event('user-updated'));
}

export function getUser() {
  const raw = safeGet(USER_KEY);
  if (!raw) return null;

  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function clearSession() {
  safeRemove(TOKEN_KEY);
  safeRemove(REFRESH_TOKEN_KEY);
  safeRemove(USER_KEY);
}

const ALERTS_KEY = 'duk_alert_prefs';

export function saveAlertPreferences(prefs) {
  safeSet(ALERTS_KEY, JSON.stringify(prefs || {}));
}

export function getAlertPreferences() {
  const raw = safeGet(ALERTS_KEY);
  if (!raw) return { vibrateEnabled: true, soundEnabled: true, masterAlertsEnabled: false };

  try {
    const prefs = JSON.parse(raw);
    return {
      vibrateEnabled: prefs.vibrateEnabled !== false,
      soundEnabled: prefs.soundEnabled !== false,
      masterAlertsEnabled: prefs.masterAlertsEnabled === true
    };
  } catch {
    return { vibrateEnabled: true, soundEnabled: true, masterAlertsEnabled: false };
  }
}

// Stash for the pickup/drop-off alert stop IDs. The master "Enable all
// Notifications" toggle disables location alerts by nulling these stops
// server-side (a null stop = no alert), which would otherwise destroy the
// user's selection. We remember them here on disable so they can be restored
// when the master toggle is turned back on.
const ALERT_STASH_KEY = 'duk_alert_stash';

export function saveAlertStash(stash) {
  safeSet(ALERT_STASH_KEY, JSON.stringify(stash || {}));
}

export function getAlertStash() {
  const raw = safeGet(ALERT_STASH_KEY);
  if (!raw) return { boarding: null, destination: null };
  try {
    const s = JSON.parse(raw);
    return { boarding: s.boarding ?? null, destination: s.destination ?? null };
  } catch {
    return { boarding: null, destination: null };
  }
}
