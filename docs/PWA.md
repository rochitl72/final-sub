# DriveLegal PWA

The PWA in `apps/pwa` (React 19 + Vite 6 + Tailwind CSS 3) is the **only client**. It runs in any
browser, installs to a phone home screen or a laptop dock, and keeps working with a weak or no
connection. The FastAPI backend serves the built app (`apps/pwa/dist`) at `/`.

* **Phone / narrow window:** chat list → chat, one screen at a time.
* **Desktop (≥ 1024 px):** chat list as a sidebar, the open chat (or a welcome pane) on the right.

## Two modes

| Mode | What the user does | Engine |
|---|---|---|
| **Calculator** | Taps through location → road → vehicle → violation | Deterministic rule engine; exact fine card |
| **AI Chat** | Describes what happened in their own words | Rule engine answers instantly (Scenario Engine: person-by-person breakdown); the org LLM refines the same answer in the background (`/api/session/{id}/updates`) |

The pill in the AI Chat header shows the engine: **AI** (LLM up), **Rules only** (user's choice — tap
to toggle), **Rules (auto)** (LLM down), **Offline** / **No internet** (answered on the device).

## What works offline

| Piece | Offline behaviour |
|---|---|
| App shell, icons | Precached by Workbox — opens instantly, even in airplane mode |
| Rule engine | Bundled in the app (`src/offline`) — answers with exact fines when the server can't be reached |
| State / city / vehicle lists | Stale-while-revalidate cache + localStorage |
| Past chats | Network-first, cached copy when offline |
| Map tiles | Cached as you browse (14 days) |
| LLM refinement, multi-person Scenario Engine, translation, Sarvam voice | Need the backend — the app falls back to the rule engine / browser voice |

## Install

* Chrome / Edge / Android: install icon in the address bar, or menu → *Install DriveLegal*.
* iPhone / iPad / Mac Safari: Share → *Add to Home Screen* / *Add to Dock*.

## Phones need HTTPS

Service workers, install and GPS need **HTTPS** (or `localhost`). `http://<laptop-ip>:8000` works on a
phone as a normal page (set the location with Map or Browse) but is not installable. Options:

1. Demo: a tunnel such as `cloudflared tunnel --url http://localhost:8000` → open the https URL on the phone.
2. Deploy the FastAPI app (it serves the site) behind HTTPS on any host.
3. Split hosting: host `apps/pwa/dist` on a static host and reverse-proxy `/api` and `/auth` to the backend (the app is always same-origin).

## Updates

`index.html`, `sw.js` and `registerSW.js` are served `no-cache`. The service worker auto-updates
(`skipWaiting` + `clientsClaim`); a new build is picked up on the next load.

## Develop

```bash
cd apps/pwa
npm install                # NODE_ENV=development npm install --include=dev if your shell sets NODE_ENV=production
npm run dev                # http://localhost:5173 — /api and /auth proxied to http://127.0.0.1:8000
npm run build              # tsc type check + vite build → dist/ (+ sw.js)
```

| Where | What |
|---|---|
| `src/App.tsx` | phone vs desktop layout, routes `/` and `/chat?sessionId=…&mode=…` |
| `src/screens/HomeScreen.tsx` | chat list, mode switch, new chat, law-update banner |
| `src/screens/ChatScreen.tsx` | the conversation: turns, refinement polling, offline fallback, translation, location |
| `src/components/` | FineCard, ScenarioCards, MultiSelect, LocationModal (GPS / Leaflet map / browse), LanguagePicker, TtsButton, … |
| `src/lib/api.ts` | typed API client (device JWT, retry, catalog cache) |
| `src/offline/` | on-device rule engine (generated data: `drivelegal_offline.json`, `faq.generated.ts`) |
| `tailwind.config.js` | design tokens (midnight black · deep navy · blue) |

## Roadmap

* Port the Scenario Engine to the on-device bundle so multi-person stories work offline too.
* Voice input.
* Push notifications for law updates (Android + desktop; limited on iOS).
