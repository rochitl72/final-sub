"""
llm_provider.py — which LLM DriveLegal talks to
================================================

DriveLegal never lets an LLM decide a fine: the model only (a) reads a story
into JSON, (b) writes a one-line lead, (c) answers open questions that are then
grounded, and (d) translates when Sarvam can't. All of those go through
`chat()` here, so switching the model is a configuration change.

Providers
---------
LLM_PROVIDER=ollama   self-hosted Ollama (e.g. the org server)  ← production
LLM_PROVIDER=groq     Groq cloud (previous default)
(unset)               ollama when OLLAMA_URL is set, else groq

Ollama settings (all optional except the URL)
---------------------------------------------
OLLAMA_URL            base URL or the full /api/generate URL
                      e.g. http://10.42.235.232:11434/api/generate
OLLAMA_API            generate (default) | chat
OLLAMA_MODEL          model for everything (else picked from /api/tags)
OLLAMA_EXTRACT_MODEL  optional bigger model just for story extraction
OLLAMA_NUM_CTX        context window to request (default 8192)
OLLAMA_TIMEOUT        seconds per request (default 60)
OLLAMA_MAX_PARALLEL   max concurrent requests from this backend (default 2)
OLLAMA_KEEP_ALIVE     how long the server keeps the model loaded (default 30m)
OLLAMA_THINK          for thinking models: false|low|medium|high (default false)

Production behaviour
--------------------
* Bounded concurrency (semaphore) so one busy chat can't swamp a shared server.
* Circuit breaker: after 3 consecutive failures the LLM is skipped for 30 s and
  the deterministic rules engine answers instantly instead of every user
  waiting for a timeout.
* Thinking output (<think>…</think> or the separate `thinking` field) is
  discarded; only the final answer is returned.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

import httpx

log = logging.getLogger("drivelegal.llm")

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

# Preference when OLLAMA_MODEL isn't set: instruction-following models that are
# good at JSON, biggest first. Matched as prefixes against /api/tags names.
OLLAMA_PREFERENCE = [
    "gpt-oss:120b", "qwen3:32b", "qwen2.5:32b", "llama3.3", "gpt-oss:20b", "gpt-oss",
    "qwen3:14b", "qwen2.5:14b", "gemma3:27b", "mistral-small", "qwen3", "qwen2.5",
    "gemma3", "llama3.1", "llama3", "mistral",
]


class LLMOfflineError(Exception):
    """The configured LLM can't answer right now — callers fall back to rules."""


# ── configuration ────────────────────────────────────────────────────────────

def provider() -> str:
    p = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if p in ("ollama", "groq"):
        return p
    return "ollama" if os.environ.get("OLLAMA_URL", "").strip() else "groq"


def ollama_base() -> str:
    url = os.environ.get("OLLAMA_URL", "").strip().rstrip("/")
    # accept the full endpoint the org gave ("…/api/generate") as well as the base
    return re.sub(r"/api/(generate|chat|tags)$", "", url)


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ── circuit breaker + concurrency ────────────────────────────────────────────

_sem: Optional[threading.BoundedSemaphore] = None
_sem_lock = threading.Lock()
_fail = {"count": 0, "open_until": 0.0}
_BREAK_AFTER, _BREAK_FOR = 3, 30.0


def _semaphore() -> threading.BoundedSemaphore:
    global _sem
    with _sem_lock:
        if _sem is None:
            _sem = threading.BoundedSemaphore(max(1, int(_f("OLLAMA_MAX_PARALLEL", 2))))
        return _sem


def _breaker_check() -> None:
    if time.time() < _fail["open_until"]:
        raise LLMOfflineError("LLM temporarily skipped after repeated failures")


def _record(ok: bool) -> None:
    if ok:
        _fail["count"] = 0
        return
    _fail["count"] += 1
    if _fail["count"] >= _BREAK_AFTER:
        _fail["open_until"] = time.time() + _BREAK_FOR
        log.warning("LLM failed %d times in a row — using rules only for %ds", _fail["count"], int(_BREAK_FOR))


# ── model discovery ──────────────────────────────────────────────────────────

_tags_cache: Dict[str, object] = {"at": 0.0, "models": None}


def ollama_models(refresh: bool = False) -> Optional[List[str]]:
    """Model names on the server (cached 60 s). None when unreachable."""
    if not refresh and _tags_cache["models"] is not None and time.time() - _tags_cache["at"] < 60:
        return _tags_cache["models"]           # type: ignore[return-value]
    try:
        r = httpx.get(f"{ollama_base()}/api/tags", timeout=4.0)
        r.raise_for_status()
        names = [m.get("name") or m.get("model") for m in r.json().get("models", [])]
    except Exception as exc:
        log.debug("ollama /api/tags failed: %s", exc)
        _tags_cache.update(at=time.time(), models=None)
        return None
    names = [n for n in names if n and "embed" not in n.lower()]
    _tags_cache.update(at=time.time(), models=names)
    return names


def ollama_model(purpose: str = "chat") -> Optional[str]:
    if purpose == "extract" and os.environ.get("OLLAMA_EXTRACT_MODEL", "").strip():
        return os.environ["OLLAMA_EXTRACT_MODEL"].strip()
    wanted = os.environ.get("OLLAMA_MODEL", "").strip()
    if wanted:
        return wanted
    names = ollama_models() or []
    for pref in OLLAMA_PREFERENCE:
        for n in names:
            if n == pref or n.startswith(pref + ":") or n.startswith(pref):
                return n
    return names[0] if names else None


