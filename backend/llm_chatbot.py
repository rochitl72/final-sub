#!/usr/bin/env python3
"""
llm_chatbot.py — Groq-powered LLM backend for DriveLegal chatbot
=================================================================
Replaces the previous Ollama integration.

Model  : llama-3.1-8b-instant (Groq free tier)
Limits : 30 RPM, 6000 TPM, 14400 RPD — well within normal chat usage.
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
GROQ_MODEL    = "llama-3.1-8b-instant"

NUM_PREDICT_ANSWER = 600


def _groq_key() -> str:
    """Read GROQ_CHAT_API_KEY fresh from the environment on every call."""
    return os.environ.get("GROQ_CHAT_API_KEY", "")


class GroqOfflineError(Exception):
    """Raised when Groq is unreachable so the caller can fall back to offline_engine."""


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


def _call_groq(
    messages: List[dict],
    max_tokens: int = 600,
    temperature: float = 0.3,
    json_mode: Optional[bool] = None,
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
        raise GroqOfflineError("GROQ_CHAT_API_KEY not set — add it to drivelegal/.env")

    want_json = _json_mode_enabled() if json_mode is None else json_mode

    def _post(with_json: bool) -> "httpx.Response":
        payload = {
            "model":       GROQ_MODEL,
            "messages":    messages,
            "temperature": temperature,
            "max_tokens":  max_tokens,
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

    if resp.status_code == 429:
        raise GroqOfflineError("Groq rate limit hit")
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:200]}")

    return resp.json()["choices"][0]["message"]["content"] or ""


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

    # Try Sarvam-M first (India-trained), fall back to Groq
    try:
        reply = _call_sarvam(messages, max_tokens=max_tokens, temperature=temperature)
        log.debug("handle_freeform: Sarvam-M OK")
    except GroqOfflineError:
        log.info("handle_freeform: Sarvam-M unavailable, trying Groq")
        reply = _call_groq(messages, max_tokens=max_tokens, temperature=temperature)

    fine_card = None
    m = re.search(r"\[VIOLATION:([A-Z0-9_]+)\]", reply)
    if m:
        vcode = m.group(1)
        fine_card = eng.quick_fine(
            violation_code     = vcode,
            state_code         = session.get("state_code"),
            city_code          = session.get("city_code"),
            vehicle_fine_class = session.get("vehicle_fine_class"),
        )
        reply = re.sub(r"\s*\[VIOLATION:[A-Z0-9_]+\]\s*", "", reply).strip()

    return {"reply": reply, "fine_card": fine_card}


# ── Public: Groq narrate call (used by dynamic_chatbot) ──────────────────────

def call_groq_narrate(
    system_prompt: str,
    history: List[dict],
    user_text: str,
    *,
    temperature: float = 0.4,
    max_tokens:  int   = 280,
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
        return _call_groq(messages, max_tokens=max_tokens, temperature=temperature)


# ── Public: warmup (no-op for Groq) ──────────────────────────────────────────

def warmup() -> bool:
    """
    No warmup needed — Groq has no cold-start latency.
    Kept for API compatibility with the old Ollama implementation.
    Returns True if Groq is reachable, False otherwise.
    """
    try:
        _call_groq(
            [{"role": "user", "content": "hi"}],
            max_tokens=1,
            temperature=0.0,
        )
        log.info("Groq reachability check OK (model=%s)", GROQ_MODEL)
        return True
    except GroqOfflineError as e:
        log.info("Groq not reachable at startup (%s) — offline engine will be used.", e)
        return False
    except Exception as e:
        log.warning("Groq warmup check failed: %s", e)
        return False


# ── Public: check_groq_status ─────────────────────────────────────────────────

def check_groq_status() -> bool:
    """Fast connectivity check for the /api/health endpoint."""
    try:
        _call_groq(
            [{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0.0,
        )
        return True
    except Exception:
        return False
