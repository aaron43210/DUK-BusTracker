import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const BACKEND = 'http://127.0.0.1:8000'

// Paths that belong to the FastAPI backend are proxied so the browser only
// ever talks to the app origin (no CORS, works through any preview proxy).
const API_PATHS = [
  '/api/v1'
]

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
    proxy: Object.fromEntries(
      API_PATHS.map((p) => [
        p,
        { target: BACKEND, changeOrigin: true, ws: true },
      ])
    ),
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    allowedHosts: true,
    proxy: Object.fromEntries(
      API_PATHS.map((p) => [
        p,
        { target: BACKEND, changeOrigin: true, ws: true },
      ])
    ),
  },
})
