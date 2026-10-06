#!/usr/bin/env python3
"""
api.py — DriveLegal FastAPI backend
====================================
Endpoint contract (v3.1 — sidebar + dynamic mode + smart relocation):

  POST   /api/session/start                          (legacy alias — static mode)
  POST   /api/sessions/new           body {mode}     (new — sidebar entry point)
  GET    /api/sessions                               (sidebar list)
  GET    /api/sessions/{id}/history                  (replay)
  DELETE /api/sessions/{id}                          (sidebar delete)
  PUT    /api/session/{id}/mode      body {mode}     (toggle calculator/chatbot)
  PUT    /api/session/{id}/geo_mode  body {mode}
  POST   /api/session/{id}/geo/pin   body {lat, lng}
  POST   /api/session/{id}/geo/manual
  POST   /api/session/{id}/slot
  POST   /api/session/{id}/turn
  POST   /api/session/{id}/revert_location
  GET    /api/catalogs/{states,cities,vehicles,violation_categories}
  GET    /api/health
  DELETE /api/session/{id}

Persistence: every successful slot-mutating / turn endpoint writes a row
into the SQLite store (`backend/data/chats.db`). The in-memory
`SessionStore` lazily hydrates from SQLite on first hit after restart.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import threading
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Load the repo-root .env so uvicorn always sees API keys (even without start.sh)
_ENV_FILE = _HERE.parent / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k, _v = _k.strip(), _v.strip().strip("'\"")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from catalogs        import get_catalogs
from dialog_manager  import (
    get_dialog_manager,
    get_session_store,
    session_state,
)
from dynamic_chatbot import (
    extract_and_reply as dynamic_extract_and_reply,
    handle as dynamic_handle,
)
from graph_engine    import get_graph_engine
from llm_chatbot     import handle_freeform, warmup, check_groq_status, groq_model_name
from persistence     import derive_title, get_chat_store
from auth            import (
    router as auth_router,
    get_current_user,
    link_session_to_user,
    get_user_sessions,
    get_anonymous_sessions,
)
from patch_engine        import get_update_status, get_all_patches
from update_orchestrator import start_update_background, is_running, last_summary
from sarvam_service      import (
    synthesize_speech, translate_text,
    SarvamOfflineError, check_sarvam_status,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger("drivelegal.api")

# ── Cloud status cache ────────────────────────────────────────────────────────
# check_groq_status() and check_sarvam_status() make real HTTP calls (1-5s).
# Calling them inside /api/health blocks FastAPI's async loop and causes the
# mobile's 5s timeout to expire → "No server" even when backend is running.
#
# Fix: run checks in a background thread every 60s and cache the result.
# /api/health reads the cache in <1ms — always responds instantly.

_cloud_cache: dict = {
    "groq_ok":    False,
    "sarvam_ok":  False,
    "checked_at": 0.0,
}
_CLOUD_CHECK_INTERVAL = 60   # seconds between background checks


def _cloud_status_checker() -> None:
    """Background daemon thread: refreshes Groq + Sarvam status every 60s."""
    # Small initial delay so the backend is fully up before the first check
    time.sleep(3)
    while True:
        try:
            _cloud_cache["groq_ok"]   = check_groq_status()
            _cloud_cache["sarvam_ok"] = check_sarvam_status()
            _cloud_cache["checked_at"] = time.time()
            log.debug(
                "Cloud status refresh: groq=%s sarvam=%s",
                _cloud_cache["groq_ok"], _cloud_cache["sarvam_ok"],
            )
        except Exception as exc:
            log.warning("Cloud status check error: %s", exc)
        time.sleep(_CLOUD_CHECK_INTERVAL)

app = FastAPI(
    title="DriveLegal — Slot-Filling Road Law Assistant",
    version="3.1.0",
    description="Offline Indian traffic-law chatbot — slot calculator + conversational chatbot.",
)

# D4: CORS origins are configurable. Defaults to "*" for local dev, but a
# deployment can lock it down with DRIVELEGAL_CORS_ORIGINS="https://app.example".
_cors_env = os.getenv("DRIVELEGAL_CORS_ORIGINS", "*").strip()
_cors_origins = (
    ["*"] if _cors_env == "*"
    else [o.strip() for o in _cors_env.split(",") if o.strip()]
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount auth routes
app.include_router(auth_router)

_LEGACY_WEB = _HERE.parent / "apps" / "web"
_DESKTOP = _HERE.parent / "apps" / "mobile" / "dist"   # Expo web export of the mobile app (the PWA)
# The mobile app's web export (apps/mobile: `npx expo export -p web`) is the main site when built; the original
# single-file PWA stays available at /classic.
_FRONTEND = _DESKTOP if (_DESKTOP / "index.html").exists() else _LEGACY_WEB
_VIZ_HTML = _HERE.parent / "docs" / "visualizations" / "drivelegal_graph_3d.html"

DEFAULT_STATE_CODE = os.getenv("DRIVELEGAL_DEFAULT_STATE", "KA")


# ── Pydantic request models ───────────────────────────────────────────────────


class GeoModeBody(BaseModel):
    mode: str = Field(..., pattern="^(map|chat)$")


class ModeBody(BaseModel):
    mode: str = Field(..., pattern="^(static|dynamic)$")


class NewSessionBody(BaseModel):
    mode: str = Field("static", pattern="^(static|dynamic)$")


from fastapi import Depends


class PinBody(BaseModel):
    lat: float = Field(..., ge=6.5,  le=35.7)
    lng: float = Field(..., ge=68.0, le=97.5)


class ManualLocationBody(BaseModel):
    state_code: Optional[str] = None
    city_code:  Optional[str] = None
    text:       Optional[str] = None


class SlotBody(BaseModel):
    slot:  str
    value: str


class TurnBody(BaseModel):
    message:  Optional[str] = Field(default=None, max_length=2000)
    chip_id:  Optional[str] = None
    # Multi-select clarification: ids of every checkbox the user ticked.
    chip_ids: Optional[List[str]] = None
    # Free text when the user picks "Something else" on an MCQ.
    other_text: Optional[str] = Field(default=None, max_length=2000)
    # Mobile: skip Groq and use offline_engine for narrate phase.
    prefer_rules: bool = False


# ── Startup: warm caches + Ollama ─────────────────────────────────────────────


@app.on_event("startup")
async def _startup() -> None:
    get_graph_engine()
    get_catalogs()
    get_session_store()
    get_chat_store()
    # Wire both callbacks once. In dynamic mode the dialog manager only
    # invokes `dynamic_fn` after location bootstrap completes — at that
    # point the LLM-driven narrate-phase extractor owns every turn.
    get_dialog_manager(
        freeform_fn = lambda s, t: handle_freeform(s, t),
        dynamic_fn  = lambda s, t: dynamic_extract_and_reply(s, t),
    )
    threading.Thread(target=warmup, name="groq-warmup", daemon=True).start()
    # Background cloud-status checker — keeps health endpoint instant (<1ms)
    threading.Thread(target=_cloud_status_checker, name="cloud-status", daemon=True).start()
    log.info("DriveLegal backend ready (cloud LLM when configured; offline rules engine always on)")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _lazy_load_session(session_id: str) -> Optional[dict]:
    """If `session_id` lives only in SQLite (server was restarted), build a
    minimal in-memory session from the persisted summary so the dialog
    manager can resume."""
    store = get_chat_store()
    row = store.get_session(session_id)
    if not row:
        return None
    summary = row.get("summary") or {}
    s = get_session_store().create_with_id(session_id, mode=row.get("mode") or "static")
    # Hydrate the fields the dialog manager relies on.
    for k in (
        "geo_mode", "state_code", "city_code", "city_name",
        "road_bucket", "vehicle_segment", "vehicle_fine_class",
        "vehicle_type", "violation_category", "violation_code",
        "stage", "last_fine_card", "narrate_started",
    ):
        v = summary.get(k)
        if v is not None:
            s[k] = v
    s["mode"] = row.get("mode") or "static"
    # Restore the last few real messages so the LLM has some context.
    # F4: drop side-effect breadcrumbs before slicing so they never reach the LLM.
    msgs = [m for m in store.get_messages(session_id) if not _is_breadcrumb(m)]
    s["messages"] = [
        {"role": m["role"], "content": m["content"]}
        for m in msgs[-12:]
    ]
    return s


def _is_breadcrumb(m: dict) -> bool:
    """F4: side-effect breadcrumbs (pin drops, chip clicks) are stored with
    role='user' but are NOT real user turns. Feeding them to the LLM as chat
    history pollutes context, so we filter them out when rehydrating."""
    if m.get("role") != "user":
        return False
    c = (m.get("content") or "").lstrip()
    return c.startswith("📍") or c.startswith("chip:")


def _get_session_or_404(sid: str) -> dict:
    s = get_session_store().get(sid)
    if s:
        return s
    revived = _lazy_load_session(sid)
    if revived:
        return revived
    raise HTTPException(status_code=404, detail=f"Unknown session_id: {sid}")


def _remember_cards(s: dict, payload: dict) -> None:
    """Per-conversation history of fines discussed (one entry per offence,
    latest location wins) — powers "recap" / "total" answers."""
    cards = payload.get("fine_cards") or ([payload["fine_card"]] if payload.get("fine_card") else [])
    if not cards:
        return
    hist = [c for c in (s.get("card_history") or []) if isinstance(c, dict)]
    for c in cards:
        hist = [h for h in hist if h.get("violation_code") != c.get("violation_code")]
        hist.append(c)
    s["card_history"] = hist[-10:]


def _envelope(s: dict, payload: dict) -> dict:
    _remember_cards(s, payload)
    out = dict(payload)
    out.setdefault("session_state", session_state(s))
    out["session_id"] = s["session_id"]
    return out


def _persist_turn_in(sid: str, *, message: Optional[str], chip_id: Optional[str]) -> None:
    """Write the user-side of a turn (chip click or text) to SQLite."""
    if not (message or chip_id):
        return
    store = get_chat_store()
    store.ensure_session(sid)
    content = message or ""
    payload = {"chip_id": chip_id} if chip_id else None
    store.append_message(sid, "user", content, payload=payload)


def _persist_turn_out(sid: str, out: dict, s: dict) -> None:
    """Write the assistant-side of a turn (whatever the dialog manager
    returned) to SQLite, plus refresh the session summary + auto-title."""
    if not isinstance(out, dict):
        return
    store = get_chat_store()
    store.ensure_session(sid)

    content = (
        out.get("reply")
        or out.get("question")
        or ""
    )
    payload = {
        "intent":             out.get("intent"),
        "slot":               out.get("slot"),
        "chips":              out.get("chips"),
        "multi_select":       out.get("multi_select"),
        "selection_mode":     out.get("selection_mode"),
        "allow_other":        out.get("allow_other"),
        "allow_text":         out.get("allow_text"),
        "fine_card":          out.get("fine_card"),
        "detail_table":       out.get("detail_table"),
        "explanation":        out.get("explanation"),
        "previous_fine_card": out.get("previous_fine_card"),
        "diff":               out.get("diff"),
        "mode":               out.get("mode"),
        "scenario":           out.get("scenario"),
        "replace_last":       out.get("replace_last") or None,
    }
    # Strip null fields so the JSON stays compact.
    payload = {k: v for k, v in payload.items() if v is not None}
    # No-op intents (the narrate-phase idle response) don't deserve a row.
    intent = out.get("intent")
    if intent == "noop" and not content:
        return
    store.append_message(sid, "assistant", content, payload=payload or None)

    summary = session_state(s)
    new_title: Optional[str] = None
    # Title-trigger intents: explicit calculator answer, smart-relocation
    # update, or a narrate-phase reply that ends up attaching a fine card.
    if intent in ("answer", "answer_updated", "scenario", "scenario_update") or (
        intent == "narrate" and out.get("fine_card")
    ):
        new_title = derive_title(summary) or None
    store.update_session_meta(
        sid,
        summary = summary,
        mode    = s.get("mode") or "static",
        title   = new_title,
    )


def _persist_user_event(sid: str, event: str, payload: Optional[dict] = None) -> None:
    """Lightweight breadcrumb for non-message side-effect calls (e.g. pin)."""
    store = get_chat_store()
    store.ensure_session(sid)
    store.append_message(sid, "user", event, payload=payload)


# ── Routes ────────────────────────────────────────────────────────────────────


@app.middleware("http")
async def _pwa_cache_headers(request, call_next):
    """Service worker + shell must always be revalidated so a new deploy reaches installed PWAs."""
    resp = await call_next(request)
    if request.url.path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest") or request.url.path.startswith("/workbox-"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
async def root():
    idx = _FRONTEND / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"status": "DriveLegal API running", "docs": "/docs"}


@app.get("/api/health")
async def health():
    # Returns instantly from cache — never blocks on external API calls.
    # The _cloud_status_checker background thread refreshes every 60 s.
    groq_ok   = _cloud_cache["groq_ok"]
    sarvam_ok = _cloud_cache["sarvam_ok"]
    ai_online = groq_ok or sarvam_ok
    age       = round(time.time() - _cloud_cache["checked_at"], 1) if _cloud_cache["checked_at"] else None
    return {
        "app":            "drivelegal",
        "status":         "ok",
        "groq_ok":        groq_ok,
        "sarvam_ok":      sarvam_ok,
        "model":          groq_model_name() if groq_ok else "offline rules engine",
        "ready":          True,          # offline engine always available
        "cloud_age_sec":  age,           # seconds since last cloud check
        # Legacy compat fields
        "ollama_ok":      ai_online,
        "model_present":  ai_online,
    }


# ── Sessions: legacy + new ───────────────────────────────────────────────────


@app.post("/api/session/start")
async def session_start():
    """Legacy entry point — defaults to static mode and registers the session
    in SQLite immediately."""
    s = get_session_store().create(mode="static")
    get_chat_store().create_session(s["session_id"], mode="static", title="New chat",
                                    summary=session_state(s))
    return {
        "session_id":      s["session_id"],
        "default_state":   DEFAULT_STATE_CODE,
        "available_modes": ["map", "chat"],
        "session_state":   session_state(s),
    }


@app.post("/api/sessions/new")
async def sessions_new(body: NewSessionBody, current_user: Optional[dict] = Depends(get_current_user)):
    s = get_session_store().create(mode=body.mode)
    get_chat_store().create_session(s["session_id"], mode=body.mode, title="New chat",
                                    summary=session_state(s))
    # If the user is authenticated, link this session to their account
    if current_user:
        link_session_to_user(s["session_id"], current_user["id"])
    return {
        "session_id":      s["session_id"],
        "mode":            body.mode,
        "default_state":   DEFAULT_STATE_CODE,
        "session_state":   session_state(s),
    }


@app.get("/api/sessions")
async def sessions_list(current_user: Optional[dict] = Depends(get_current_user)):
    # Authenticated → device-scoped; otherwise only unlinked (anonymous) sessions
    if current_user:
        rows = get_user_sessions(current_user["id"])
    else:
        rows = get_anonymous_sessions()
    out = []
    for r in rows:
        out.append({
            "id":           r["id"],
            "title":        r["title"] or "New chat",
            "mode":         r["mode"] or "static",
            "created_at":   r["created_at"],
            "updated_at":   r["updated_at"],
            "last_snippet": r.get("last_snippet") or "",
        })
    return {"sessions": out}


@app.get("/api/sessions/{session_id}/history")
async def sessions_history(session_id: str):
    store = get_chat_store()
    row = store.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown session_id: {session_id}")
    msgs = store.get_messages(session_id)
    # Best-effort: hydrate the in-memory session so subsequent /turn calls work.
    s = get_session_store().get(session_id) or _lazy_load_session(session_id)
    summary = session_state(s) if s else (row.get("summary") or {})
    return {
        "id":            row["id"],
        "title":         row["title"],
        "mode":          row["mode"] or "static",
        "messages":      msgs,
        "session_state": summary,
    }


@app.delete("/api/sessions/{session_id}")
async def sessions_delete(session_id: str):
    get_session_store().delete(session_id)
    get_chat_store().delete_session(session_id)
    return {"ok": True}


# ── Legacy single delete (kept for the reset button) ─────────────────────────


@app.delete("/api/session/{session_id}")
async def session_delete(session_id: str):
    get_session_store().delete(session_id)
    get_chat_store().delete_session(session_id)
    return {"ok": True}


# ── Mode toggle ──────────────────────────────────────────────────────────────


@app.put("/api/session/{session_id}/mode")
async def session_set_mode(session_id: str, body: ModeBody):
    s   = _get_session_or_404(session_id)
    dm  = get_dialog_manager()
    out = dm.set_mode(s, body.mode)
    get_chat_store().update_session_meta(session_id, mode=body.mode,
                                         summary=session_state(s))
    _persist_turn_out(session_id, out, s)
    return _envelope(s, out)


# ── Slot / geo / turn ────────────────────────────────────────────────────────


@app.put("/api/session/{session_id}/geo_mode")
async def session_geo_mode(session_id: str, body: GeoModeBody):
    s   = _get_session_or_404(session_id)
    dm  = get_dialog_manager()
    out = dm.set_geo_mode(s, body.mode)
    _persist_turn_out(session_id, out, s)
    return _envelope(s, out)


@app.post("/api/session/{session_id}/geo/pin")
async def session_geo_pin(session_id: str, body: PinBody):
    s   = _get_session_or_404(session_id)
    dm  = get_dialog_manager()
    _persist_user_event(session_id, f"📍 Pin dropped at {body.lat:.4f}, {body.lng:.4f}",
                        payload={"lat": body.lat, "lng": body.lng})
    out = dm.set_pin(s, body.lat, body.lng)
    _persist_turn_out(session_id, out, s)
    return _envelope(s, out)


@app.post("/api/session/{session_id}/geo/manual")
async def session_geo_manual(session_id: str, body: ManualLocationBody):
    s   = _get_session_or_404(session_id)
    dm  = get_dialog_manager()
    label = body.text or body.city_code or body.state_code or "(manual location)"
    _persist_user_event(session_id, f"📍 {label}",
                        payload={"state_code": body.state_code,
                                 "city_code":  body.city_code,
                                 "text":       body.text})
    out = dm.set_manual_location(
        s,
        state_code = body.state_code,
        city_code  = body.city_code,
        text       = body.text,
    )
    _persist_turn_out(session_id, out, s)
    return _envelope(s, out)


@app.post("/api/session/{session_id}/slot")
async def session_slot(session_id: str, body: SlotBody):
    s  = _get_session_or_404(session_id)
    dm = get_dialog_manager()
    _persist_user_event(session_id, f"chip:{body.slot}={body.value}",
                        payload={"slot": body.slot, "value": body.value})
    try:
        out = dm.set_slot(s, body.slot, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _persist_turn_out(session_id, out, s)
    return _envelope(s, out)


@app.post("/api/session/{session_id}/turn")
async def session_turn(session_id: str, body: TurnBody):
    s  = _get_session_or_404(session_id)
    # F3: transient, per-call flag. Set it only for the duration of this turn
    # and always clear it in `finally` so it never persists into the session
    # summary or bleeds into a concurrent turn on the same session.
    s["_force_rules"] = bool(body.prefer_rules)
    dm = get_dialog_manager()
    _persist_turn_in(session_id, message=body.message, chip_id=body.chip_id)
    try:
        out = dm.turn(s, message=body.message, chip_id=body.chip_id,
                      chip_ids=body.chip_ids, other_text=body.other_text)
    except Exception:
        log.exception("dialog_manager.turn failed")
        raise HTTPException(status_code=500, detail="Dialog engine error")
    finally:
        s.pop("_force_rules", None)
    _persist_turn_out(session_id, out, s)
    return _envelope(s, out)


@app.post("/api/session/{session_id}/revert_location")
async def session_revert_location(session_id: str):
    s   = _get_session_or_404(session_id)
    dm  = get_dialog_manager()
    out = dm.revert_location(s)
    _persist_turn_out(session_id, out, s)
    return _envelope(s, out)


# ── Catalogs ─────────────────────────────────────────────────────────────────


@app.get("/api/catalogs/states")
async def catalogs_states():
    return {"states": get_catalogs().states()}


@app.get("/api/catalogs/cities")
async def catalogs_cities(state: str = Query(..., min_length=2, max_length=4)):
    cities = get_catalogs().cities(state.upper())
    return {"state": state.upper(), "cities": cities}


@app.get("/api/catalogs/vehicles")
async def catalogs_vehicles():
    return {"vehicles": get_catalogs().vehicle_segments()}


@app.get("/api/catalogs/violation_categories")
async def catalogs_violation_categories():
    return {"categories": get_catalogs().violation_categories()}


# ── Law-update endpoints ──────────────────────────────────────────────────────


@app.get("/api/update/status")
async def update_status():
    """Return the last update run metadata + patch count.
    The mobile app polls this to decide whether to show the UpdateBanner."""
    status = get_update_status()
    status["is_running"] = is_running()
    return status


@app.post("/api/update/run")
async def update_run():
    """Trigger a background scrape-and-patch cycle.
    Returns immediately; poll /api/update/status for progress."""
    started = start_update_background()
    if not started:
        return {"ok": False, "message": "Update already in progress"}
    return {"ok": True, "message": "Update started in background"}


@app.get("/api/update/patches")
async def update_patches():
    """Return all stored patches for the mobile app to cache locally.
    Patches are separate from the base graph — base data is never changed."""
    patches = get_all_patches(limit=1000)
    return {"patches": patches, "count": len(patches)}


# ── Sarvam AI endpoints ───────────────────────────────────────────────────────

class SarvamTranslateBody(BaseModel):
    text:            str
    target_language: str            # BCP-47 e.g. "hi-IN"
    source_language: str = "en-IN"
    mode:            str = "classic-colloquial"


class SarvamTTSBody(BaseModel):
    text:          str
    language_code: str   = "en-IN"
    pace:          float = 0.9


@app.post("/api/sarvam/translate")
async def sarvam_translate(body: SarvamTranslateBody):
    """Translate text using Mayura v1 (11 Indian languages)."""
    try:
        translated = await asyncio.to_thread(
            translate_text,
            body.text,
            body.target_language,
            source_language_code = body.source_language,
            mode                 = body.mode,
        )
        return {"translated": translated, "target_language": body.target_language,
                "engine": "sarvam"}
    except SarvamOfflineError as e:
        sarvam_err = e
    # Fallback: translate with the Groq LLM so the language picker still works
    # without Sarvam credits.
    try:
        from llm_chatbot import groq_translate
        translated = await asyncio.to_thread(
            groq_translate, body.text, body.target_language, body.source_language)
        return {"translated": translated, "target_language": body.target_language,
                "engine": "groq"}
    except Exception as e:
        raise HTTPException(status_code=503,
                            detail=f"Translation unavailable (Sarvam: {sarvam_err}; Groq: {e})")


@app.post("/api/sarvam/tts")
async def sarvam_tts(body: SarvamTTSBody):
    """Convert text to speech using Bulbul v3. Returns base64 WAV."""
    try:
        audio_b64 = await asyncio.to_thread(
            synthesize_speech,
            body.text,
            body.language_code,
            pace = body.pace,
        )
        return {"audio_base64": audio_b64, "format": "wav", "sample_rate": 22050}
    except SarvamOfflineError as e:
        raise HTTPException(status_code=503, detail=f"Sarvam offline: {e}")



# ── 3D knowledge graph (before static mount) ─────────────────────────────────
@app.get("/viz")
async def knowledge_graph_viz():
    """Interactive Three.js explorer for drivelegal_graph.json."""
    if not _VIZ_HTML.is_file():
        raise HTTPException(status_code=404, detail="Visualization file not found")
    return FileResponse(_VIZ_HTML, media_type="text/html; charset=utf-8")


# ── Static frontend (mounted last so /api/* still wins) ──────────────────────
if _FRONTEND is _DESKTOP and _LEGACY_WEB.exists():
    app.mount("/classic", StaticFiles(directory=str(_LEGACY_WEB), html=True), name="classic")
class _SPAStatic(StaticFiles):
    """Static files with a single-page-app fallback: unknown paths (e.g. /chat?sessionId=…) get index.html,
    so reloading a deep link in the PWA works."""
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except Exception as exc:          # starlette raises HTTPException(404) for missing files
            if getattr(exc, "status_code", None) == 404 and "." not in path.rsplit("/", 1)[-1] \
                    and not path.lstrip("/").startswith(("api/", "auth/", "docs", "openapi", "viz", "classic")):
                return await super().get_response("index.html", scope)
            raise


if _FRONTEND.exists():
    app.mount("/", _SPAStatic(directory=str(_FRONTEND), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
