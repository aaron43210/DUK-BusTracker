import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    react(),

    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.js',
      registerType: 'autoUpdate',
      injectRegister: false,

      includeAssets: [
        'favicon.ico',
        'buslogo.png',
        'icon-192.png',
        'icon-512.png',
        'duk-logo.png',
        'canlab.png',
        'bus_green.png',
        'bus_gray.png',
        'offline.html'
      ],

      manifest: {
        name: 'DUK Bus Tracker',
        short_name: 'DUK Bus',
        description: 'Real-time bus tracking for Digital University Kerala',
        theme_color: '#A2D7C3',
        background_color: '#f4f5f7',
        display: 'standalone',
        scope: '/',
        start_url: '/',
        orientation: 'portrait',
        icons: [
          {
            src: 'icon-192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'any maskable'
          },
          {
            src: 'icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable'
          }
        ]
      },

      injectManifest: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}']
      }
    })
  ],

  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:80',
        changeOrigin: true,
        ws: true
      },
      '/auth': {
        target: 'http://127.0.0.1:80',
        changeOrigin: true
      }
    }
  }
});
