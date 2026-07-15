<div align="center">

# 🚦 DriveLegal

### Offline-first AI assistant for Indian road law, fines & safety

*Ask about any traffic fine, rule, or accident — in plain language, in your state, even with no internet.*

</div>

---

## What it is

DriveLegal turns India's Motor Vehicles Act into a conversation. It's built around a
**cite-able knowledge graph** (127 violations, 702 fines, 36 states, 561 cities with
GPS) so every answer is grounded in real law with the exact MV Act section and
state/city-specific fine — never a hallucinated number.

It runs across a **FastAPI backend**, an **Expo (React Native) mobile app**, and a
**Progressive Web App** — and it works **online *and* fully offline**.

## The three AI modes

| Mode | How it works | Needs internet? |
|------|--------------|-----------------|
| ☁️ **Cloud AI** | Groq (Llama-3.1-8B) narrates over the graph; best language quality | Yes |
| 📋 **Rules (Offline)** | Deterministic resolver + graph fine-cascade — instant, exact, 100% grounded | No |
| 🔌 **On-device AI** | A real LLM (Llama-3.2-1B via **WebLLM / WebGPU**) running *in the browser*, offline, with on-device RAG over the graph | No (after first model download) |

**Across every mode, the fine amount always comes from the graph** — the language
model only phrases the answer, so it can never misquote a penalty.

## Key features

- **Grounded answers** — exact fine, MV Act section, compoundable status, tips, and
  "what to do if stopped", per state and city.
- **Location aware** — GPS, map pin (Leaflet), or state→city picker; fines adjust to
  your state/city (e.g. no-helmet is ₹500 in Karnataka, ₹1,000 in Tamil Nadu).
- **Truly offline** — the knowledge graph, resolver, and narration are ported to the
  client, so the app answers with zero network. The on-device model even runs an LLM
  offline via WebGPU.
- **Conversational** — multi-turn memory, follow-ups ("is it compoundable?"), a safety
  guardrail that refuses bribery/forgery requests, out-of-scope handling, and coherence
  checks (helmet vs seat belt).
- **Voice & multilingual** — Sarvam AI text-to-speech and translation (11+ Indian
  languages), plus offline browser TTS.
- **Self-updating law data** — a gov.in scraper + additive patch pipeline keeps fines
  current without ever mutating the base graph.
- **Accident mode** — one-tap MV Act §134 procedure with emergency numbers.

## Architecture

```
                ┌──────────────────────────────────────────────┐
                │            Knowledge Graph (1.2 MB)            │
                │ violations · fines · states · cities · spatial │
                └───────────────┬───────────────┬───────────────┘
                                │               │
              ┌─────────────────┘               └──────────────────┐
              ▼                                                     ▼
   ┌──────────────────────┐                         ┌──────────────────────────┐
   │   FastAPI backend     │                         │  Client (ported to TS/JS) │
   │  graph engine         │                         │  resolver + fine cascade  │
   │  violation resolver   │                         │  narration engine         │
   │  dynamic chatbot (LLM)│                         │  (mobile app + PWA)       │
   │  gov scraper + patches│                         └──────────────────────────┘
   └──────────┬────────────┘                                      │
              │ Groq / Sarvam                                     │ WebLLM (WebGPU)
              ▼                                                    ▼
        Cloud AI mode                                    On-device AI mode (offline)
```

**Grounding principle:** the model narrates, the graph adjudicates. Every ₹ amount is
looked up in the graph regardless of which mode answered.

## Tech stack

- **Backend:** Python, FastAPI, SQLite, an in-memory graph engine, Groq + Sarvam APIs
- **Mobile:** Expo / React Native, expo-router, Zustand, react-native-maps
- **Web / On-device:** Vanilla JS PWA, service worker, **WebLLM** (WebGPU), Leaflet
- **Data:** compiled JSON knowledge graph + additive patch store

## Repository structure

```
backend/                 FastAPI app, graph engine, resolver, chatbot, offline engine
  api.py                 REST endpoints
  graph_engine.py        loads + queries the knowledge graph
  violation_resolver.py  free-text → violation matching
  dynamic_chatbot.py     LLM narrate flow (Cloud AI)
  offline_engine.py      rule-based fallback (no LLM)
  gov_scraper.py + patch_engine.py + update_orchestrator.py   law-update pipeline
apps/mobile/             Expo React Native app (Calculator · AI Chat · On-device)
  src/offline/           TypeScript port of the resolver + graph + on-device SLM seam
apps/web/                PWA
  slm.html               On-device AI mode (WebLLM, offline, graph-grounded)
  offline.bundle.js      bundled offline engine
  drivelegal_cities.json GPS/map/city dataset
  webllm.bundle.js       self-hosted WebLLM loader (offline-safe)
  sw.js                  service worker (precaches app + engine)
data/compiled/           drivelegal_graph.json — the knowledge graph
scripts/                 build_offline_bundle.py, build_cities.py, setup helpers
docs/                    architecture notes, offline-AI guide
```

## Getting started

### 1. Backend

```bash
cd backend
python3 -m pip install fastapi "uvicorn[standard]" "python-jose[cryptography]" httpx pydantic beautifulsoup4
cp ../.env.example ../.env      # then add your keys (see below)
python3 api.py                 # serves on http://0.0.0.0:8000
```

### 2. Mobile app (Expo Go)

```bash
cd apps/mobile
npm install
# point the app at your machine's LAN IP so a physical phone can reach the backend
EXPO_PUBLIC_API_BASE_URL="http://<YOUR_LAN_IP>:8000" npx expo start
```
Scan the QR with **Expo Go**. Use the home-screen toggle: **Calculator · AI Chat ·
On-device**.

### 3. PWA / On-device AI

Serve `apps/web/` over **HTTPS** (WebGPU requires a secure context) — e.g. drag the
folder onto [Netlify Drop](https://app.netlify.com/drop). Open `…/slm.html`. First load
downloads a small model once, then it runs fully offline.

Regenerate the offline datasets after changing the graph:
```bash
python3 scripts/build_offline_bundle.py   # → apps/mobile/src/offline/drivelegal_offline.json
python3 scripts/build_cities.py           # → apps/web/drivelegal_cities.json
```

## Environment variables

Copy `.env.example` → `.env` (git-ignored) and set:

| Variable | Purpose | Required |
|----------|---------|----------|
| `GROQ_CHAT_API_KEY` | Cloud AI chat (Groq free tier) | for Cloud mode |
| `SARVAM_API_KEY` | Text-to-speech + translation | for voice/translate |
| `SESSION_SECRET` | JWT signing (set a random string) | for production |

> The app runs without any keys — it just falls back to the offline rule engine and
> disables voice/translate. **Never commit `.env`.**

## How offline works

The knowledge graph and resolver are pure functions, so they were ported to TypeScript
and bundled for the client (`apps/mobile/src/offline/`, `apps/web/offline.bundle.js`).
The service worker precaches the app shell, the offline engine, and the WebLLM loader;
WebLLM caches the model weights in the browser. Result: after one online visit, the
whole experience — including a running LLM — works with the network off.

## License & credits

Built for a road-safety hackathon by the Rehabilitation Bioengineering Group (RBG).
Traffic-law data compiled from the Motor Vehicles Act 1988 (amended 2019) and official
gov.in sources.
