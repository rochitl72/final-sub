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

It is one **installable Progressive Web App** (React + Vite + Tailwind) served by a
**FastAPI backend** — phone layout on phones, two-pane layout on desktop — and it works
**online *and* offline**. See [docs/PWA.md](docs/PWA.md).

## The two modes

| Mode | How it works | Needs the server? |
|------|--------------|-------------------|
| ✨ **AI Chat** | Describe what happened in your own words. The rule engine answers instantly (person-by-person breakdown from the Scenario Engine); the org's self-hosted LLM (Ollama, llama3.1:8b) then refines that same answer in the background. Groq is the fallback provider. | For the LLM — falls back to the on-device rule engine |
| 🧮 **Calculator** | Chip-driven: location → road → vehicle → violation. Deterministic resolver + graph fine cascade — instant, exact, 100% grounded | No (on-device engine) |

**Across every mode, the fine amount always comes from the graph** — the language
model only phrases the answer, so it can never misquote a penalty.

## Key features

- **Grounded answers** — exact fine, MV Act section, compoundable status, tips, and
  "what to do if stopped", per state and city.
- **Location aware** — GPS, map pin (Leaflet), or state→city picker; fines adjust to
  your state/city (e.g. no-helmet is ₹500 in Karnataka, ₹1,000 in Tamil Nadu).
- **Truly offline** — the knowledge graph, resolver, and narration are ported to the
  client, so the rule-based engine answers with zero network.
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
   │  dynamic chatbot (LLM)│                         │  (PWA, apps/pwa)          │
   │  gov scraper + patches│                         └──────────────────────────┘
   └──────────┬────────────┘                                      │
              │ Ollama (org) / Groq / Sarvam                      │
              ▼                                                    ▼
        AI Chat mode                                     Rule-based offline fallback
```

**Grounding principle:** the model narrates, the graph adjudicates. Every ₹ amount is
looked up in the graph regardless of which mode answered.

## Tech stack

- **Backend:** Python, FastAPI, SQLite, an in-memory graph engine, Scenario Engine; Ollama (org LLM) / Groq + Sarvam
- **App (PWA):** React 19, Vite 6, Tailwind CSS 3, Zustand, React Router, Leaflet, Workbox (vite-plugin-pwa)
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
apps/pwa/                the DriveLegal PWA — React + Vite + Tailwind (npm run build → dist/, served at /)
  src/screens/           HomeScreen (chat list / sidebar), ChatScreen (both modes)
  src/components/        fine card, scenario cards, MCQ, location picker, language picker, …
  src/offline/           TypeScript port of the resolver + graph (on-device rule engine)
data/compiled/           drivelegal_graph.json — the knowledge graph
scripts/                 build_offline_bundle.py, eval + verify helpers
docs/                    architecture notes, scenario engine, eval guide
```

## Getting started

One command (needs Python 3.11+ and Node 18+):

```bash
cp .env.example .env     # then add your keys (see below)
bash run.sh              # installs deps, builds the PWA if needed, serves http://localhost:8000
```

Open `http://localhost:8000` on the laptop, or `http://<your-LAN-IP>:8000` on a phone on the same
Wi-Fi, and use the browser's **Install app / Add to Home Screen**.

UI development with hot reload (backend still running on :8000):

```bash
cd apps/pwa && npm install && npm run dev     # http://localhost:5173, /api proxied to :8000
```

Regenerate the on-device data after changing the graph:
```bash
python3 scripts/build_offline_bundle.py   # → apps/pwa/src/offline/drivelegal_offline.json
python3 scripts/sync_offline_nlu.py       # → apps/pwa/src/offline/faq.generated.ts
```

## Environment variables

Copy `.env.example` → `.env` (git-ignored) and set:

| Variable | Purpose | Required |
|----------|---------|----------|
| `LLM_PROVIDER`, `OLLAMA_URL`, `OLLAMA_MODEL` | The org's self-hosted LLM for AI Chat (see [docs/ORG_LLM.md](docs/ORG_LLM.md)) | for AI Chat |
| `GROQ_CHAT_API_KEY` | Fallback LLM provider (Groq free tier) | optional |
| `SARVAM_API_KEY` | Text-to-speech + translation | for voice/translate |
| `SESSION_SECRET` | JWT signing (set a random string) | for production |

> The app runs without any keys — it just falls back to the offline rule engine and
> disables voice/translate. **Never commit `.env`.**

## How offline works

The knowledge graph and resolver are pure functions, so they were ported to TypeScript
and bundled into the PWA (`apps/pwa/src/offline/`). The Workbox service worker precaches the
app shell and the engine. After one online visit, when the server can't be reached the app
answers on-device with the same fine cards; the LLM refinement needs the server.

## Tested conversation quality

Every combination of **Chatbot / Calculator × Rules / Groq** is evaluated end-to-end with
135 scripted conversations (follow-ups, memory, what-ifs, multiple offences, Hinglish,
typos, off-topic, unsafe requests, accidents, novel questions) — all pass, with every ₹
amount and MV Act section grounded in the knowledge graph. See
[docs/CONVERSATION_EVAL.md](docs/CONVERSATION_EVAL.md) and run
`python3 scripts/eval_conversations.py`.

## License & credits

Built for a road-safety hackathon by the Rehabilitation Bioengineering Group (RBG).
Traffic-law data compiled from the Motor Vehicles Act 1988 (amended 2019) and official
gov.in sources.
