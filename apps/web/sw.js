/**
 * sw.js — DriveLegal Service Worker (v3)
 *
 * Strategy:
 *   - Map tiles (OSM)       → cache-first, store on miss (zoom 4..14).
 *   - /api/catalogs/*       → network-first, cache fallback (write-through).
 *   - /api/health           → network-first, cache fallback.
 *   - /api/sessions/*       → network-first, cache fallback (sidebar still
 *                             renders offline; mutations stay live).
 *   - /api/session/*        → network-only (stateful per request).
 *   - App shell + logo      → cache-first.
 */

const TILE_CACHE  = "drivelegal-tiles-v1";
const SHELL_CACHE = "drivelegal-shell-v11";  // bumped: offline guardrail + follow-ups, deduped cities
const API_CACHE   = "drivelegal-api-v4";  // bumped: deduped city catalog
const TILE_HOSTS  = [
  "tile.openstreetmap.org",
  "a.tile.openstreetmap.org",
  "b.tile.openstreetmap.org",
  "c.tile.openstreetmap.org",
];

const SHELL_URLS = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/favicon.svg",
  "/logo.png",
  "/sw.js",
  "/offline.bundle.js",   // on-device engine — required for airplane-mode chat
  "/slm.html",            // on-device AI (WebLLM) mode page
  "/drivelegal_cities.json", // offline GPS / map / city lookup for the SLM page
  "/webllm.bundle.js",    // self-hosted WebLLM loader — makes the model work offline after reload
];

const CATALOG_URLS = [
  "/api/catalogs/states",
  "/api/catalogs/vehicles",
  "/api/catalogs/violation_categories",
];

// ── Install: pre-cache shell + catalogs ───────────────────────────────────────
self.addEventListener("install", evt => {
  evt.waitUntil((async () => {
    const shell = await caches.open(SHELL_CACHE);
    await Promise.all(SHELL_URLS.map(u => shell.add(u).catch(() => null)));
    const api = await caches.open(API_CACHE);
    await Promise.all(CATALOG_URLS.map(u => api.add(u).catch(() => null)));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", evt => {
  evt.waitUntil((async () => {
    const keys = await caches.keys();
    const allowed = new Set([TILE_CACHE, SHELL_CACHE, API_CACHE]);
    await Promise.all(keys.filter(k => !allowed.has(k)).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

// ── Fetch routing ─────────────────────────────────────────────────────────────
self.addEventListener("fetch", evt => {
  const req = evt.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (TILE_HOSTS.some(h => url.hostname.includes(h))) {
    evt.respondWith(tileStrategy(req));
    return;
  }

  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith("/api/catalogs/")) {
      evt.respondWith(networkFirstCacheable(req, API_CACHE));
      return;
    }
    if (url.pathname === "/api/health") {
      evt.respondWith(networkFirstCacheable(req, API_CACHE));
      return;
    }
    // /api/sessions  (list)              → network-first with cache fallback
    // /api/sessions/{id}/history (read) → network-first with cache fallback
    if (
      url.pathname === "/api/sessions" ||
      (url.pathname.startsWith("/api/sessions/") && url.pathname.endsWith("/history"))
    ) {
      evt.respondWith(networkFirstCacheable(req, API_CACHE));
      return;
    }
    // Stateful single-session endpoints — never intercept
    if (url.pathname.startsWith("/api/session/") || url.pathname.startsWith("/api/sessions/")) {
      return;
    }
    // App shell → cache-first
    evt.respondWith(
      caches.match(req).then(r => r || fetch(req).then(resp => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(SHELL_CACHE).then(c => c.put(req, copy)).catch(() => {});
        }
        return resp;
      }))
    );
  }
});

// ── Strategies ────────────────────────────────────────────────────────────────
async function tileStrategy(request) {
  const cache  = await caches.open(TILE_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const url   = new URL(request.url);
      const parts = url.pathname.split("/").filter(Boolean);
      const zoom  = parseInt(parts[0], 10);
      if (zoom >= 4 && zoom <= 14) cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response(PLACEHOLDER_TILE, {
      headers: { "Content-Type": "image/png", "Cache-Control": "no-store" },
    });
  }
}

async function networkFirstCacheable(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response && response.ok) cache.put(request, response.clone()).catch(() => {});
    return response;
  } catch {
    const cached = await cache.match(request);
    return cached || new Response(JSON.stringify({ error: "offline" }), {
      headers: { "Content-Type": "application/json" }, status: 503,
    });
  }
}

const PLACEHOLDER_TILE = Uint8Array.from(atob(
  "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEAAQMAAABmvDolAAAAA1BMVEXo6Oir2sa5AAAAHklEQVR42u3BMQEAAADCoPVP7WsIoAAAAAAAAAAAeAMBcAABHgAAAABJRU5ErkJggg=="
), c => c.charCodeAt(0)).buffer;
