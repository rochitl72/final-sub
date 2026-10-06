# DriveLegal as a PWA (main version)

The PWA is the **web build of the mobile app** (`apps/mobile`, Expo / React Native Web): the exact same screens and
components as the phone app. On wide windows (≥1024px) the chat list sits on the left and the open chat on the right;
below that it is the phone layout. It is the primary product. It runs in any
browser, installs to a laptop dock / phone home screen, and keeps working with a weak or no connection.
The same code still builds the native iOS / Android app through Expo Go — optional.

## What works offline
| Piece | Offline behaviour |
|---|---|
| App shell, fonts, icons | Precached by the service worker — opens instantly, even in airplane mode |
| Rule-based engine | Built into the app (`src/offline`) — answers typed questions with exact fines when there is no connection |
| State / city / vehicle lists | Stale-while-revalidate cache |
| Past chats | Network-first, cached copy shown when offline |
| Map tiles | Cached as you browse (14 days) |
| AI Chat (Groq), multi-person Scenario Engine, voice | **Need internet / the backend** — the app says so and falls back to the rule engine |

## Install
* Chrome / Edge / Android: the install icon in the address bar (or menu → Install DriveLegal).
* iPhone / iPad / Mac Safari: Share → *Add to Home Screen* / *Add to Dock*.

## Phones need HTTPS
Browsers only allow service workers and installing on **HTTPS** (or `localhost`). `http://<laptop-ip>:8000` works as a
normal page on a phone but is **not installable**. Quick ways to get HTTPS:
1. Demo today: `cloudflared tunnel --url http://localhost:8000` (or `ngrok http 8000`) → open the https URL on the phone.
2. Permanent: deploy the FastAPI app (it serves the site) to Render / Railway / Fly.io; set `GROQ_CHAT_API_KEY` as an env var there.
3. Split hosting: build with `EXPO_PUBLIC_API_BASE_URL=https://your-api npm run build:web` and host `dist/` on Netlify / Vercel; allow that origin in the backend CORS list.

## Updates
`sw.js` and `index.html` are served `no-cache`; a new deploy is picked up on the next visit and open tabs reload once.

## Develop
```bash
cd apps/mobile && npm install
npx expo start --web     # live-reload dev server (http://localhost:8081) — set EXPO_PUBLIC_API_BASE_URL=http://localhost:8000
npm run build:web        # expo export -p web + manifest/icons/service worker → dist/ (served by the backend at "/")
```
Desktop layout: `app/(app)/_layout.tsx` (two panes when `useDesktop()`); web-only map: `src/components/IndiaMap.web.tsx` (Leaflet).

## Roadmap
* Port the Scenario Engine to the offline bundle so multi-person stories work offline too.
* Voice (Sarvam TTS/STT) and translation in the PWA.
* Web Share Target / push notifications for law updates (Android + desktop; limited on iOS).
