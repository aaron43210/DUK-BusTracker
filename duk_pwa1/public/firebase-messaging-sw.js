importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging-compat.js');

// Standard Firebase config (these keys are safe to be public in a service worker)
const firebaseConfig = {
  apiKey: "AIzaSyA6-LuwDI5IQq6boL7hKcNTxNNYpwvRUAE",
  authDomain: "dukbus-bcf10.firebaseapp.com",
  projectId: "dukbus-bcf10",
  messagingSenderId: "156522756954",
  appId: "1:156522756954:web:8af8b928849674293fd163"
};

firebase.initializeApp(firebaseConfig);

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  console.log('[firebase-messaging-sw.js] Received background message ', payload);
  
  // If the payload has a 'notification' object, the browser handles it automatically.
  // We only need to manually show a notification if it's a data-only message.
  if (payload.notification) {
    return;
  }

  const notificationTitle = payload.data?.title || 'DUK Bus Tracker';
  const notificationOptions = {
    body: payload.data?.body || '',
    icon: '/favicon.svg',
    vibrate: [200, 100, 200, 100, 500],
    data: payload.data
  };

  self.registration.showNotification(notificationTitle, notificationOptions);
});
