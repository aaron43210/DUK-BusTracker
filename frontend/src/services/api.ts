/**
 * api.ts — DUK Bus Tracker mobile API client.
 * All requests go to the FastAPI backend (port 5004).
 *
 * For local development with an Android emulator, use:
 *   http://10.0.2.2:5004   (emulator → laptop localhost)
 * For a physical device on the same WiFi, use your laptop's local IP:
 *   http://192.168.x.x:5004
 * For the college server:
 *   http://103.156.188.51:5004
 */

import axios from 'axios';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

// ── Use 10.0.2.2 for Android Emulator, and localhost for iOS Simulator
const BASE_URL = Platform.OS === 'android' ? 'http://10.0.2.2:5004' : 'http://localhost:5004';

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
});

// ── JWT Interceptor — auto-attaches token to every request ─────────────────
api.interceptors.request.use(async (config) => {
  const token = await AsyncStorage.getItem('jwt_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── Auth ───────────────────────────────────────────────────────────────────
export const authApi = {
  /**
   * Step 1 — Submit name + @duk.ac.in email → backend sends OTP email.
   * Backend also accepts an optional boarding_stop_id to pre-populate profile.
   */
  register: (name: string, email: string, boarding_stop_id?: number) =>
    api.post('/auth/register', { name, email, boarding_stop_id }),

  /**
   * Step 2 — Submit OTP code → returns JWT + user object on success.
   * Response: { access_token, token_type, user: { id, name, email, boarding_stop_id } }
   */
  verify: (email: string, otp: string) =>
    api.post('/auth/verify', { email, otp }),

  /**
   * Update proximity alert settings (requires JWT).
   */
  updatePreferences: (prefs: {
    proximity_alert_enabled: boolean;
    proximity_alert_type?: string;
    proximity_alert_value?: number;
    proximity_alert_for?: string;
    notifications_on?: boolean;
  }) => api.patch('/auth/preferences', prefs),
};

// ── Tracking ───────────────────────────────────────────────────────────────
export const trackingApi = {
  /**
   * All bus stops from the database (for profile setup dropdown).
   */
  getStops: () => api.get('/api/v1/stops'),

  /**
   * Latest GPS ping — lat, lon, speed, is_live flag.
   */
  getLatest: () => api.get('/api/v1/latest'),

  /**
   * Current trip state — Morning/Evening trip, idle, or offline.
   * Response: { trip, status, next_trip_time? }
   */
  getTripState: () => api.get('/api/v1/trip_state'),

  /**
   * Today's visited stops and arrival times for the current session.
   * Response: { visitedStops: { stopName: true }, arrivalTimes: { stopName: "9:15 AM" } }
   */
  getRouteHistory: () => api.get('/api/v1/route_history'),

  /**
   * ML-predicted ETA to a specific bus stop.
   * Response: { stop_id, stop_name, eta_minutes, confidence, status }
   */
  getEta: (stop_id: number) => api.get('/api/v1/eta', { params: { stop_id } }),

  /**
   * GPS breadcrumb trail for a specific date (YYYY-MM-DD).
   * Used to draw the blue "where the bus has been" line on the map.
   * Response: Array of { lat, lon, speed_kmh, recorded_at }
   */
  getHistoryForDate: (dateStr: string) => api.get(`/api/v1/history/${dateStr}`),

  /**
   * Complete historical snapped route trace for a specific trip ID.
   * Response: Array of [lon, lat] coordinates.
   */
  getTripTrace: (tripId: number) => api.get(`/api/v1/trip_trace/${tripId}`),

  /**
   * Get the road geometry between two GPS coordinates
   */
  getRouteSegment: (lat1: number, lon1: number, lat2: number, lon2: number) =>
    api.get(`/api/v1/route_segment?lat1=${lat1}&lon1=${lon1}&lat2=${lat2}&lon2=${lon2}`),

  /**
   * Complete route geometry
   */
  getRouteGeometry: () => api.get('/api/v1/route_geometry'),

  /**
   * Snap arbitrary GPS points to road coordinates via OSRM.
   */
  snapRoute: (points: any[]) => api.post('/api/v1/snap_route', { points }),
};


// ── Suggestions ────────────────────────────────────────────────────────────
export const suggestionApi = {
  /**
   * Submit a text suggestion — works without auth (user_id attached if logged in).
   * @param suggestion  The suggestion text (max 500 chars).
   * @param trip        Optional trip label (e.g. "Morning Trip").
   * @param location    Optional location string.
   */
  submit: (suggestion: string, trip = '', location = '') =>
    api.post('/api/v1/suggestion', { suggestion, trip, location }),
};

export default api;
