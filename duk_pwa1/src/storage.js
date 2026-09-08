/**
 * storage.js
 * Safe localStorage helpers.
 */

const TOKEN_KEY = 'duk_jwt_token';
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

export function saveToken(token) {
  safeSet(TOKEN_KEY, token);
}

export function getToken() {
  return safeGet(TOKEN_KEY);
}

export function clearToken() {
  safeRemove(TOKEN_KEY);
}

export function saveUser(user) {
  safeSet(USER_KEY, JSON.stringify(user || null));
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
  safeRemove(USER_KEY);
}

const UI_PREF_KEY = 'duk_ui_pref';

export function setUIPref(isUI2) {
  safeSet(UI_PREF_KEY, isUI2 ? 'ui2' : 'ui1');
}

export function getUIPref() {
  return safeGet(UI_PREF_KEY) === 'ui2';
}
