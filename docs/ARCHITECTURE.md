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
| `nlu.py` | Shared understanding layer: guardrail, small talk, grounded FAQ, recall, vehicle what-ifs, multi-offence, LLM output validation |
| `followups.py` | Deterministic follow-ups about the last fine (compoundable, jail, repeat, licence, section, pay, bail, tips) |
| `violation_resolver.py` | Keywords → `violation_code` (typo repair, phrase match, rider/pillion disambiguation) |
| `location_resolver.py` | Aliases, pin → state/city |
| `clarification_engine.py` | Clarification MCQs, topic router |
| `llm_chatbot.py` | Groq + Sarvam-M |
| `sarvam_service.py` | Bulbul v3 TTS, Mayura translate |
| `auth.py` | Device UUID → JWT |
| `persistence.py` | SQLite sessions/messages |

---

## Session modes

### Calculator (`static`)

Slot filling: geo → road → vehicle → violation → fine card. Typed text is understood
too: a story like "riding my bike without a helmet on a city street" fills vehicle,
violation and road in one go, and info questions are answered before the pending chip
question is re-asked. Road type is skipped once the violation is known. No LLM required.

### Chatbot (`dynamic`)

1. Location bootstrap (GPS / map / browse)
2. Free-text story → violation resolution
3. Clarification MCQs if ambiguous
4. Narrate + fine card
5. Optional driver-context checkboxes

LLM path: `call_groq_narrate` → parse `<<SLOTS>>` → apply slots.  
Fallback: `offline_engine.narrate_protocol` with same slot format.

### Turn pipeline (both modes, both engines)

Every free-text turn runs the deterministic layer **before** any LLM call:

1. Safety guardrail (bribery, fake documents, evading cameras/tests) → refusal
2. Small talk / out-of-scope → short reply (no tokens spent)
3. Grounded FAQ (DigiLocker, receipts, contesting, compounding, seizure, towing, Good Samaritan…)
4. Session recall ("what was the fine?", "which city?", "my total so far")
5. What-ifs: another city (re-priced with a diff) or another vehicle (switches to the
   right rule, e.g. car overspeeding → heavy-vehicle overspeeding)
6. Follow-ups about the last fine, or about an earlier one ("is the licence one compoundable?")
7. Several offences in one message → combined answer with total
8. Violation matching → grounded answer from the graph

**Grounding rule:** when a violation is known, the facts (₹ amounts, MV Act section,
compoundable, imprisonment, advice) always come from the graph template. In Cloud mode the
LLM only contributes a one-sentence, fact-free acknowledgement (~150-token prompt). When the
LLM answers open questions, any sentence quoting an amount, section or internal code that
isn't on the fine card is dropped. The vehicle is never taken from the LLM unless the user
mentioned it. This keeps Groq usage to roughly a tenth of the original prompt cost
(important on the 8k tokens/min free tier) and removes hallucinated fines/sections.

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

Includes smoke tests, device auth, Sarvam TTS speaker regression, offline-robustness
units, the **conversation eval** (rules engine, strict), persistence selftest, service
worker / offline bundle syntax and the mobile TypeScript check.

### Conversation eval

`scripts/eval_conversations.py` runs ~55 scripted multi-turn conversations (single
offences, Hinglish, typos, follow-ups, memory, what-ifs, multiple offences, off-topic,
unsafe requests, accidents, info questions, novel questions) in Chatbot and Calculator
mode against the real API, and checks every turn: expected fine card / vehicle / state,
no topic-menu where an answer is expected, guardrail hits and false positives, ₹ amounts
grounded in the graph, no protocol leakage, no LLM calls in rules mode.

```bash
python3 scripts/eval_conversations.py                         # rules engine (free, ~2 s)
python3 scripts/eval_conversations.py --engines rules groq    # + Groq (needs GROQ_CHAT_API_KEY)
```

It writes `eval_out/eval_report.md` with full transcripts. In Groq mode it paces itself and
retries on rate limits so results aren't masked by the offline fallback.

### Keeping the on-device engine in sync

- `scripts/augment_keywords.py` — everyday / Hinglish synonyms merged into the graph
- `scripts/build_offline_bundle.py` — graph slice for the phone / PWA
- `scripts/sync_offline_nlu.py` — FAQ + guardrail generated from `backend/nlu.py`
- `npx esbuild apps/web/offline-entry.ts …` — PWA bundle (see `apps/web/offline-entry.ts`)
