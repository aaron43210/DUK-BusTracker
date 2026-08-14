// src/main.jsx
// ─────────────────────────────────────────────────────────────────────────────
// This is the ENTRY POINT of the React application.
// Vite loads this file first (configured in index.html as <script type="module">).
// Its only job: mount the <App /> component into the real HTML DOM.
// ─────────────────────────────────────────────────────────────────────────────

import { StrictMode } from 'react';       // StrictMode runs extra checks in development only (not production)
import { createRoot } from 'react-dom/client'; // React 18's way to create the root DOM node
import './index.css';                     // import global styles so they apply to the whole app
import App from './App.jsx';              // our root component that contains routing + layout

// document.getElementById('root') finds the <div id="root"> in index.html
// That is the single HTML element React "takes over" and renders into
const root = createRoot(document.getElementById('root'));

// root.render() tells React "put this component tree into the #root div"
// StrictMode wraps App to catch common mistakes like deprecated APIs in development
root.render(
  <StrictMode>
    <App />   {/* All pages, routing, and layout live inside App */}
  </StrictMode>
);
