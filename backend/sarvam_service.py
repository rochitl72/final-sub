#!/usr/bin/env python3
"""
sarvam_service.py — Sarvam AI API wrappers for DriveLegal
==========================================================
Integrates all 4 Sarvam capabilities:
  1. STT   — Saaras v3  (speech-to-text, 23 Indian languages, auto-detect)
  2. TTS   — Bulbul v3  (text-to-speech, 11 languages, 30+ speakers)
  3. Trans — Mayura v1  (translate, 11 languages, modern-colloquial mode)
  4. LLM   — Sarvam-M   (India-trained chat LLM, OpenAI-compatible)

All functions raise SarvamOfflineError on network/connectivity failures
so callers can degrade gracefully to the offline engine.

API key: set SARVAM_API_KEY in the .env file at the project root.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

import httpx

log = logging.getLogger("drivelegal.sarvam")

# ── Config ────────────────────────────────────────────────────────────────────

SARVAM_BASE     = "https://api.sarvam.ai"
SARVAM_CHAT_URL = f"{SARVAM_BASE}/v1/chat/completions"
SARVAM_TTS_URL  = f"{SARVAM_BASE}/text-to-speech"
SARVAM_TRL_URL  = f"{SARVAM_BASE}/translate"

TIMEOUT = 20.0   # seconds — generous for TTS/STT


class SarvamOfflineError(Exception):
    """Raised when Sarvam API is unreachable — caller falls back to offline path."""


# ── Key helpers — read at call time, not module load ─────────────────────────
# This ensures keys loaded from .env by start.sh are always current,
# and the module never caches an empty key from before the env was set.

def _api_key() -> str:
    """Read SARVAM_API_KEY fresh from the environment on every call."""
    return os.environ.get("SARVAM_API_KEY", "")


def _sub_hdrs() -> dict:
    return {"api-subscription-key": _api_key()}


def _bearer_hdrs() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type":  "application/json",
    }


def _require_api_key() -> None:
    if not _api_key():
        raise SarvamOfflineError(
            "SARVAM_API_KEY not set — add it to .env in the repo root and restart"
        )
    if _quota_blocked():
        raise SarvamOfflineError("Sarvam account has no credits left")


# A key can be valid but out of credits (HTTP 4xx "insufficient_quota_error").
# Remember that for a while so /api/health reports sarvam_ok=false and callers
# fall back (Groq translation, browser speech) instead of failing every request.
_QUOTA_BACKOFF_S = 30 * 60
_quota_until: float = 0.0
_probed: bool = False


def _quota_blocked() -> bool:
    return time.time() < _quota_until


def _raise_for_quota(resp: "httpx.Response", what: str) -> None:
    global _quota_until
    body = resp.text[:400]
    if resp.status_code in (402, 403) or "insufficient_quota" in body or "No credits" in body:
        _quota_until = time.time() + _QUOTA_BACKOFF_S
        log.warning("Sarvam %s: no credits on this key — disabling Sarvam for %d min",
                    what, _QUOTA_BACKOFF_S // 60)
        raise SarvamOfflineError("Sarvam account has no credits left")


# ── 1. Text-to-Speech ─────────────────────────────────────────────────────────

# Bulbul v2 names (meera, pavithra, anushka) are invalid on bulbul:v3.
# Omit speaker for Indian languages → Sarvam picks a language-appropriate default.
# Only set speakers we have verified against the live v3 API.
_BULBUL_V3_SPEAKERS: dict[str, str] = {
    "en-IN": "shubh",
}

def synthesize_speech(
    text: str,
    language_code: str = "en-IN",
    *,
    pace: float        = 0.9,
    sample_rate: int   = 22050,
) -> str:
    """
    Convert text to speech using Bulbul v3.
    Returns base64-encoded WAV audio string.
    """
    _require_api_key()
    text = text[:2500]
    speaker = _BULBUL_V3_SPEAKERS.get(language_code)

    payload: dict = {
        "inputs":               [text],
        "target_language_code": language_code,
        "model":                "bulbul:v3",
        "speech_sample_rate":   sample_rate,
        "pace":                 pace,
        "enable_preprocessing": True,
    }
    if speaker:
        payload["speaker"] = speaker

    try:
        resp = httpx.post(
            SARVAM_TTS_URL,
            headers=_sub_hdrs(),
            json=payload,
            timeout=TIMEOUT,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
        raise SarvamOfflineError(f"TTS unreachable: {e}") from e

    _raise_for_quota(resp, "TTS")
    if resp.status_code == 401:
        raise SarvamOfflineError("TTS: invalid API key")
    if resp.status_code == 429:
        raise SarvamOfflineError("TTS rate limited")
    if resp.status_code != 200:
        log.warning("TTS error %d: %s", resp.status_code, resp.text[:200])
        detail = resp.text[:300]
        try:
            detail = resp.json().get("error", {}).get("message", detail)
        except Exception:
            pass
        raise SarvamOfflineError(f"TTS API error {resp.status_code}: {detail}")

    audios = resp.json().get("audios", [])
    if not audios:
        raise SarvamOfflineError("TTS returned empty audio list")
    log.info(
        "TTS OK: lang=%s speaker=%s len=%d chars",
        language_code,
        speaker or "(default)",
        len(text),
    )
    return audios[0]


# ── 3. Translation ────────────────────────────────────────────────────────────

def _translate_one(
    text: str,
    target_language_code: str,
    source_language_code: str = "en-IN",
    *,
    mode: str = "classic-colloquial",
) -> str:
    """
    Translate text using Mayura v1.
    Returns translated string, or original text on non-critical failures.
    """
    if not text.strip():
        return text
    if target_language_code == source_language_code or target_language_code == "en-IN":
        return text

    _require_api_key()
    # Mayura treats the full stop in "s.194D" as a sentence break and mangles
    # the section number; spell it out so the reference survives translation.
    text = re.sub(r"\bs\.\s?(?=\d)", "Section ", text)
    text = re.sub(r"\bss\.\s?(?=\d)", "Sections ", text)
    # "Fine ₹1,000" is read as the adjective ("okay") → ठीक है / சரி; "penalty"
    # translates correctly.
    text = re.sub(r"\bFines\b", "Penalties", text)
    text = re.sub(r"\bfines\b", "penalties", text)
    text = re.sub(r"\bFine\b(?=[:\s]*[₹\d])", "Penalty", text)
    text = re.sub(r"\bfine\b(?=[:\s]*(?:of\s+)?[₹\d])", "penalty", text)
    try:
        resp = httpx.post(
            SARVAM_TRL_URL,
            headers=_sub_hdrs(),
            json={
                "input":                text[:2000],
                "source_language_code": source_language_code,
                "target_language_code": target_language_code,
                "model":                "mayura:v1",
                "mode":                 mode,
            },
            timeout=TIMEOUT,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
        raise SarvamOfflineError(f"Translate unreachable: {e}") from e

    _raise_for_quota(resp, "translate")
    if resp.status_code == 401:
        raise SarvamOfflineError("Translate: invalid API key")
    if resp.status_code == 429:
        raise SarvamOfflineError("Translate rate limited")
    if resp.status_code != 200:
        # Never return the English text as if it were a translation — let the
        # caller fall back to another translator.
        log.warning("Translate error %d: %s", resp.status_code, resp.text[:200])
        raise SarvamOfflineError(f"Translate error {resp.status_code}")

    translated = resp.json().get("translated_text", text)
    log.info("Translate OK: %s→%s", source_language_code, target_language_code)
    return translated


# ── 4. Sarvam-M LLM ──────────────────────────────────────────────────────────

def chat_complete(
    messages: list,
    *,
    max_tokens:  int   = 300,
    temperature: float = 0.4,
    model: str         = "sarvam-105b",  # sarvam-m / sarvam-30b deprecated → sarvam-105b
) -> str:
    """
    Chat completion using Sarvam (India-trained, OpenAI-compatible).
    Returns the assistant reply string.
    """
    _require_api_key()
    try:
        resp = httpx.post(
            SARVAM_CHAT_URL,
            headers=_bearer_hdrs(),
            json={
                "model":       model,
                "messages":    messages,
                "temperature": temperature,
                "max_tokens":  max_tokens,
            },
            timeout=TIMEOUT,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
        raise SarvamOfflineError(f"Sarvam LLM unreachable: {e}") from e

    _raise_for_quota(resp, "LLM")
    if resp.status_code == 401:
        raise SarvamOfflineError("Sarvam LLM: invalid API key")
    if resp.status_code == 429:
        raise SarvamOfflineError("Sarvam LLM rate limited")
    if resp.status_code != 200:
        raise SarvamOfflineError(f"Sarvam LLM error {resp.status_code}")

    return resp.json()["choices"][0]["message"]["content"] or ""


# ── Connectivity check ────────────────────────────────────────────────────────

def check_sarvam_status() -> bool:
    """
    Returns True only when BOTH the API key is set AND Sarvam is reachable.
    Used by /api/health to determine cloudFeaturesEnabled on the mobile.
    """
    global _probed
    if not _api_key():
        log.debug("check_sarvam_status: no API key")
        return False
    if _quota_blocked():
        return False
    if not _probed:
        # One real (tiny) call per process: catches a bad key or an account with
        # no credits, which a bare reachability GET can't see.
        _probed = True
        try:
            translate_text("ok", "hi-IN")
        except SarvamOfflineError as e:
            log.warning("Sarvam disabled: %s", e)
            return False
        except Exception:
            pass
    try:
        # Lightweight GET — just checks network reachability
        resp = httpx.get(SARVAM_BASE, timeout=4.0)
        return resp.status_code < 500
    except Exception as e:
        log.debug("check_sarvam_status: unreachable (%s)", e)
        return False


_BULLET_RE = re.compile(r"^(\s*(?:[•\-*]|\d+[.)])\s+)(.*)$")


def translate_text(
    text: str,
    target_language_code: str,
    source_language_code: str = "en-IN",
    *,
    mode: str = "classic-colloquial",
) -> str:
    """Translate keeping the answer's layout. Sarvam's /translate collapses line
    breaks and bullets, so translate line by line (in parallel) and re-join."""
    if "\n" not in text.strip():
        return _translate_one(text, target_language_code, source_language_code, mode=mode)
    from concurrent.futures import ThreadPoolExecutor

    lines = text.split("\n")
    jobs: dict[int, tuple[str, str]] = {}
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        m = _BULLET_RE.match(ln)
        prefix, body = (m.group(1), m.group(2)) if m else ("", ln)
        jobs[i] = (prefix, body)

    def run(item):
        i, (_, body) = item
        return i, _translate_one(body, target_language_code, source_language_code, mode=mode)

    out = list(lines)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, tr in ex.map(run, jobs.items()):
            out[i] = jobs[i][0] + tr
    return "\n".join(out)
