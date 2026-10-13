# DriveLegal — Setup

DriveLegal is one **Progressive Web App** (`apps/pwa`, React + Vite + Tailwind) served by a
**FastAPI backend** (`backend/`). There is nothing to install on the phone: open the site and
"Add to Home Screen".

## 1. Prerequisites

| Tool | Why |
|------|-----|
| **Python 3.11+** | backend |
| **Node.js 18+** and npm | builds the PWA (once, ~30 s) |
| A browser | Chrome / Edge / Safari — on the laptop and/or a phone on the same Wi-Fi |

## 2. Repository layout

```
final-sub/
├── run.sh                 # one command: deps → build PWA if needed → start the server
├── .env.example           # copy to .env (git-ignored) and fill in
├── backend/               # FastAPI app (api.py), graph engine, Scenario Engine, LLM + Sarvam services
├── apps/pwa/              # the DriveLegal PWA (React + Vite + Tailwind) → dist/ served at "/"
├── data/                  # knowledge graph + SQLite
├── scripts/               # offline bundle builders, eval suites, verify.sh
└── docs/                  # architecture, PWA, org LLM, scenario engine, eval notes
```

## 3. Configure keys

```bash
cp .env.example .env
```

Edit `.env` (it is git-ignored — keys never go in the repo or in chat):

| Variable | What it does |
|----------|--------------|
| `LLM_PROVIDER=ollama`, `OLLAMA_URL`, `OLLAMA_MODEL` | AI Chat uses the org's self-hosted LLM (org network only). See [docs/ORG_LLM.md](docs/ORG_LLM.md). |
| `GROQ_CHAT_API_KEY` | Optional fallback LLM provider. |
| `SARVAM_API_KEY` | Translation (11 Indian languages) + text-to-speech. |
| `SESSION_SECRET` | JWT signing secret — set a long random string for any shared deployment. |

With no keys at all the app still works: both modes fall back to the rule engine, and
translation / Sarvam voice are disabled (the browser's own voice is used for read-aloud).

## 4. Run it

**macOS / Linux**

```bash
bash run.sh
```

**Windows** — use Git Bash for `bash run.sh`, or run the steps by hand in PowerShell:

```powershell
python -m pip install -r backend\requirements.txt
cd apps\pwa; npm install; npm run build; cd ..\..
cd backend; python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000**.

## 5. Use it on a phone

1. Phone and laptop on the same Wi-Fi.
2. Find the laptop's LAN IP (`ipconfig getifaddr en0` on macOS, `ipconfig` on Windows).
3. Open `http://<LAN-IP>:8000` on the phone.
4. Browser menu → **Add to Home Screen** / **Install app**.

> Browsers only allow GPS and full offline install on `https://` or `localhost`. Over plain
> `http://<LAN-IP>` the app works, but use **Map** or **Browse** to set the location, and the
> service worker won't install. For a phone demo, serve it over HTTPS — see [docs/PWA.md](docs/PWA.md).

## 6. Developing the UI

```bash
# terminal 1 — backend
bash run.sh
# terminal 2 — hot-reloading PWA dev server (proxies /api and /auth to :8000)
cd apps/pwa && npm run dev          # → http://localhost:5173
```

`npm run build` type-checks and writes `apps/pwa/dist/`; `run.sh` rebuilds automatically
whenever the PWA source is newer than the build.

After changing the knowledge graph or the NLU FAQ, regenerate the on-device engine data:

```bash
python3 scripts/build_offline_bundle.py
python3 scripts/sync_offline_nlu.py
```

## 7. Checks

```bash
bash run.sh test          # pytest + scripted conversation eval
bash scripts/verify.sh    # full gate, including the PWA type check + build
```

## 8. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Page shows `{"status": "DriveLegal API running"}` | The PWA isn't built — `cd apps/pwa && npm install && npm run build`, or check `/tmp/drivelegal-pwa-build.log`. |
| `npm install` skips Vite / Tailwind | Your shell has `NODE_ENV=production`; run `NODE_ENV=development npm install --include=dev`. |
| Engine pill says **Rules (auto)** | The LLM isn't reachable (off the org network?). Answers still come from the rule engine. |
| Engine pill says **Offline** | The device can't reach the server — answers come from the on-device engine. |
| Old UI after an update | The service worker updates itself on the next load; reload once more. |
| Translation / Sarvam voice missing | Add `SARVAM_API_KEY` to `.env` and restart. |
