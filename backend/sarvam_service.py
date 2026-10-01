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

def translate_text(
    text: str,
    target_language_code: str,
    source_language_code: str = "en-IN",
    *,
    mode: str = "modern-colloquial",
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

    if resp.status_code == 401:
        raise SarvamOfflineError("Translate: invalid API key")
    if resp.status_code == 429:
        raise SarvamOfflineError("Translate rate limited")
    if resp.status_code != 200:
        log.warning("Translate error %d: %s", resp.status_code, resp.text[:200])
        return text

    translated = resp.json().get("translated_text", text)
    log.info("Translate OK: %s→%s", source_language_code, target_language_code)
    return translated


# ── 4. Sarvam-M LLM ──────────────────────────────────────────────────────────

def chat_complete(
    messages: list,
    *,
    max_tokens:  int   = 300,
    temperature: float = 0.4,
    model: str         = "sarvam-30b",   # sarvam-m deprecated Jul-2026 → use sarvam-30b
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
    if not _api_key():
        log.debug("check_sarvam_status: no API key")
        return False
    try:
        # Lightweight GET — just checks network reachability
        resp = httpx.get(SARVAM_BASE, timeout=4.0)
        return resp.status_code < 500
    except Exception as e:
        log.debug("check_sarvam_status: unreachable (%s)", e)
        return False
