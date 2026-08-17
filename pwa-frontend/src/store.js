/**
 * store.js — DUK Bus Tracker PWA
 * Lightweight global memory cache to instantly share state between tabs without full re-renders.
 */

const CACHE_KEY = 'duk_bus_tracker_cache';

const loadCache = () => {
  try {
    const data = localStorage.getItem(CACHE_KEY);
    if (data) return JSON.parse(data);
  } catch (e) { }
  return null;
};

const defaultState = {
  tripState: null,
  busPosition: null,
  stops: [],
  history: [],
  plannedCoords: []
};

export const globalStore = loadCache() || defaultState;

// Debounced write — avoids blocking the main thread on every 15s poll / every GPS tick.
let _saveTimer = null;
export const saveStoreToCache = () => {
  if (_saveTimer) return; // already scheduled
  _saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(globalStore));
    } catch (e) { /* quota exceeded — ignore */ }
    _saveTimer = null;
  }, 500);
};
