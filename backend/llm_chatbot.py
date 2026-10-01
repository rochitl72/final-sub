#!/usr/bin/env python3
"""
llm_chatbot.py — Groq-powered LLM backend for DriveLegal chatbot
=================================================================
Replaces the previous Ollama integration.

Model  : GROQ_CHAT_MODEL env var, else the first available of
         GROQ_MODEL_PREFERENCE (default openai/gpt-oss-20b — Groq retired
         llama-3.1-8b-instant). Auto-switches if Groq retires a model.
Key    : configurable via GROQ_CHAT_API_KEY env var.

On network failure (offline / Groq down), raises GroqOfflineError so
the caller (dynamic_chatbot) can fall back to offline_engine instantly.

Public surface:
  handle_freeform(session, user_text)  -> {"reply": str, "fine_card": dict?}
  warmup()                              -> no-op (Groq has no cold-start)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional

import httpx

from graph_engine import get_graph_engine

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_ENDPOINT = "https://api.groq.com/openai/v1/models"

# Fast, protocol-following chat models, best first. Groq retires models often
# (llama-3.1-8b-instant is gone), so we pick the first one the key can use.
GROQ_MODEL_PREFERENCE = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]
# Extra completion budget for reasoning models: their hidden reasoning tokens
# count against max_tokens and would otherwise leave `content` empty.
_REASONING_BUDGET = 512

_active_model: Optional[str] = None
_retired_models: set = set()

NUM_PREDICT_ANSWER = 600


def _groq_key() -> str:
    """Read GROQ_CHAT_API_KEY fresh from the environment on every call."""
    return os.environ.get("GROQ_CHAT_API_KEY", "")


class GroqOfflineError(Exception):
    """Raised when Groq is unreachable so the caller can fall back to offline_engine."""


def _available_models(key: str) -> Optional[List[str]]:
    """IDs the key can use (free endpoint — costs no tokens). None on failure."""
    try:
        r = httpx.get(GROQ_MODELS_ENDPOINT,
                      headers={"Authorization": f"Bearer {key}"}, timeout=8.0)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return [m.get("id") for m in r.json().get("data", []) if m.get("active", True)]


def _resolve_model(key: str, *, refresh: bool = False) -> str:
    """Pick the chat model: GROQ_CHAT_MODEL if set, else first available."""
    global _active_model
    if _active_model and not refresh:
        return _active_model
    wanted = os.environ.get("GROQ_CHAT_MODEL", "").strip()
    prefs = ([wanted] if wanted else []) + [
        m for m in GROQ_MODEL_PREFERENCE if m != wanted]
    prefs = [m for m in prefs if m not in _retired_models] or prefs
    avail = _available_models(key)
    if avail:
        pick = next((m for m in prefs if m in avail), None)
        if wanted and wanted not in avail:
            log.warning("GROQ_CHAT_MODEL=%s not available to this key", wanted)
        if pick:
            if pick != _active_model:
                log.info("Groq chat model: %s", pick)
            _active_model = pick
            return pick
    return prefs[0]


def groq_model_name() -> Optional[str]:
    """Model currently in use (None until resolved)."""
    return _active_model


def _model_params(model: str, max_tokens: int) -> dict:
    """Per-family request tweaks so reasoning models still return content."""
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low",
                "max_tokens": max_tokens + _REASONING_BUDGET}
    if model.startswith("qwen/"):
        return {"reasoning_effort": "none", "max_tokens": max_tokens}
    return {"max_tokens": max_tokens}


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


# ── System prompt builder ─────────────────────────────────────────────────────

def _build_system_prompt(session: dict) -> str:
    eng = get_graph_engine()
    ctx_parts: List[str] = []
    if session.get("city_name"):
        ctx_parts.append(f"City: {session['city_name']} ({session.get('city_code','')})")
    if session.get("state_code"):
        st = eng.get_state(session["state_code"])
        st_name = st["name"] if st else session["state_code"]
        ctx_parts.append(f"State: {st_name} ({session['state_code']})")
    if session.get("road_bucket"):
        ctx_parts.append(f"Road: {session['road_bucket']}")
    if session.get("vehicle_type"):
        ctx_parts.append(f"Vehicle: {session['vehicle_type']}")
    ctx = "\n".join(ctx_parts) if ctx_parts else "No location set yet."

    subgraph_text = ""
    if session.get("road_bucket"):
        subgraph_text = "\n\n" + eng.subgraph_text(
            session["road_bucket"],
            session.get("state_code"),
            session.get("city_code"),
            session.get("vehicle_fine_class"),
        )

    if session.get("mode") == "dynamic":
        try:
            from dynamic_chatbot import CONSTABLE_PERSONA_PROMPT
        except Exception:
            CONSTABLE_PERSONA_PROMPT = (
                "You are DriveLegal — a friendly Indian traffic constable. "
                "2-4 sentences, cite §section, use ₹."
            )
        persona = CONSTABLE_PERSONA_PROMPT
    else:
        persona = (
            "You are DriveLegal — a friendly Indian traffic law assistant. "
            "Use Motor Vehicles Act 1988 (amended 2019). 2–4 sentence replies, "
            "always cite §section, always use ₹ for amounts."
        )

    return (
        f"{persona}\n\n"
        f"SESSION CONTEXT:\n{ctx}\n\n"
        "When you give a specific fine answer, append [VIOLATION:CODE] at the "
        "VERY end (e.g. [VIOLATION:SAFETY_NO_HELMET_RIDER]) using a code from "
        "the RELEVANT VIOLATIONS list. Don't invent fine amounts — only use "
        "values from that list."
        f"{subgraph_text}"
    )


# ── Sarvam-M LLM call (primary) ──────────────────────────────────────────────

# Sarvam's current chat models (sarvam-30b / sarvam-105b, after sarvam-m was
# deprecated) are REASONING models: they return their output in
# `reasoning_content` and leave `content` empty, and they're slow (they spend
# the whole token budget "thinking"). That makes them unusable for our fast
# narrate turns — an empty reply means no violation gets matched, which is what
# broke the AI chat mode. So we route chat to Groq (which works well) and keep
# Sarvam only for TTS + translate. Flip this to re-enable if Sarvam ships a
# non-reasoning chat model again.
_SARVAM_CHAT_ENABLED = False


def _call_sarvam(messages: List[dict], max_tokens: int = 600, temperature: float = 0.4) -> str:
    """
    Sarvam chat completion. Disabled for chat (see note above) — raises so the
    caller falls straight through to Groq with no wasted/slow network call.
    """
    if not _SARVAM_CHAT_ENABLED:
        raise GroqOfflineError("Sarvam chat disabled (reasoning-model returns empty content)")
    from sarvam_service import chat_complete, SarvamOfflineError
    try:
        reply = chat_complete(messages, max_tokens=max_tokens, temperature=temperature)
        if not (reply and reply.strip()):
            raise GroqOfflineError("Sarvam returned empty content")
        return reply
    except SarvamOfflineError as e:
        raise GroqOfflineError(f"Sarvam offline: {e}") from e


# ── Groq API call (fallback) ──────────────────────────────────────────────────

def _json_mode_enabled() -> bool:
    """Structured-output switch (shared with dynamic_chatbot). Default OFF."""
    return os.environ.get("DRIVELEGAL_LLM_JSON", "").strip().lower() in ("1", "true", "yes")


# Larger model for open-ended reasoning (questions no rule matched); it has
# its own rate-limit budget on Groq. The small fast model does everything else.
GROQ_REASONING_PREFERENCE = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
_avail_cache: Dict[str, object] = {"models": None}


def _reasoning_model(key: str) -> Optional[str]:
    wanted = os.environ.get("GROQ_REASONING_MODEL", "").strip()
    if _avail_cache["models"] is None:
        _avail_cache["models"] = _available_models(key) or []
    avail = _avail_cache["models"] or []
    for m in ([wanted] if wanted else []) + GROQ_REASONING_PREFERENCE:
        if m in avail and m not in _retired_models:
            return m
    return None


def _call_groq(
    messages: List[dict],
    max_tokens: int = 600,
    temperature: float = 0.3,
    json_mode: Optional[bool] = None,
    reasoning: bool = False,
) -> str:
    """
    Single Groq chat completion call.
    Raises GroqOfflineError on any network / connectivity failure.
    Raises RuntimeError on API errors (bad key, quota, etc.).

    When structured-output is enabled, request a JSON object via response_format.
    If the provider rejects that parameter we transparently retry WITHOUT it, so
    the online path can never regress because of JSON mode.
    """
    key = _groq_key()
    if not key:
        raise GroqOfflineError("GROQ_CHAT_API_KEY not set — add it to .env in the repo root")

    want_json = _json_mode_enabled() if json_mode is None else json_mode
    model = (_reasoning_model(key) if reasoning else None) or _resolve_model(key)

    def _post(with_json: bool) -> "httpx.Response":
        payload = {
            "model":       model,
            "messages":    messages,
            "temperature": temperature,
            **_model_params(model, max_tokens),
        }
        if with_json:
            payload["response_format"] = {"type": "json_object"}
        return httpx.post(
            GROQ_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=20.0,
        )

    try:
        resp = _post(want_json)
        # If JSON mode was the problem (bad-request), retry once without it.
        if want_json and resp.status_code == 400:
            log.info("Groq rejected response_format — retrying without JSON mode")
            resp = _post(False)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        raise GroqOfflineError(f"Groq unreachable: {exc}") from exc
    except Exception as exc:
        raise GroqOfflineError(f"Groq request failed: {exc}") from exc

    # Model retired / not enabled for this key → switch model and retry once.
    if resp.status_code in (400, 404) and "model" in resp.text and (
            "not found" in resp.text or "does not exist" in resp.text
            or "decommissioned" in resp.text):
        _retired_models.add(model)
        new_model = _resolve_model(key, refresh=True)
        if new_model != model:
            log.warning("Groq model %s unavailable — switching to %s", model, new_model)
            model = new_model
            try:
                resp = _post(False)
            except Exception as exc:
                raise GroqOfflineError(f"Groq request failed: {exc}") from exc

    if resp.status_code == 429:
        raise GroqOfflineError("Groq rate limit hit")
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:200]}")

    content = resp.json()["choices"][0]["message"].get("content") or ""
    content = _THINK_RE.sub("", content).strip()
    if not content:
        # e.g. a reasoning model spent the whole budget thinking — let the
        # caller fall back to the offline engine rather than show nothing.
        raise GroqOfflineError(f"Groq ({model}) returned empty content")
    return content


# ── Offline fallback for freeform (calculator-mode) turns ────────────────────

def _offline_freeform(session: dict, user_text: str) -> Dict[str, Optional[object]]:
    """Deterministic, network-free answer for a freeform turn.

    Reuses the dynamic-mode offline narrator (graph + resolver) and strips its
    <<SLOTS>> protocol line. If it resolved a violation and we know the state,
    attach a graph-sourced fine card so the amount is always grounded.
    """
    from offline_engine import narrate_protocol
    from dynamic_chatbot import _parse_protocol

    # Work on a copy without the previous violation so an unrelated question
    # isn't answered with the last fine again (follow-ups are handled upstream).
    scratch = {**session, "violation_code": None}
    try:
        raw    = narrate_protocol(scratch, user_text)
        parsed = _parse_protocol(raw)
        reply  = parsed.get("clean_reply") or ""
        vcode  = (parsed.get("slots") or {}).get("violation_code")
    except Exception:
        log.exception("offline freeform failed")
        reply, vcode = "", None

    if not reply:
        reply = ("I couldn't find a rule for that offline. Try picking one of "
                 "the violation chips, or describe the offence in a few words "
                 "(e.g. 'no helmet', 'jumped a red light').")

    fine_card = None
    eng = get_graph_engine()
    if vcode and vcode != "?" and session.get("state_code") and eng.get_violation(vcode):
        try:
            fine_card = eng.quick_fine(
                violation_code     = vcode,
                state_code         = session.get("state_code"),
                city_code          = session.get("city_code"),
                vehicle_fine_class = session.get("vehicle_fine_class"),
            )
        except Exception:
            log.exception("quick_fine failed for %s", vcode)
    return {"reply": reply, "fine_card": fine_card}


# ── Public: handle_freeform ───────────────────────────────────────────────────

def handle_freeform(session: dict, user_text: str) -> Dict[str, Optional[object]]:
    """
    One LLM turn.  Returns `{"reply": str, "fine_card": dict | None}`.

    Raises GroqOfflineError if network is unavailable — caller should
    fall back to offline_engine.
    """
    eng           = get_graph_engine()
    system_prompt = _build_system_prompt(session)
    history       = (session.get("messages") or [])[-6:]

    if session.get("mode") == "dynamic":
        try:
            from dynamic_chatbot import DYNAMIC_GEN_OPTIONS
            temperature = DYNAMIC_GEN_OPTIONS.get("temperature", 0.4)
            max_tokens  = DYNAMIC_GEN_OPTIONS.get("num_predict", 350)
        except Exception:
            temperature, max_tokens = 0.4, 350
    else:
        temperature, max_tokens = 0.3, NUM_PREDICT_ANSWER

    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": user_text}]
    )

    # Rules-only requested by the client (mobile "Rules" mode) → skip the LLM.
    if session.get("_force_rules"):
        return _offline_freeform(session, user_text)

    # Try Sarvam-M first (India-trained), fall back to Groq, then to the
    # deterministic offline engine so a missing key / no network never 500s.
    try:
        try:
            reply = _call_sarvam(messages, max_tokens=max_tokens, temperature=temperature)
            log.debug("handle_freeform: Sarvam-M OK")
        except GroqOfflineError:
            log.info("handle_freeform: Sarvam-M unavailable, trying Groq")
            reply = _call_groq(messages, max_tokens=max_tokens, temperature=temperature,
                               reasoning=True)
    except (GroqOfflineError, RuntimeError) as exc:
        log.info("handle_freeform: cloud LLM unavailable (%s) — using offline_engine", exc)
        return _offline_freeform(session, user_text)
    if not (reply and reply.strip()):
        return _offline_freeform(session, user_text)

    fine_card = None
    m = re.search(r"\[VIOLATION:([A-Z0-9_]+)\]", reply)
    if m:
        vcode = m.group(1)
        if eng.get_violation(vcode):
            fine_card = eng.quick_fine(
                violation_code     = vcode,
                state_code         = session.get("state_code"),
                city_code          = session.get("city_code"),
                vehicle_fine_class = session.get("vehicle_fine_class"),
            )
        reply = re.sub(r"\s*\[VIOLATION:[A-Z0-9_]+\]\s*", "", reply).strip()

    # Grounding: only amounts / sections from the cards in play may appear;
    # any sentence quoting something else is dropped (the model invents them).
    reply = _ground_freeform(reply, [c for c in (fine_card, session.get("last_fine_card")) if c])
    if not reply:
        return _offline_freeform(session, user_text)
    return {"reply": reply, "fine_card": fine_card}


def _ground_freeform(reply: str, cards: List[dict]) -> str:
    import nlu
    from graph_engine import card_amounts
    allowed_amts: set = set()
    sections: List[str] = []
    for c in cards:
        allowed_amts |= card_amounts(c)
        if c.get("mv_section"):
            sections.append(str(c["mv_section"]))
    sents = re.split(r"(?<=[.!?])\s+|\n+", reply or "")
    kept = [x for x in sents if x.strip() and not nlu.llm_reply_problems(
        x, allowed_amounts=allowed_amts, allowed_sections=sections, session={})]
    total = len([x for x in sents if x.strip()])
    if len(kept) == total:
        return (reply or "").strip()          # untouched — keep formatting
    log.info("freeform: dropped %d ungrounded sentence(s)", total - len(kept))
    return " ".join(k.strip() for k in kept).strip()


# ── Public: Groq narrate call (used by dynamic_chatbot) ──────────────────────

def call_groq_narrate(
    system_prompt: str,
    history: List[dict],
    user_text: str,
    *,
    temperature: float = 0.4,
    max_tokens:  int   = 280,
    reasoning:   bool  = False,
) -> str:
    """
    Narrate-phase online LLM call with <<SLOTS>> protocol.
    Tries Sarvam-M first (India-trained), falls back to Groq.
    Raises GroqOfflineError if both are unreachable.
    """
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": user_text}]
    )
    try:
        result = _call_sarvam(messages, max_tokens=max_tokens, temperature=temperature)
        log.debug("call_groq_narrate: Sarvam-M OK")
        return result
    except GroqOfflineError:
        log.info("call_groq_narrate: Sarvam-M unavailable, trying Groq")
        return _call_groq(messages, max_tokens=max_tokens, temperature=temperature,
                          reasoning=reasoning)


# ── Public: warmup (no-op for Groq) ──────────────────────────────────────────

def warmup() -> bool:
    """Resolve the Groq model at startup (token-free). True if Groq is usable."""
    key = _groq_key()
    if not key:
        log.info("Groq not configured (GROQ_CHAT_API_KEY not set) — offline engine will be used.")
        return False
    if _available_models(key) is None:
        log.info("Groq not reachable or key rejected — offline engine will be used.")
        return False
    log.info("Groq reachability check OK (model=%s)", _resolve_model(key, refresh=True))
    return True


def check_groq_status() -> bool:
    """Connectivity + key check for /api/health. Uses the free /models
    endpoint, so the 60 s background poll never spends tokens."""
    key = _groq_key()
    if not key:
        return False
    avail = _available_models(key)
    if not avail:
        return False
    return _resolve_model(key, refresh=True) in avail
