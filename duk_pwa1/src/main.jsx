/**
 * main.jsx
 * App entry point.
 * Registers one PWA service worker and mounts React.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import { registerSW } from 'virtual:pwa-register';

import './index.css';
// Liquid-glass theme overlay. Rules are scoped under `.theme-liquid-glass`
// (set on <html>/<body> in index.html) and override the index.css base.
import './liquid-glass.css';

import App from './App';
import { initKeyboardInsets } from './utils/keyboardInsets';

if (window.__BLOCKED__) {
  console.warn("Execution blocked by iOS gate");
} else {
  // Register after the window load event so precaching doesn't compete with
  // the first page load for bandwidth.
  registerSW({
    immediate: false,
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
  
  initKeyboardInsets();

  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
