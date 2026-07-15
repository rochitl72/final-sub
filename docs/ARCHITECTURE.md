# DriveLegal — Architecture reference

Technical deep-dive for developers. For installation, see **[SETUP.md](../SETUP.md)**.

---

## Design principles

1. **Graph is source of truth** — Fines come from `data/compiled/drivelegal_graph.json`.
2. **Rules before models** — `dialog_manager`, `violation_resolver`, `location_resolver` run first.
3. **Same protocol online and offline** — Dynamic mode uses trailing `<<SLOTS …>>` lines.
4. **Graceful degradation** — Groq down → `offline_engine`; Sarvam down → text-only.
5. **Additive legal updates** — Gov scraper writes patches; base graph is not mutated at runtime.

---

## Knowledge graph

Single shipped file: **`data/compiled/drivelegal_graph.json`** (~1.2 MB).

Indexed by `graph_engine.py`:

- States, cities, districts
- Violations, fines, road classes, vehicle segments
- Spatial / relocation helpers for pin changes

---

## Backend modules

| Module | Responsibility |
|--------|----------------|
| `api.py` | REST routes, CORS, mounts `apps/web/` |
| `dialog_manager.py` | Session store, turns, MCQ, mode switch |
| `dynamic_chatbot.py` | Narrate phase, protocol parse/strip |
| `offline_engine.py` | Template narrate when cloud LLM unavailable |
| `violation_resolver.py` | Keywords → `violation_code` |
| `location_resolver.py` | Aliases, pin → state/city |
| `clarification_engine.py` | Clarification MCQs, topic router |
| `llm_chatbot.py` | Groq + Sarvam-M |
| `sarvam_service.py` | Bulbul v3 TTS, Mayura translate |
| `auth.py` | Device UUID → JWT |
| `persistence.py` | SQLite sessions/messages |

---

## Session modes

### Calculator (`static`)

Linear slot filling: geo → road → vehicle → violation → fine card. No LLM required. Typical turn &lt; 50 ms.

### Chatbot (`dynamic`)

1. Location bootstrap (GPS / map / browse)
2. Free-text story → violation resolution
3. Clarification MCQs if ambiguous
4. Narrate + fine card
5. Optional driver-context checkboxes

LLM path: `call_groq_narrate` → parse `<<SLOTS>>` → apply slots.  
Fallback: `offline_engine.narrate_protocol` with same slot format.

---

## Connectivity model

| Layer | Meaning |
|-------|---------|
| Phone has no network | Cannot reach API — app shows “No internet” |
| Phone has network, server down | “No server” |
| Server up, Groq down | “Rules (auto)” — `offline_engine` |
| Server up, Groq up, user prefers rules | “Rules only” |
| Server up, Groq up, cloud mode | “Cloud AI” |

`prefer_rules` on `POST .../turn` forces rules path even when Groq is healthy.

---

## Sarvam integration

| Endpoint | Model | Use |
|----------|-------|-----|
| `POST /api/sarvam/translate` | Mayura v1 | UI language |
| `POST /api/sarvam/tts` | Bulbul v3 | Speaker button |

TTS uses language-specific defaults; English may set speaker `shubh`. Indian languages omit legacy v2 speaker names.

---

## Mobile app (`apps/mobile/`)

- **expo-router** — `app/(app)/home`, `chat`
- **Zustand** — `authStore`, `languageStore`, `aiModeStore`
- **`useAiConnectivity`** — NetInfo + `/api/health` → pill + `sarvamAvailable` for TTS
- **Device auth** — UUID in SecureStore → `POST /auth/device`

Cross-platform: same JS on iOS and Android via Expo Go.

---

## API summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | `groq_ok`, `sarvam_ok`, `ready` |
| POST | `/auth/device` | Register device, get JWT |
| POST | `/api/sessions/new` | New session `{mode}` |
| GET | `/api/sessions` | List (scoped by auth) |
| POST | `/api/session/{id}/turn` | Message / chips |
| POST | `/api/sarvam/tts` | Base64 WAV |
| POST | `/api/sarvam/translate` | Translated text |
| GET | `/api/catalogs/*` | States, cities, vehicles |

Full interactive docs: `http://localhost:8000/docs` when server runs.

---

## Persistence

SQLite: `backend/data/chats.db` — sessions, messages, device users.  
In-memory `SessionStore` hydrates from DB on first access after restart.

---

## Tests

```bash
bash scripts/verify.sh
```

Includes smoke tests, device auth, Sarvam TTS speaker regression, persistence selftest, service worker syntax.
