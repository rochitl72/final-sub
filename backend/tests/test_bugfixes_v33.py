#!/usr/bin/env python3
"""
test_bugfixes_v33.py — regression checks for the v3.3 review fixes
=================================================================
Covers:
  C1  fresh-clone first boot must not crash (auth imports before persistence)
  C2  stale/deleted-subject JWTs rejected; valid device tokens accepted
  F4  side-effect breadcrumbs filtered out of rehydrated LLM history

Run it directly (each check is isolated in its own subprocess):

    python3 tests/test_bugfixes_v33.py

It intentionally SKIPS under pytest: the existing suite holds a hardcoded-path
SQLite singleton, so any co-collected test that touches data/chats.db corrupts
the other modules. Running standalone keeps these checks hermetic.
"""

import sys

# Skip when collected by pytest; run normally when executed directly.
if __name__ != "__main__":
    import pytest
    pytest.skip(
        "run directly: python3 tests/test_bugfixes_v33.py",
        allow_module_level=True,
    )

import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _fresh_db():
    for suffix in ("", "-wal", "-shm"):
        p = BACKEND / "data" / f"chats.db{suffix}"
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND), capture_output=True, text=True,
    )


def check_c1_fresh_boot():
    _fresh_db()
    code = (
        "import auth, sqlite3, pathlib;"
        "c=sqlite3.connect(str(pathlib.Path('data')/'chats.db'));"
        "cols={r[1] for r in c.execute('PRAGMA table_info(sessions)').fetchall()};"
        "assert 'user_id' in cols, cols; print('OK')"
    )
    r = _run(code)
    assert r.returncode == 0 and "OK" in r.stdout, f"C1 fresh boot crashed:\n{r.stderr}"
    print("  C1 fresh-boot: OK")


def check_c2_token_validation():
    _fresh_db()
    code = (
        "import auth;"
        "u=auth.upsert_device_user('12345678-1234-4234-8234-1234567890ab');"
        "assert auth.get_current_user('Bearer '+auth._make_jwt(u))['id']==u['id'];"
        "ghost=auth._make_jwt({'id':'nope','email':'x','name':'y'});"
        "assert auth.get_current_user('Bearer '+ghost) is None; print('OK')"
    )
    r = _run(code)
    assert r.returncode == 0 and "OK" in r.stdout, f"C2 failed:\n{r.stderr}"
    print("  C2 token-validation: OK")


def check_f4_breadcrumbs():
    _fresh_db()
    code = (
        "import api;"
        "assert api._is_breadcrumb({'role':'user','content':'\U0001F4CD Pin dropped'});"
        "assert api._is_breadcrumb({'role':'user','content':'chip:vehicle=two_wheeler'});"
        "assert not api._is_breadcrumb({'role':'user','content':'fine for no helmet?'});"
        "assert not api._is_breadcrumb({'role':'assistant','content':'rupees 1000'});"
        "print('OK')"
    )
    r = _run(code)
    assert r.returncode == 0 and "OK" in r.stdout, f"F4 failed:\n{r.stderr}"
    print("  F4 breadcrumb-filter: OK")


if __name__ == "__main__":
    check_c1_fresh_boot()
    check_c2_token_validation()
    check_f4_breadcrumbs()
    _fresh_db()
    print("v3.3 bugfix regressions: ALL OK")
