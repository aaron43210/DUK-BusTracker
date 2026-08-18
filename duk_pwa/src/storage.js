/**
 * storage.js — DUK Bus Tracker PWA
 * localStorage helpers — mirrors the React Native AsyncStorage pattern
 * so logic can be kept nearly identical between RN and PWA.
 */

const TOKEN_KEY = 'duk_jwt_token';
const USER_KEY  = 'duk_user_data';

// ── Token ──────────────────────────────────────────────────────────────────

export function saveToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// ── User ───────────────────────────────────────────────────────────────────

export function saveUser(user) {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function getUser() {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// ── Session ────────────────────────────────────────────────────────────────

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
