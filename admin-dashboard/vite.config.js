// vite.config.js
// Vite is the build tool that powers this React app.
// This file configures the dev server and how the app is built.

import { defineConfig } from 'vite';   // defineConfig gives us TypeScript-style autocomplete
import react from '@vitejs/plugin-react'; // the official Vite plugin that transforms JSX → JS

export default defineConfig({

  // plugins: list of Vite plugins to activate
  // @vitejs/plugin-react handles JSX, Fast Refresh (hot reload), and React-specific transforms
  plugins: [react()],

  // server: dev server configuration (only used during `npm run dev`)
  server: {

    // port: run the dev server on port 5173 (Vite's default)
    port: 5173,

    // proxy: forward certain URL prefixes to the backend instead of trying to serve them as static files
    // This avoids CORS issues in development — the browser thinks everything comes from localhost:5173
    proxy: {

      // Any request that starts with /api will be forwarded to the FastAPI backend
      '/api': {
        target: 'http://127.0.0.1:5004', // FastAPI runs on port 5004 (see backend/main.py)
        changeOrigin: true,              // rewrite the Host header to match the target
        ws: true,                        // enable WebSocket proxying
      },

      // Any request that starts with /admin/api will also be forwarded to FastAPI
      // (admin routes are under /admin/api/*)
      '/admin/api': {
        target: 'http://127.0.0.1:5004',
        changeOrigin: true,
      },
    },
  },
});
