/**
 * sw.js
 * Single production service worker for:
 * 1. PWA precaching
 * 2. Runtime map tile caching
 * 3. Firebase background notifications
 *
 * Important:
 * Do not register another root-scoped service worker.
 */

import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching';
import { registerRoute } from 'workbox-routing';
import { CacheFirst, NetworkOnly } from 'workbox-strategies';
import { ExpirationPlugin } from 'workbox-expiration';
import { CacheableResponsePlugin } from 'workbox-cacheable-response';

import { initializeApp } from 'firebase/app';
import { getMessaging, onBackgroundMessage } from 'firebase/messaging/sw';

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST || []);

/**
 * Cache map tiles.
 * Map tiles are safe to cache because they are static and expensive to reload.
 */
registerRoute(
  ({ url }) => url.origin === 'https://tiles.openfreemap.org',
  new CacheFirst({
    cacheName: 'map-tiles-cache',
    plugins: [
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      new ExpirationPlugin({
        maxEntries: 500,
        maxAgeSeconds: 60 * 60 * 24 * 7
      })
    ]
  })
);

/**
 * Real-time API routes must always hit the network.
 */
registerRoute(
  ({ url }) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/auth/'),
  new NetworkOnly()
);

/**
 * Firebase config.
 * These values are public client identifiers, not private secrets.
 */
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID
};

if (firebaseConfig.apiKey && firebaseConfig.messagingSenderId) {
  const firebaseApp = initializeApp(firebaseConfig);
  const messaging = getMessaging(firebaseApp);

  onBackgroundMessage(messaging, (payload) => {
    const title =
      payload.notification?.title ||
      payload.data?.title ||
      'DUK Bus Tracker';

    const body =
      payload.notification?.body ||
      payload.data?.body ||
      '';

    const tag =
      payload.data?.type === 'proximity'
        ? `proximity-${payload.data?.stop_id || 'stop'}`
        : payload.data?.type || 'duk-bus';

    self.registration.showNotification(title, {
      body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag,
      renotify: false,
      data: payload.data || {},
      vibrate: [200, 100, 200]
    });
  });
}

/**
 * Open/focus app when notification is clicked.
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