def _is_thinking_model(model: str) -> bool:
    m = model.lower()
    return any(k in m for k in ("gpt-oss", "qwen3", "deepseek-r1", "magistral", "phi4-reasoning"))


# ── message rendering for /api/generate ──────────────────────────────────────

def _render(messages: List[dict]) -> tuple[str, str]:
    """Split chat messages into Ollama's (system, prompt) for /api/generate."""
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    convo = [m for m in messages if m.get("role") != "system"]
    if len(convo) == 1:
        return system, convo[0]["content"]
    lines = []
    for m in convo[:-1]:
        who = "User" if m.get("role") == "user" else "Assistant"
        lines.append(f"{who}: {m.get('content', '')}")
    lines.append(f"User: {convo[-1].get('content', '')}")
    lines.append("Assistant:")
    return system, "Conversation so far:\n" + "\n".join(lines)


# ── the call ─────────────────────────────────────────────────────────────────

_last_stats: Dict[str, object] = {}


def last_stats() -> Dict[str, object]:
    """Timing of the most recent Ollama call (for benchmarks / logs)."""
    return dict(_last_stats)


def ollama_chat(messages: List[dict], *, max_tokens: int = 600, temperature: float = 0.3,
                json_mode: bool = False, purpose: str = "chat", timeout: Optional[float] = None) -> str:
    _breaker_check()
    base = ollama_base()
    if not base:
        raise LLMOfflineError("OLLAMA_URL not set")
    model = ollama_model(purpose)
    if not model:
        _record(False)
        raise LLMOfflineError("no model available on the Ollama server")
    api = os.environ.get("OLLAMA_API", "generate").strip().lower()
    think_env = os.environ.get("OLLAMA_THINK", "false").strip().lower()
    num_predict = max_tokens + (512 if _is_thinking_model(model) and think_env not in ("false", "0", "no") else 0)
    options = {"temperature": temperature, "num_predict": num_predict,
               "num_ctx": int(_f("OLLAMA_NUM_CTX", 8192))}
    payload: Dict[str, object] = {"model": model, "stream": False, "options": options,
                                  "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m")}
    if api == "chat":
        payload["messages"] = messages
        url = f"{base}/api/chat"
    else:
        system, prompt = _render(messages)
        payload["system"], payload["prompt"] = system, prompt
        url = f"{base}/api/generate"
    if json_mode:
        payload["format"] = "json"
    if _is_thinking_model(model):
        payload["think"] = (think_env if think_env in ("low", "medium", "high")
                            else think_env not in ("false", "0", "no"))

    tmo = timeout or _f("OLLAMA_TIMEOUT", 60.0)
    t0 = time.time()
    sem = _semaphore()
    if not sem.acquire(timeout=tmo):
        raise LLMOfflineError("LLM busy (too many parallel requests)")
    try:
        try:
            r = httpx.post(url, json=payload, timeout=httpx.Timeout(tmo, connect=5.0))
            if r.status_code == 400 and "think" in r.text.lower() and "think" in payload:
                payload.pop("think", None)          # older server / model without thinking control
                r = httpx.post(url, json=payload, timeout=httpx.Timeout(tmo, connect=5.0))
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            _record(False)
            raise LLMOfflineError(f"Ollama unreachable: {exc}") from exc
    finally:
        sem.release()

    if r.status_code == 404:
        _record(False)
        raise LLMOfflineError(f"model '{model}' not found on the Ollama server")
    if r.status_code != 200:
        _record(False)
        raise LLMOfflineError(f"Ollama error {r.status_code}: {r.text[:200]}")
    data = r.json()
    text = (data.get("message") or {}).get("content", "") if api == "chat" else data.get("response", "")
    text = _THINK_RE.sub("", text or "").strip()
    _last_stats.update(model=model, wall_s=round(time.time() - t0, 2),
                       prompt_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
                       load_s=round((data.get("load_duration") or 0) / 1e9, 2),
                       gen_s=round((data.get("eval_duration") or 0) / 1e9, 2))
    if not text:
        _record(False)
        raise LLMOfflineError(f"{model} returned empty content")
    if json_mode:
        try:
            json.loads(re.search(r"\{.*\}|\[.*\]", text, re.S).group(0))
        except Exception:
            log.info("ollama: %s returned invalid JSON (%d chars)", model, len(text))
    _record(True)
    return text


def ollama_ok() -> bool:
    names = ollama_models(refresh=True)
    if not names:
        return False
    m = ollama_model()
    return bool(m) and any(n == m or n.split(":")[0] == m.split(":")[0] for n in names)


def ollama_warmup() -> bool:
    """Load the model into memory at startup so the first user doesn't wait for it."""
    try:
        m = ollama_model()
        if not m:
            log.info("Ollama at %s has no usable model", ollama_base())
            return False
        httpx.post(f"{ollama_base()}/api/generate",
                   json={"model": m, "prompt": "", "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m")},
                   timeout=120.0)
        log.info("Ollama ready at %s (model=%s)", ollama_base(), m)
        return True
    except Exception as exc:
        log.info("Ollama warmup failed: %s", exc)
        return False
