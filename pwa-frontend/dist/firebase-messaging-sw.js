/**
 * firebase-messaging-sw.js — DUK Bus Tracker PWA
 *
 * Firebase Cloud Messaging Service Worker.
 * This file MUST live at the root of the served site (public/).
 * It handles push notifications when the app is CLOSED or in the background.
 *
 * Uses Firebase CDN imports because bundlers (Vite) cannot process service workers.
 *
 * To update the Firebase config, change the values below to match your
 * Firebase project settings (Firebase Console → Project Settings → Your apps).
 */

importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey:            self.__FIREBASE_API_KEY__            || 'AIzaSyDJXJfKG18-9YdQhHnrWfHtzUpKaIkdSRE',
  authDomain:        self.__FIREBASE_AUTH_DOMAIN__        || 'duk-bus-tracker.firebaseapp.com',
  projectId:         self.__FIREBASE_PROJECT_ID__         || 'duk-bus-tracker',
  messagingSenderId: self.__FIREBASE_MESSAGING_SENDER_ID__ || '773406220529',
  appId:             self.__FIREBASE_APP_ID__             || '1:773406220529:web:386ffb0a35b31ea944c950',
});

const messaging = firebase.messaging();

/**
 * Background message handler.
 * Called when a push arrives and the PWA is NOT in the foreground.
 */
messaging.onBackgroundMessage((payload) => {
  const title = payload.notification?.title || 'DUK Bus Tracker';
  const body  = payload.notification?.body  || '';
  const icon  = '/icon-192.png';

  // Map FCM data type to a notification tag so duplicate alerts are collapsed
  const tag = payload.data?.type === 'proximity'
    ? `proximity-${payload.data?.stop_id}`
    : payload.data?.type || 'duk-bus';

  self.registration.showNotification(title, {
    body,
    icon,
    badge:  '/icon-192.png',
    tag,
    renotify: false,
    data: payload.data || {},
    vibrate: [200, 100, 200],
  });
});

/**
 * Notification click handler.
 * Opens (or focuses) the PWA when the user taps the OS notification.
 */
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      return clients.openWindow('/route');
    })
  );
});
