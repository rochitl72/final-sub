import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// The FastAPI backend serves dist/ at "/" in production. In `npm run dev`
// Vite proxies the API to the backend so the app is always same-origin.
const BACKEND = process.env.DRIVELEGAL_API || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: false, // registered in src/main.tsx (auto-reloads on a new deploy)
      manifest: false, // we ship our own public/manifest.webmanifest
      includeAssets: ['logo.png', 'apple-touch-icon.png', 'icon-192.png', 'icon-512.png', 'icon-maskable-512.png'],
      workbox: {
        globPatterns: ['**/*.{js,css,html,png,svg,ico,json,webmanifest,woff2}'],
        maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/auth\//, /^\/viz/, /^\/docs/, /^\/openapi/],
        runtimeCaching: [
          {
            urlPattern: /\/api\/catalogs\//,
            handler: 'StaleWhileRevalidate',
            options: { cacheName: 'dl-catalogs', expiration: { maxAgeSeconds: 7 * 86400 } },
          },
          {
            urlPattern: /\/api\/sessions(\/[^/]+\/history)?(\?.*)?$/,
            handler: 'NetworkFirst',
            options: { cacheName: 'dl-sessions', networkTimeoutSeconds: 4, expiration: { maxEntries: 60, maxAgeSeconds: 30 * 86400 } },
          },
          {
            urlPattern: /^https:\/\/[abc]?\.?tile\.openstreetmap\.org\//,
            handler: 'CacheFirst',
            options: { cacheName: 'dl-tiles', expiration: { maxEntries: 400, maxAgeSeconds: 14 * 86400 }, cacheableResponse: { statuses: [0, 200] } },
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': BACKEND,
      '/auth': BACKEND,
    },
  },
  build: {
    target: 'es2020',
    chunkSizeWarningLimit: 900,
  },
});
