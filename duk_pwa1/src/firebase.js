/**
 * firebase.js — DUK Bus Tracker PWA
 * Initialises the Firebase JS SDK and exports helpers for:
 *   - Requesting notification permission
 *   - Getting / refreshing the FCM device token
 *   - Saving the token to the backend
 *   - Listening for foreground push messages
 */
import { initializeApp, getApps } from 'firebase/app';
import { getMessaging, getToken, onMessage } from 'firebase/messaging';
import { apiFetch } from './api';

const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId:             import.meta.env.VITE_FIREBASE_APP_ID,
};

// Only initialize once (hot-reload guard)
const app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];

let _messaging = null;
function getMsg() {
  if (!_messaging) _messaging = getMessaging(app);
  return _messaging;
}

//  Save FCM token to backend 

async function saveTokenToBackend(token) {
  if (!token) return;
  try {
    // Use apiFetch so the JWT auto-refresh interceptor handles expired tokens.
    await apiFetch('/api/v1/auth/device-token', {
      method: 'PUT',
      body: JSON.stringify({ device_token: token, notifications_on: true }),
    });
    console.info('[FCM] Device token saved to backend.');
  } catch (e) {
    console.warn('[FCM] Could not save device token:', e);
  }
}

//  Request permission + register token 

/**
 * Called immediately after a successful login.
 * Asks the OS for notification permission, generates a FCM token, and
 * saves it to the backend.
 */
export async function requestAndSaveFcmToken() {
  try {
    if (!('Notification' in window)) {
      console.warn('[FCM] Notifications API not supported in this browser.');
      return;
    }
    
    // Vibrate before prompting to grab attention
    if (Notification.permission !== 'granted' && navigator.vibrate) {
      navigator.vibrate([200]);
    }
    
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      console.info('[FCM] Notification permission denied.');
      return;
    }

    const vapidKey = import.meta.env.VITE_FIREBASE_VAPID_KEY;
    if (!vapidKey) {
      console.error('[FCM] VITE_FIREBASE_VAPID_KEY is not set — token cannot be generated.');
      return;
    }

    const messaging = getMsg();
    const token = await getToken(messaging, {
      vapidKey,
      serviceWorkerRegistration: await navigator.serviceWorker.ready,
    });

    if (!token) {
      console.warn('[FCM] getToken() returned empty — check VAPID key and HTTPS setup.');
      return;
    }

    localStorage.setItem('duk_fcm_token', token);
    await saveTokenToBackend(token);
    console.info('[FCM] Device token registered successfully.');
  } catch (e) {
    console.warn('[FCM] requestAndSaveFcmToken failed:', e);
  }
}

/**
 * Called on every app load when the user is already logged in.
 * Silently refreshes the FCM token in case it has rotated.
 */
export async function refreshFcmToken() {
  try {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const messaging = getMsg();
    const token = await getToken(messaging, {
      vapidKey: import.meta.env.VITE_FIREBASE_VAPID_KEY,
      serviceWorkerRegistration: await navigator.serviceWorker.ready,
    });
    if (token) {
      const prev = localStorage.getItem('duk_fcm_token');
      if (token !== prev) {
        localStorage.setItem('duk_fcm_token', token);
        await saveTokenToBackend(token);
        console.info('[FCM] Device token refreshed.');
      }
    }
  } catch (e) {
    console.warn('[FCM] refreshFcmToken failed:', e);
  }
}

/**
 * Register a handler for messages received while the app is in the foreground.
 * The `handler` receives { title, body, data }.
 * Returns an unsubscribe function.
 */
export function onForegroundMessage(handler) {
  try {
    const messaging = getMsg();
    return onMessage(messaging, (payload) => {
      const title = payload.notification?.title || payload.data?.title || 'DUK Bus Tracker';
      const body  = payload.notification?.body  || payload.data?.body  || '';
      const data  = payload.data || {};
      handler({ title, body, data });
    });
  } catch (e) {
    console.warn('[FCM] onForegroundMessage failed:', e);
    return () => {};
  }
}
