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
  const jwt = localStorage.getItem('duk_jwt_token');
  if (!jwt || !token) return;
  const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
  try {
    await fetch(`${BASE}/api/v1/auth/device-token`, {
      method:  'PUT',
      headers: {
        'Content-Type':  'application/json',
        'Authorization': `Bearer ${jwt}`,
      },
      body: JSON.stringify({ device_token: token, notifications_on: true }),
    });
  } catch (e) {
    console.warn('[FCM] Could not save device token:', e);
  }
}

//  Request permission + register token 

/**
 * Called immediately after a successful OTP login.
 * Asks the OS for notification permission, generates a FCM token, and
 * saves it to the backend.
 */
export async function requestAndSaveFcmToken() {
  try {
    if (!('Notification' in window)) return;
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      console.info('[FCM] Notification permission denied.');
      return;
    }
    const messaging = getMsg();
    const token = await getToken(messaging, {
      vapidKey:           import.meta.env.VITE_FIREBASE_VAPID_KEY,
      serviceWorkerRegistration: await navigator.serviceWorker.ready,
    });
    if (token) {
      localStorage.setItem('duk_fcm_token', token);
      await saveTokenToBackend(token);
      console.info('[FCM] Device token registered.');
    }
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
