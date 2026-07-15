#!/usr/bin/env python3
"""
patch_engine.py — Additive law-update patch storage
=====================================================
Stores incremental patches scraped from official .gov.in sources.
Patches NEVER modify the base graph — they layer on top of it.

Tables added to chats.db:
  data_patches  — fine/rule changes with timeline info
  scrape_cache  — SHA-256 hashes for change detection (skip unchanged pages)
  update_log    — audit log of every update run
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

_HERE    = Path(__file__).parent
_DB_DIR  = _HERE / "data"
_DB_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = _DB_DIR / "chats.db"

_lock = threading.Lock()

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_patches (
    id               TEXT PRIMARY KEY,
    state_code       TEXT,          -- NULL = central / MV Act (all states)
    violation_code   TEXT NOT NULL,
    vehicle_class    TEXT,          -- NULL = applies to all vehicles
    patch_type       TEXT NOT NULL DEFAULT 'fine_update',
    old_fine_first   INTEGER,
    old_fine_repeat  INTEGER,
    new_fine_first   INTEGER,
    new_fine_repeat  INTEGER,
    imprisonment_days INTEGER,
    effective_date   TEXT,          -- ISO-8601 date when rule took effect
    rule_summary     TEXT,          -- 1-sentence human-readable description
    source_url       TEXT,
    source_domain    TEXT,
    groq_confidence  REAL DEFAULT 0.0,
    scraped_at       INTEGER,       -- unix milliseconds
    patch_hash       TEXT UNIQUE    -- dedup key
);

CREATE TABLE IF NOT EXISTS scrape_cache (
    url          TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    last_scraped INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS update_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         INTEGER NOT NULL,
    pages_checked  INTEGER DEFAULT 0,
    pages_changed  INTEGER DEFAULT 0,
    patches_added  INTEGER DEFAULT 0,
    groq_calls     INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'ok',
    error          TEXT
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_patch_tables() -> None:
    with _lock:
        c = _conn()
        c.executescript(_SCHEMA)
        c.close()


# Initialise on import
init_patch_tables()


# ── Patch CRUD ────────────────────────────────────────────────────────────────

def _patch_hash(source_url: str, effective_date: str, new_fine_first: Optional[int]) -> str:
    raw = f"{source_url}|{effective_date}|{new_fine_first}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def save_patch(
    *,
    state_code:        Optional[str],
    violation_code:    str,
    vehicle_class:     Optional[str]  = None,
    patch_type:        str            = "fine_update",
    old_fine_first:    Optional[int]  = None,
    old_fine_repeat:   Optional[int]  = None,
    new_fine_first:    Optional[int]  = None,
    new_fine_repeat:   Optional[int]  = None,
    imprisonment_days: Optional[int]  = None,
    effective_date:    Optional[str]  = None,
    rule_summary:      Optional[str]  = None,
    source_url:        str            = "",
    source_domain:     str            = "",
    groq_confidence:   float          = 0.0,
) -> bool:
    """Insert a patch. Returns True if newly inserted, False if duplicate (idempotent)."""
    phash = _patch_hash(source_url, effective_date or "", new_fine_first)
    pid   = uuid.uuid4().hex
    now   = int(time.time() * 1000)
    with _lock:
        c = _conn()
        try:
            c.execute("""
                INSERT OR IGNORE INTO data_patches
                  (id, state_code, violation_code, vehicle_class, patch_type,
                   old_fine_first, old_fine_repeat, new_fine_first, new_fine_repeat,
                   imprisonment_days, effective_date, rule_summary,
                   source_url, source_domain, groq_confidence, scraped_at, patch_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (pid, state_code, violation_code, vehicle_class, patch_type,
                  old_fine_first, old_fine_repeat, new_fine_first, new_fine_repeat,
                  imprisonment_days, effective_date, rule_summary,
                  source_url, source_domain, groq_confidence, now, phash))
            inserted = c.total_changes > 0
            c.commit()
            return inserted
        finally:
            c.close()


def get_patches(
    violation_code:  str,
    state_code:      Optional[str] = None,
    confidence_min:  float         = 0.75,
) -> List[dict]:
    """Return all patches for a violation+state, most recent first.
    Includes both state-specific patches AND central (NULL state_code) patches."""
    c = _conn()
    try:
        rows = c.execute("""
            SELECT * FROM data_patches
            WHERE violation_code = ?
              AND (state_code = ? OR state_code IS NULL)
              AND groq_confidence >= ?
            ORDER BY effective_date DESC NULLS LAST, scraped_at DESC
        """, (violation_code, state_code, confidence_min)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_all_patches(limit: int = 1000) -> List[dict]:
    """Return all high-confidence patches for mobile sync."""
    c = _conn()
    try:
        rows = c.execute("""
            SELECT * FROM data_patches
            WHERE groq_confidence >= 0.75
            ORDER BY scraped_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ── Scrape cache ──────────────────────────────────────────────────────────────

def get_page_hash(url: str) -> Optional[str]:
    c = _conn()
    try:
        row = c.execute(
            "SELECT content_hash FROM scrape_cache WHERE url = ?", (url,)
        ).fetchone()
        return row["content_hash"] if row else None
    finally:
        c.close()


def set_page_hash(url: str, content_hash: str) -> None:
    now = int(time.time() * 1000)
    with _lock:
        c = _conn()
        try:
            c.execute("""
                INSERT OR REPLACE INTO scrape_cache (url, content_hash, last_scraped)
                VALUES (?, ?, ?)
            """, (url, content_hash, now))
            c.commit()
        finally:
            c.close()


# ── Update log ────────────────────────────────────────────────────────────────

def log_update_run(
    pages_checked: int,
    pages_changed: int,
    patches_added: int,
    groq_calls:    int,
    status:        str           = "ok",
    error:         Optional[str] = None,
) -> None:
    now = int(time.time() * 1000)
    with _lock:
        c = _conn()
        try:
            c.execute("""
                INSERT INTO update_log
                  (run_at, pages_checked, pages_changed, patches_added, groq_calls, status, error)
                VALUES (?,?,?,?,?,?,?)
            """, (now, pages_checked, pages_changed, patches_added, groq_calls, status, error))
            c.commit()
        finally:
            c.close()


def get_last_run() -> Optional[dict]:
    c = _conn()
    try:
        row = c.execute(
            "SELECT * FROM update_log ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def get_update_status() -> dict:
    """Summary for the /api/update/status endpoint."""
    last = get_last_run()
    c    = _conn()
    try:
        patch_count = c.execute(
            "SELECT COUNT(*) AS n FROM data_patches WHERE groq_confidence >= 0.75"
        ).fetchone()["n"]
    finally:
        c.close()

    if not last:
        return {
            "last_run_at":    None,
            "days_since_run": None,
            "patch_count":    patch_count,
            "last_status":    None,
            "pages_checked":  0,
            "patches_added":  0,
        }

    elapsed_ms   = time.time() * 1000 - last["run_at"]
    days_elapsed = elapsed_ms / 86_400_000
    return {
        "last_run_at":    last["run_at"],
        "days_since_run": round(days_elapsed, 1),
        "patch_count":    patch_count,
        "last_status":    last["status"],
        "pages_checked":  last["pages_checked"],
        "patches_added":  last["patches_added"],
    }
