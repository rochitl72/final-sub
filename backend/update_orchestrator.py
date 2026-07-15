#!/usr/bin/env python3
"""
update_orchestrator.py — Scrape → Groq extract → Patch store
=============================================================
Coordinates the full update cycle:
  1. Fetch each gov.in source (skip if unchanged via SHA-256)
  2. Send changed text to Groq (llama-3.1-8b-instant) for structured extraction
  3. Validate & store patches — ADDITIVE ONLY, base graph never touched

Groq budget management (free tier limits):
  Model  : llama-3.1-8b-instant
  RPM    : 30  → we use 1 call per 3.5s  (≈17 RPM, safe headroom)
  TPM    : 6,000 → we cap each call at ≤2,000 tokens in + ≤600 out
  RPD    : 14,400 → one full cycle ≈ 28 calls, well within daily budget
  TPD    : 500,000 → one cycle ≈ 28 × 2,600 ≈ 72,800 tokens — fine
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import threading
from typing import Dict, List, Optional

import httpx

from gov_scraper   import GOV_SOURCES, fetch_page
from patch_engine  import (
    get_last_run,
    log_update_run,
    save_patch,
)

log = logging.getLogger("drivelegal.updater")

# ── Groq config ───────────────────────────────────────────────────────────────

GROQ_MODEL     = "llama-3.1-8b-instant"
GROQ_ENDPOINT  = "https://api.groq.com/openai/v1/chat/completions"


def _groq_key() -> str:
    return os.environ.get("GROQ_API_KEY", "")
GROQ_RPM_DELAY = 3.5          # seconds between calls (≈17 RPM, 30 RPM cap)
MIN_CONFIDENCE = 0.75         # discard low-confidence extractions
MAX_GROQ_CALLS = 60           # hard cap per run to stay within daily budget

# Tracks whether an update is currently running (prevent concurrent runs)
_update_lock  = threading.Lock()
_update_state = {"running": False, "last_summary": None}

# ── Known violation codes (hint for the LLM) ─────────────────────────────────

_KNOWN_CODES = (
    "SAFETY_NO_HELMET_RIDER, SAFETY_NO_HELMET_PILLION, SAFETY_NO_SEATBELT, "
    "SPEED_GENERAL, SPEED_SCHOOL_ZONE, SPEED_EXPRESSWAY, "
    "DRUNK_DRIVING, DRUNK_DRIVING_REPEAT, MOBILE_HANDHELD, "
    "SIGNAL_JUMPING, WRONG_SIDE, PARKING_NO_PARKING_ZONE, "
    "OVERLOADING_GOODS, POLLUTION_PUC_EXPIRED, LICENCE_NO_LICENCE, "
    "INSURANCE_NO_INSURANCE, INSURANCE_EXPIRED, REGISTRATION_EXPIRED, "
    "LANE_DISCIPLINE, TRIPLE_RIDING, TINTED_GLASS"
)

# ── System prompt (kept short to save tokens) ─────────────────────────────────

_SYSTEM = (
    "You are a structured-data extractor for Indian traffic law changes. "
    "Extract ONLY factual fine or penalty changes from official government text. "
    "Return a JSON array — nothing else, no prose. "
    "Each object must have exactly these fields:\n"
    "  violation_code   : string (pick closest from known codes or invent a clear snake_case code)\n"
    "  violation_name   : string\n"
    "  old_fine_first   : integer|null  (previous first-offence fine in ₹)\n"
    "  new_fine_first   : integer|null  (new first-offence fine in ₹)\n"
    "  old_fine_repeat  : integer|null\n"
    "  new_fine_repeat  : integer|null\n"
    "  effective_date   : string YYYY-MM-DD|null\n"
    "  rule_summary     : string (≤20 words describing the change)\n"
    "  confidence       : float 0.0-1.0\n\n"
    "Rules:\n"
    "- Only include items where you found a specific ₹ amount.\n"
    "- Do NOT invent data — only extract what the text explicitly states.\n"
    "- Return [] if no relevant changes found.\n"
    f"Known violation codes: {_KNOWN_CODES}"
)


# ── Groq call ─────────────────────────────────────────────────────────────────

def _call_groq(
    text:        str,
    state_code:  Optional[str],
    source_label: str,
) -> List[dict]:
    """Send extracted page text to Groq. Returns list of structured patch dicts."""
    key = _groq_key()
    if not key:
        log.warning("GROQ_API_KEY not set — skipping Groq extraction (add to .env)")
        return []

    scope = f"State: {state_code}" if state_code else "Central / MV Act (all-India)"
    user_msg = (
        f"Source: {source_label}\n"
        f"Scope: {scope}\n\n"
        f"--- GOV WEBSITE TEXT ---\n"
        f"{text[:1900]}\n"
        f"--- END ---\n\n"
        "Extract fine changes as a JSON array:"
    )

    try:
        resp = httpx.post(
            GROQ_ENDPOINT,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       GROQ_MODEL,
                "messages":    [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                "temperature": 0.05,   # near-deterministic extraction
                "max_tokens":  600,
            },
            timeout=25.0,
        )

        if resp.status_code == 429:
            log.warning("Groq rate-limited — backing off 65s")
            time.sleep(65)
            return []

        if resp.status_code != 200:
            log.warning("Groq %d: %s", resp.status_code, resp.text[:200])
            return []

        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Extract the first JSON array from the response
        m = re.search(r"\[.*?\]", content, re.DOTALL)
        if not m:
            return []

        items = json.loads(m.group(0))
        return items if isinstance(items, list) else []

    except json.JSONDecodeError:
        log.warning("Groq returned invalid JSON for %s", source_label)
        return []
    except Exception as exc:
        log.warning("Groq call failed for %s: %s", source_label, exc)
        return []


# ── Main update cycle ─────────────────────────────────────────────────────────

def run_update() -> Dict:
    """
    Full update cycle. Blocks the calling thread (run in a background thread
    from the API route so the HTTP response returns immediately).

    Returns a summary dict.
    """
    pages_checked = 0
    pages_changed = 0
    patches_added = 0
    groq_calls    = 0
    errors: List[str] = []

    log.info("=== Update cycle start — %d sources ===", len(GOV_SOURCES))

    for source in GOV_SOURCES:
        if groq_calls >= MAX_GROQ_CALLS:
            log.info("Groq call cap (%d) reached — stopping early", MAX_GROQ_CALLS)
            break

        pages_checked += 1
        result = fetch_page(source)

        if result is None:
            errors.append(source["url"])
            continue

        url, text, changed = result

        if not changed or not text.strip():
            continue

        pages_changed += 1

        # Rate limit before each Groq call
        time.sleep(GROQ_RPM_DELAY)
        items = _call_groq(text, source.get("state_code"), source["label"])
        groq_calls += 1

        for item in items:
            confidence    = float(item.get("confidence", 0.0))
            violation_code = str(item.get("violation_code", "")).strip()
            new_fine_first = item.get("new_fine_first")

            # Filter: must be high-confidence AND have a concrete fine amount
            if confidence < MIN_CONFIDENCE:
                continue
            if not violation_code or new_fine_first is None:
                continue

            try:
                new_fine_first = int(new_fine_first)
            except (TypeError, ValueError):
                continue

            inserted = save_patch(
                state_code        = source.get("state_code"),
                violation_code    = violation_code,
                patch_type        = "fine_update",
                old_fine_first    = _safe_int(item.get("old_fine_first")),
                old_fine_repeat   = _safe_int(item.get("old_fine_repeat")),
                new_fine_first    = new_fine_first,
                new_fine_repeat   = _safe_int(item.get("new_fine_repeat")),
                effective_date    = _clean_date(item.get("effective_date")),
                rule_summary      = str(item.get("rule_summary", ""))[:300],
                source_url        = url,
                source_domain     = source["domain"],
                groq_confidence   = confidence,
            )
            if inserted:
                patches_added += 1
                log.info(
                    "New patch: %s [%s] ₹%d (conf=%.2f)",
                    violation_code,
                    source.get("state_code") or "central",
                    new_fine_first,
                    confidence,
                )

    status = "ok" if not errors else ("partial" if patches_added else "error")
    log_update_run(
        pages_checked = pages_checked,
        pages_changed = pages_changed,
        patches_added = patches_added,
        groq_calls    = groq_calls,
        status        = status,
        error         = "; ".join(errors[:5]) if errors else None,
    )

    summary = {
        "pages_checked": pages_checked,
        "pages_changed": pages_changed,
        "patches_added": patches_added,
        "groq_calls":    groq_calls,
        "status":        status,
    }
    log.info("=== Update cycle complete: %s ===", summary)
    return summary


def start_update_background() -> bool:
    """
    Start an update cycle in a daemon thread.
    Returns False if one is already running.
    """
    with _update_lock:
        if _update_state["running"]:
            return False
        _update_state["running"] = True

    def _run():
        try:
            summary = run_update()
            _update_state["last_summary"] = summary
        except Exception as exc:
            log.error("Update cycle crashed: %s", exc)
        finally:
            _update_state["running"] = False

    threading.Thread(target=_run, name="drivelegal-updater", daemon=True).start()
    return True


def is_running() -> bool:
    return bool(_update_state.get("running"))


def last_summary() -> Optional[Dict]:
    return _update_state.get("last_summary")


def should_run_update(min_interval_days: float = 7.0) -> bool:
    """True if no update has been run yet, or it's been ≥ min_interval_days."""
    last = get_last_run()
    if not last:
        return True
    elapsed_days = (time.time() * 1000 - last["run_at"]) / 86_400_000
    return elapsed_days >= min_interval_days


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _clean_date(v) -> Optional[str]:
    """Normalise date strings to YYYY-MM-DD, return None if unparseable."""
    if not v:
        return None
    s = str(v).strip()
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # Try DD-MM-YYYY or DD/MM/YYYY
    m = re.match(r"(\d{2})[/-](\d{2})[/-](\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None
