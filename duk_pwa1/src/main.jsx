/**
 * main.jsx
 * App entry point.
 * Registers one PWA service worker and mounts React.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { registerSW } from 'virtual:pwa-register';

import 'maplibre-gl/dist/maplibre-gl.css';
import './index.css';

import App from './App';

registerSW({
  immediate: true,
  onRegisteredSW(swUrl, registration) {
    console.info('[PWA] Service worker registered:', swUrl);

    // Periodically check for app updates.
    if (registration) {
      setInterval(
        () => registration.update().catch(() => {}),
        60 * 60 * 1000
      );
    }
  },
  onRegisterError(error) {
    console.warn('[PWA] Service worker registration failed:', error);
  }
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
