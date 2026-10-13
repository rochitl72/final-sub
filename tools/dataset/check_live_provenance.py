#!/usr/bin/env python3
"""
check_live_provenance.py — gate on what the app actually SERVES.

The other tools in this folder audit the v5 artifacts. The running service reads
data/drivelegal.db (via legal_db → graph_engine) and the PWA ships
apps/pwa/src/offline/drivelegal_offline.json. This check makes sure the audit
results reached both, and that nobody re-introduces a figure the statute contradicts.

Fails on:
  * a penalty row with no verify_status (the audit was not applied)
  * a verified / corrected row with no source URL (a label we cannot back up)
  * a corrected row that drifted from its statutory figure
  * a central s.177A row stating more than the statutory ₹1,000
  * the offline bundle missing rows or labels the DB has
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from apply_v5_provenance import CORRECTIONS  # noqa: E402

DB = ROOT / "data" / "drivelegal.db"
BUNDLE = ROOT / "apps" / "pwa" / "src" / "offline" / "drivelegal_offline.json"


def main() -> int:
    fail: list[str] = []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT p.*, s.url AS url, v.mv_section AS section
                          FROM penalty p LEFT JOIN source s ON s.id = p.source_id
                          JOIN violation v ON v.code = p.violation_code""").fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        st = r["verify_status"]
        counts[st or "MISSING"] = counts.get(st or "MISSING", 0) + 1
        if not st:
            fail.append(f"[unlabelled] penalty {r['id']} ({r['violation_code']}) has no verify_status")
        if st in ("verified", "corrected") and not r["url"]:
            fail.append(f"[no-source] penalty {r['id']} is '{st}' but has no source URL")
        if r["id"] in CORRECTIONS:
            want = CORRECTIONS[r["id"]]
            for k in ("first_offence", "repeat_offence", "imprisonment"):
                if k in want and r[k] != want[k]:
                    fail.append(f"[drift] penalty {r['id']} {k}={r[k]!r}, statute says {want[k]!r}")
        if r["scope"] == "central" and (r["section"] or "").replace(" ", "").upper() in ("177A", "S.177A") \
                and any((r[k] or 0) > 1000 for k in ("first_offence", "repeat_offence")):
            fail.append(f"[statute] penalty {r['id']} ({r['violation_code']}) exceeds the s.177A maximum of ₹1,000")

    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    fines = bundle.get("fines", {})
    if len(fines) != len(rows):
        fail.append(f"[bundle] offline bundle has {len(fines)} fines, DB has {len(rows)} — run scripts/build_offline_bundle.py")
    unlabelled = [k for k, v in fines.items() if not v.get("verify_status")]
    if unlabelled:
        fail.append(f"[bundle] {len(unlabelled)} offline fines carry no verify_status, e.g. {unlabelled[:3]}")

    print("Live provenance gate")
    print("=" * 52)
    print("  penalties:", counts)
    for f in fail:
        print("  FAIL", f)
    if not fail:
        print(f"  PASS  {len(rows)} served amounts labelled; {counts.get('verified', 0)} verified, "
              f"{counts.get('corrected', 0)} corrected to statute; offline bundle in step.")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
