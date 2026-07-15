#!/usr/bin/env python3
"""
auth.py — Device-ID + JWT authentication for DriveLegal
========================================================
Provides:
  POST /auth/device   → register or refresh session for a stable device UUID
  GET  /auth/me       → current user (Bearer JWT)
  POST /auth/logout   → stateless — client discards JWT

Mobile flow:
  1. Generate/persist device_id in SecureStore (UUID v4).
  2. POST /auth/device { "device_id": "..." } → JWT + user profile.
  3. Attach Authorization: Bearer <token> on API calls.

Sessions linked via user_id scope chat history per device.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, Field

log = logging.getLogger("drivelegal.auth")

# ── Config ────────────────────────────────────────────────────────────────────

# C2 fix: never silently ship the default dev secret to production. In a prod
# environment a hardcoded secret means anyone can forge a 30-day JWT for any
# user_id and read another device's chat history.
_DEFAULT_SECRET = "drivelegal-dev-secret-change-me-in-production"
SESSION_SECRET  = os.getenv("SESSION_SECRET", _DEFAULT_SECRET)

if SESSION_SECRET == _DEFAULT_SECRET:
    if os.getenv("DRIVELEGAL_ENV", "dev").lower() in ("prod", "production"):
        raise RuntimeError(
            "SESSION_SECRET must be set in production — refusing to start with "
            "the built-in dev secret (JWTs would be forgeable)."
        )
    log.warning(
        "Using the built-in dev SESSION_SECRET — set the SESSION_SECRET env var "
        "before deploying (current JWTs are forgeable)."
    )

JWT_ALGORITHM  = "HS256"
JWT_EXPIRY_SEC = 30 * 24 * 3600   # 30 days

_DEVICE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# ── DB setup (piggybacks on the same chats.db) ────────────────────────────────

_HERE   = Path(__file__).parent
_DB_DIR = _HERE / "data"
_DB_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = _DB_DIR / "chats.db"

_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    email        TEXT UNIQUE NOT NULL,
    name         TEXT,
    picture      TEXT,
    google_sub   TEXT UNIQUE,
    device_id    TEXT UNIQUE,
    created_at   INTEGER,
    updated_at   INTEGER
);
"""

_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_sessions_table(conn: sqlite3.Connection) -> None:
    """C1 fix — guarantee the `sessions` table exists before we migrate it.

    `persistence.ChatStore` owns the sessions/messages schema, but it only
    creates the tables lazily (first `get_chat_store()` call at the FastAPI
    startup event). `auth._init_db()` runs at *import* time, which is earlier.
    On a fresh clone (data/ is gitignored) that ordering meant the
    `ALTER TABLE sessions` below hit a non-existent table and crashed the whole
    app with `OperationalError: no such table: sessions`.

    We now proactively initialise the persistence store (preferred, keeps a
    single schema definition) and fall back to a minimal CREATE so the ALTER is
    always safe regardless of import ordering.
    """
    try:
        from persistence import get_chat_store
        get_chat_store()            # creates sessions/messages tables if missing
    except Exception as exc:        # pragma: no cover - defensive
        log.warning("persistence init skipped (%s) — creating minimal schema", exc)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "id TEXT PRIMARY KEY, title TEXT, mode TEXT, "
            "created_at INTEGER, updated_at INTEGER, summary_json TEXT)"
        )


def _init_db() -> None:
    conn = _get_conn()
    conn.executescript(_USERS_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "device_id" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN device_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_device_id "
            "ON users(device_id) WHERE device_id IS NOT NULL"
        )
    _ensure_sessions_table(conn)
    sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "user_id" not in sess_cols:
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError as exc:
            # Idempotent: another connection may have added it concurrently.
            if "duplicate column" not in str(exc).lower():
                raise
    conn.close()


_init_db()


# ── User store helpers ────────────────────────────────────────────────────────


def _guest_profile(device_id: str) -> tuple[str, str, str]:
    short = device_id.replace("-", "")[:6].upper()
    return (
        f"guest-{device_id.lower()}@device.local",
        f"Guest {short}",
        "",
    )


