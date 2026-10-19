import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    base: '/bus/ios/mobile/',
    define: {
      'import.meta.env.VITE_FIREBASE_API_KEY': JSON.stringify(env.VITE_FIREBASE_API_KEY),
      'import.meta.env.VITE_FIREBASE_AUTH_DOMAIN': JSON.stringify(env.VITE_FIREBASE_AUTH_DOMAIN),
      'import.meta.env.VITE_FIREBASE_PROJECT_ID': JSON.stringify(env.VITE_FIREBASE_PROJECT_ID),
      'import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID': JSON.stringify(env.VITE_FIREBASE_MESSAGING_SENDER_ID),
      'import.meta.env.VITE_FIREBASE_APP_ID': JSON.stringify(env.VITE_FIREBASE_APP_ID),
    },
  plugins: [
    react(),

    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.js',
      registerType: 'autoUpdate',
      injectRegister: false,

      // Launcher icons are fetched by the OS from the manifest, so only the
      // notification icon and the offline page need precaching here.
      includeAssets: [
        'icon-192.png',
        'offline.html'
      ],

      manifest: {
        name: 'DUK Bus Tracker',
        short_name: 'DUK Bus',
        description: 'Real-time bus tracking for Digital University Kerala',
        theme_color: '#FFFFFF',
        background_color: '#f4f5f7',
        display: 'standalone',
        scope: '/bus/ios/mobile/',
        start_url: '/bus/ios/mobile/',
        orientation: 'portrait',
        // PNG icons generated from public/duk-logo.png via scripts/make-icons.sh.
        // `any` = full-bleed launcher icon; `maskable` = padded for Android's crop.
        icons: [
          {
            src: 'icon-192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'any'
          },
          {
            src: 'icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any'
          },
          {
            src: 'icon-maskable-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable'
          }
        ]
      },

      injectManifest: {
        // App shell + the small UI images (all .webp). The idle bus animation is
        // large and only shown when no trip is running, so it's left to the HTTP cache.
        globPatterns: ['**/*.{js,css,html,webp,svg,woff2}'],
        globIgnores: ['**/bus_animation.*']
      }
    })
  ],

  server: {
    port: 5175,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:80',
        changeOrigin: true,
        ws: true
      }
    }
  }
  };
});
