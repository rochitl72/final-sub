#!/usr/bin/env python3
"""
persistence.py — SQLite-backed chat history + session metadata
==============================================================
Stores DriveLegal sessions + per-turn messages on disk so the sidebar
("past chats") survives server restarts.

Schema
------
    sessions (
        id            TEXT PRIMARY KEY,
        title         TEXT,
        mode          TEXT,              -- 'static' | 'dynamic'
        created_at    INTEGER,           -- ms epoch
        updated_at    INTEGER,           -- ms epoch
        summary_json  TEXT               -- last session_state snapshot
    )

    messages (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id    TEXT,
        role          TEXT,              -- 'user' | 'assistant' | 'system'
        content       TEXT,
        payload_json  TEXT,              -- fine_card / chips / chip_id, etc.
        created_at    INTEGER
    )

The store is intentionally **dumb** — it has no opinion on what a
"valid" session_state looks like. The dialog_manager owns shape; this
module only persists it.

CLI self-test:

    python3 -m drivelegal.backend.persistence selftest
or
    python3 backend/persistence.py selftest
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

_HERE   = Path(__file__).parent
_DB_DIR = _HERE / "data"
_DB_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = _DB_DIR / "chats.db"


# ─────────────────────────────────────────────────────────────────────────────
# Connection helpers
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    title         TEXT,
    mode          TEXT,
    created_at    INTEGER,
    updated_at    INTEGER,
    summary_json  TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT,
    payload_json  TEXT,
    created_at    INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, id);

CREATE INDEX IF NOT EXISTS idx_sessions_updated
    ON sessions(updated_at DESC);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# ChatStore — thin wrapper. All methods are thread-safe (single lock).
# ─────────────────────────────────────────────────────────────────────────────


class ChatStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = db_path or _DB_PATH
        self._lock = threading.RLock()
        self._conn = _connect(self._path)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # ── Sessions ─────────────────────────────────────────────────────────────

    def create_session(
        self,
        session_id: str,
        mode: str = "static",
        title: Optional[str] = None,
        summary: Optional[dict] = None,
    ) -> dict:
        now = _now_ms()
        title = title or "New chat"
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions (id, title, mode, created_at, updated_at, summary_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, title, mode, now, now,
                 json.dumps(summary or {}, ensure_ascii=False)),
            )
        return {
            "id":         session_id,
            "title":      title,
            "mode":       mode,
            "created_at": now,
            "updated_at": now,
        }

    def ensure_session(self, session_id: str, mode: str = "static") -> None:
        """Create a row if one doesn't already exist (used when an
        in-memory session predates persistence)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row:
                self.create_session(session_id, mode=mode)

    def update_session_meta(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        mode: Optional[str] = None,
        summary: Optional[dict] = None,
    ) -> None:
        now = _now_ms()
        sets: List[str] = ["updated_at = ?"]
        args: List[object] = [now]
        if title is not None:
            sets.append("title = ?")
            args.append(title)
        if mode is not None:
            sets.append("mode = ?")
            args.append(mode)
        if summary is not None:
            sets.append("summary_json = ?")
            args.append(json.dumps(summary, ensure_ascii=False))
        args.append(session_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", args
            )

    def touch(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_now_ms(), session_id),
            )

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, mode, created_at, updated_at, summary_json "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return _row_to_session(row)

    def list_sessions(self, limit: int = 200) -> List[dict]:
        """Sidebar payload — each row also carries `last_snippet` (the most
        recent assistant or user content, trimmed to 80 chars)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.id, s.title, s.mode, s.created_at, s.updated_at, s.summary_json, "
                "       (SELECT content FROM messages "
                "        WHERE session_id = s.id "
                "        ORDER BY id DESC LIMIT 1) AS last_snippet "
                "FROM sessions s "
                "ORDER BY s.updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out: List[dict] = []
        for r in rows:
            base = _row_to_session(r)
            snip = (r["last_snippet"] or "").strip()
            if len(snip) > 80:
                snip = snip[:77].rstrip() + "…"
            base["last_snippet"] = snip
            out.append(base)
        return out

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ── Messages ─────────────────────────────────────────────────────────────

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        payload: Optional[dict] = None,
    ) -> int:
        now = _now_ms()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content or "",
                 json.dumps(payload, ensure_ascii=False) if payload else None, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            return cur.lastrowid

    def get_messages(self, session_id: str) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, payload_json, created_at "
                "FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [_row_to_message(r) for r in rows]

    # ── Bulk migration helper (for old localStorage snapshots) ───────────────

    def import_legacy_snapshot(self, snapshot: dict) -> Optional[str]:
        """Imports an old `drivelegal_v1` blob (`{session_id, messages, ...}`)
        into SQLite. Returns the migrated session_id, or None when input is
        malformed."""
        sid = snapshot.get("session_id")
        if not sid:
            return None
        if self.get_session(sid):
            return sid  # already imported
        summary = snapshot.get("session_state") or {}
        mode = "static"
        title = _derive_title(summary) or "Imported chat"
        self.create_session(sid, mode=mode, title=title, summary=summary)
        for m in snapshot.get("messages") or []:
            role = "user" if m.get("type") == "user" else "assistant"
            content = m.get("text") or ""
            payload = {k: v for k, v in m.items() if k not in ("type", "text")}
            self.append_message(sid, role=role, content=content,
                                payload=payload if payload else None)
        return sid

    def is_empty(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return (row[0] or 0) == 0

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _row_to_session(r: sqlite3.Row) -> dict:
    try:
        summary = json.loads(r["summary_json"]) if r["summary_json"] else {}
    except Exception:
        summary = {}
    return {
        "id":         r["id"],
        "title":      r["title"],
        "mode":       r["mode"] or "static",
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "summary":    summary,
    }


def _row_to_message(r: sqlite3.Row) -> dict:
    try:
        payload = json.loads(r["payload_json"]) if r["payload_json"] else None
    except Exception:
        payload = None
    return {
        "id":         r["id"],
        "role":       r["role"],
        "content":    r["content"] or "",
        "payload":    payload,
        "created_at": r["created_at"],
    }


def _derive_title(summary: dict) -> Optional[str]:
    """Build a sidebar title from a session_state summary.

    `"{violation_name} — {city_name or state_code}"` per the plan,
    falling back to the violation code or the location if needed.
    """
    if not isinstance(summary, dict):
        return None
    fine_card = summary.get("last_fine_card") or {}
    vname = (
        fine_card.get("violation_name")
        or summary.get("violation_name")
        or summary.get("violation_code")
    )
    loc = summary.get("city_name") or summary.get("state_code")
    if vname and loc:
        return f"{vname} — {loc}"
    if vname:
        return vname
    if loc:
        return f"Chat — {loc}"
    return None


def derive_title(summary: dict) -> Optional[str]:
    """Public wrapper around `_derive_title` for the dialog manager."""
    return _derive_title(summary)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_singleton: Optional[ChatStore] = None
_singleton_lock = threading.Lock()


def get_chat_store() -> ChatStore:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ChatStore()
    return _singleton


# ─────────────────────────────────────────────────────────────────────────────
# CLI self-test
# ─────────────────────────────────────────────────────────────────────────────


def _selftest() -> None:
    """Round-trip the schema in a temp DB; raises on any mismatch."""
    import tempfile
    import uuid

    tmp = Path(tempfile.mkdtemp()) / "selftest.db"
    store = ChatStore(db_path=tmp)
    sid = uuid.uuid4().hex
    store.create_session(sid, mode="static", title="Selftest")
    store.append_message(sid, "user", "I had no helmet on")
    store.append_message(sid, "assistant", "₹1,000 fine under §129")
    store.append_message(sid, "assistant", "fine card",
                         payload={"violation_code": "SAFETY_NO_HELMET_RIDER",
                                  "fine_first": 1000})
    sessions = store.list_sessions()
    assert len(sessions) == 1, "expected exactly one session"
    history = store.get_messages(sid)
    assert len(history) == 3, f"expected 3 messages, got {len(history)}"
    assert history[2]["payload"]["fine_first"] == 1000
    store.delete_session(sid)
    assert store.get_session(sid) is None
    print(f"persistence.selftest OK ({tmp})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
    else:
        print("usage: python3 persistence.py selftest", file=sys.stderr)
        sys.exit(2)