def upsert_device_user(device_id: str) -> dict:
    """Find or create the user row for this device_id."""
    conn = _get_conn()
    now = int(time.time() * 1000)
    email, name, picture = _guest_profile(device_id)

    with _db_lock:
        row = conn.execute(
            "SELECT id, email, name, picture FROM users WHERE device_id = ?",
            (device_id,),
        ).fetchone()

        if row:
            conn.execute(
                "UPDATE users SET name=?, updated_at=? WHERE id=?",
                (name, now, row["id"]),
            )
            user_id = row["id"]
            email = row["email"]
            name = row["name"] or name
            picture = row["picture"] or ""
        else:
            user_id = _uuid.uuid4().hex
            conn.execute(
                "INSERT INTO users "
                "(id, email, name, picture, google_sub, device_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (user_id, email, name, picture, device_id, now, now),
            )

    conn.close()
    return {"id": user_id, "email": email, "name": name, "picture": picture}


def get_user_by_id(user_id: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, email, name, picture FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def link_session_to_user(session_id: str, user_id: str) -> None:
    conn = _get_conn()
    with _db_lock:
        conn.execute(
            "UPDATE sessions SET user_id = ? WHERE id = ?",
            (user_id, session_id),
        )
    conn.close()


def _rows_to_session_list(rows) -> list:
    out = []
    for r in rows:
        snip = (r["last_snippet"] or "").strip()
        if len(snip) > 80:
            snip = snip[:77].rstrip() + "…"   # D1: match persistence.list_sessions truncation
        out.append({
            "id":           r["id"],
            "title":        r["title"] or "New chat",
            "mode":         r["mode"] or "static",
            "created_at":   r["created_at"],
            "updated_at":   r["updated_at"],
            "last_snippet": snip,
        })
    return out


_SESSION_LIST_SQL = (
    "SELECT s.id, s.title, s.mode, s.created_at, s.updated_at, "
    "       (SELECT content FROM messages WHERE session_id=s.id "
    "        ORDER BY id DESC LIMIT 1) AS last_snippet "
    "FROM sessions s "
)


def get_user_sessions(user_id: str) -> list:
    """Return session rows for a specific user (latest first)."""
    conn = _get_conn()
    rows = conn.execute(
        _SESSION_LIST_SQL + "WHERE s.user_id = ? ORDER BY s.updated_at DESC LIMIT 200",
        (user_id,),
    ).fetchall()
    conn.close()
    return _rows_to_session_list(rows)


def get_anonymous_sessions() -> list:
    """Sessions not linked to any device user (legacy / no Bearer token)."""
    conn = _get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "user_id" not in cols:
        conn.close()
        return []
    rows = conn.execute(
        _SESSION_LIST_SQL
        + "WHERE s.user_id IS NULL ORDER BY s.updated_at DESC LIMIT 200",
    ).fetchall()
    conn.close()
    return _rows_to_session_list(rows)


# ── JWT helpers ───────────────────────────────────────────────────────────────


def _make_jwt(user: dict) -> str:
    payload = {
        "sub":     user["id"],
        "email":   user["email"],
        "name":    user["name"],
        "picture": user.get("picture", ""),
        "exp":     int(time.time()) + JWT_EXPIRY_SEC,
        "iat":     int(time.time()),
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SESSION_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


def get_current_user(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    """Extract and validate the Bearer JWT. Returns None for anonymous requests."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:]
    payload = decode_jwt(token)
    if not payload:
        return None
    # C2 fix: a validly-signed token whose subject no longer exists (deleted
    # user / stale 30-day token) must not authorize. Re-validate against the DB.
    sub = payload.get("sub")
    if not sub or get_user_by_id(sub) is None:
        return None
    return {
        "id":      sub,
        "email":   payload.get("email"),
        "name":    payload.get("name"),
        "picture": payload.get("picture"),
    }


def require_user(user: Optional[dict] = Depends(get_current_user)) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


# ── API router ────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth", tags=["auth"])


class DeviceAuthBody(BaseModel):
    device_id: str = Field(..., min_length=36, max_length=36)


@router.post("/device")
async def auth_device(body: DeviceAuthBody):
    """Register or refresh JWT for a stable mobile device identifier."""
    device_id = body.device_id.strip().lower()
    if not _DEVICE_ID_RE.match(device_id):
        raise HTTPException(
            status_code=400,
            detail="device_id must be a valid UUID v4 string",
        )

    user = upsert_device_user(device_id)
    token = _make_jwt(user)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   JWT_EXPIRY_SEC,
        "user":         user,
    }


@router.get("/me")
async def auth_me(user: dict = Depends(require_user)):
    db_user = get_user_by_id(user["id"])
    return db_user or user


@router.post("/logout")
async def auth_logout():
    return {"ok": True, "message": "Token should be discarded client-side"}
