/**
 * Turns the Expo web export (dist/) into a PWA:
 *  - copies the manifest + icons, adds <link rel="manifest">, theme colour and iOS tags to index.html
 *  - generates dist/sw.js with Workbox (precache the app shell, cache catalogs / history / map tiles)
 *  - registers the service worker (auto-updates: a new deploy reloads open tabs once)
 * Run via `npm run build:web`.
 */
import { copyFileSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { generateSW } from 'workbox-build';

const DIST = 'dist';
for (const f of readdirSync('public-pwa')) copyFileSync(join('public-pwa', f), join(DIST, f));

const head = `
<link rel="manifest" href="/manifest.webmanifest" />
<meta name="theme-color" content="#050d1f" />
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="DriveLegal" />
<meta name="description" content="Exact traffic fines, sections and who is liable — for any road incident in India." />
<style>html,body{background:#000}</style>
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    var had = !!navigator.serviceWorker.controller, reloaded = false;
    navigator.serviceWorker.addEventListener('controllerchange', function () {
      if (had && !reloaded) { reloaded = true; location.reload(); }   // new version deployed
    });
    navigator.serviceWorker.register('/sw.js').catch(function (e) { console.warn('sw', e); });
  });
}
</script>`;
let html = readFileSync(join(DIST, 'index.html'), 'utf8');
html = html.replace(/<title>.*?<\/title>/, '<title>DriveLegal — Indian Road Law Assistant</title>');
if (!html.includes('rel="manifest"')) html = html.replace('</head>', head + '\n</head>');
writeFileSync(join(DIST, 'index.html'), html);

const { count, size, warnings } = await generateSW({
  globDirectory: DIST,
  globPatterns: ['**/*.{js,css,html,png,ico,ttf,json,webmanifest}'],
  // only the two icon fonts the app uses (Ionicons, MaterialCommunityIcons); other vector-icon fonts are never loaded
  globIgnores: ['metadata.json', '**/*.map', '**/vector-icons/**/Fonts/!(Ionicons|MaterialCommunityIcons).*.ttf'],
  maximumFileSizeToCacheInBytes: 8 * 1024 * 1024,
  swDest: join(DIST, 'sw.js'),
  skipWaiting: true,
  clientsClaim: true,
  cleanupOutdatedCaches: true,
  navigateFallback: '/index.html',
  navigateFallbackDenylist: [/^\/api\//, /^\/auth\//, /^\/classic/, /^\/viz/, /^\/docs/, /^\/openapi/],
  runtimeCaching: [
    { urlPattern: /\/api\/catalogs\//, handler: 'StaleWhileRevalidate', options: { cacheName: 'dl-catalogs', expiration: { maxAgeSeconds: 7 * 86400 } } },
    { urlPattern: /\/api\/sessions(\/[^/]+\/history)?(\?.*)?$/, handler: 'NetworkFirst', options: { cacheName: 'dl-sessions', networkTimeoutSeconds: 4, expiration: { maxEntries: 60, maxAgeSeconds: 30 * 86400 } } },
    { urlPattern: /^https:\/\/tile\.openstreetmap\.org\//, handler: 'CacheFirst', options: { cacheName: 'dl-tiles', expiration: { maxEntries: 400, maxAgeSeconds: 14 * 86400 }, cacheableResponse: { statuses: [0, 200] } } },
  ],
});
warnings.forEach((w) => console.warn(w));
console.log(`PWA ready: precached ${count} files (${(size / 1024 / 1024).toFixed(1)} MB) → ${DIST}/sw.js`);
