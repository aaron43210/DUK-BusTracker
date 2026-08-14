/**
 * main.jsx — DUK Bus Tracker PWA
 * React entry point — renders App into #root and registers the Firebase
 * Cloud Messaging service worker for background push notifications.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

// Register the Firebase Messaging Service Worker so push notifications
// are delivered even when the app is closed / backgrounded.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker
    .register('/firebase-messaging-sw.js', { scope: '/' })
    .then((reg) => console.info('[SW] Firebase SW registered:', reg.scope))
    .catch((err) => console.warn('[SW] Firebase SW registration failed:', err));
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
