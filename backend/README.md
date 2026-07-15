# DriveLegal backend (`server/`)

FastAPI service: slot-filling dialog, graph lookups, optional Groq + Sarvam.

## Layout

| Path | Role |
|------|------|
| `api.py` | HTTP routes, health, static web mount |
| `graph_engine.py` | Loads `../data/compiled/drivelegal_graph.json` |
| `dialog_manager.py` | Session state machine (calculator + chatbot) |
| `dynamic_chatbot.py` | Chatbot narrate phase + `<<SLOTS>>` protocol |
| `offline_engine.py` | Rule-based narrate when Groq is down |
| `violation_resolver.py` | Keyword → violation code |
| `location_resolver.py` | State/city/pin resolution |
| `clarification_engine.py` | MCQs, topic router |
| `catalogs.py` | Chip catalogues for UI |
| `llm_chatbot.py` | Groq / Sarvam-M calls |
| `sarvam_service.py` | TTS, translate (Bulbul v3 / Mayura) |
| `auth.py` | Device-ID JWT |
| `persistence.py` | SQLite sessions |
| `patch_engine.py` / `gov_scraper.py` / `update_orchestrator.py` | Gov data patches |
| `data/` | Runtime SQLite (`chats.db`) |
| `tests/` | Smoke + auth + Sarvam TTS tests |

## Run

```bash
cd drivelegal/backend
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Or from repo root: `bash start.sh` (backend + Expo).
