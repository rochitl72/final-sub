#!/usr/bin/env python3
"""
apply_v5_provenance.py — bring the Sept-2026 dataset audit into data/drivelegal.db
================================================================================
The audit (docs/DATASET_VALIDATION_REPORT.md) checked every claim in the old graph
against retrieved primary law and wrote the result into the v5 graph
(data/compiled/drivelegal_graph.v5.serving.json, field `_verification`).
Penalty ids in the DB are the same as the v5 `fine:<id>` nodes, so the results
map row-for-row.

Policy ("cite + label"):
  verified     amount matched the statute; the row links to the source the audit used
  corrected    the statute contradicted the amount; it is replaced with the statutory
               figure below, and the note quotes the provision (evidence URL kept)
  unverified   no source could be found (all state / city amounts — no state
               notification was retrievable); the amount is kept but labelled

Nothing is deleted. Idempotent: re-running gives the same DB.

    python3 scripts/apply_v5_provenance.py && python3 scripts/export_graph.py \
        && python3 scripts/build_offline_bundle.py
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import urlparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "drivelegal.db"
V5 = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"

S177A = ("s.177A: fine of ₹500, which may extend to ₹1,000, for any offence against the road "
         "regulations — the Act sets no higher amount for a repeat offence.")

# Statutory corrections, keyed by penalty id. Each one is taken from the audit's
# evidence for that row (the provision text it retrieved); see the report §3.
# `None` clears a value the statute does not provide.
CORRECTIONS: dict[int, dict] = {
    16:  {"first_offence": 200,
          "note": "s.194A: ₹200 for each passenger carried in excess of the permitted number.",
          "why": "Graph said a flat ₹1,000; s.194A fixes ₹200 per excess passenger."},
    21:  {"first_offence": 500,
          "note": "s.201: ₹500 for leaving a vehicle so that it obstructs the free flow of traffic.",
          "why": "Graph said ₹50 (the pre-2019 amount); s.201 now fixes ₹500."},
    281: {"repeat_offence": None, "imprisonment": None,
          "note": ("s.183: ₹1,000–₹2,000 for a light motor vehicle, ₹2,000–₹4,000 for a medium or "
                   "heavy vehicle; a second offence leads to the licence being impounded, not a "
                   "higher fixed fine."),
          "why": "Graph said ₹5,000 repeat and imprisonment; s.183 provides neither."},
    299: {"first_offence": 500, "repeat_offence": 1500, "imprisonment": None,
          "note": ("Under the cited s.177: ₹500, ₹1,500 for a repeat. A forged or copied plate can "
                   "separately attract criminal charges for forgery or cheating."),
          "why": "Graph said ₹5,000 with imprisonment; the cited s.177 fixes ₹500 / ₹1,500."},
}

# Rows whose AMOUNT is fine but whose cited section is wrong — label, don't change.
SECTION_NOTES: dict[int, str] = {
    273: ("The cited s.161 is the hit-and-run compensation scheme, not a penalty; failing to "
          "stop, help or report after an accident is punished under s.187."),
    426: "The cited s.189 is about racing; driving an unsafe vehicle falls under s.190.",
    275: ("s.4 sets the minimum age; the penalties (including the guardian's liability and jail "
          "term) are under s.199A."),
}

STATE_NOTE = ("No verified state notification was found for this amount. Under s.200 the state's "
              "notified compounding amount applies, else the MV Act figure — check your state's "
              "e-challan portal.")


def fields(node: dict) -> dict:
    return ((node or {}).get("_verification") or {}).get("fields") or {}


def matched_section(p: dict) -> str | None:
    """'Matches s.177 — General provision for punishment of offences.' from the audit note."""
    m = re.search(r"s\.([0-9A-Z()]+) '([^']+)'", p.get("note") or "")
    return f"Matches s.{m.group(1)} — {m.group(2)}." if m else None


def ensure_columns(con: sqlite3.Connection) -> None:
    have = {r[1] for r in con.execute("PRAGMA table_info(penalty)")}
    for col in ("verify_status", "verify_note"):
        if col not in have:
            con.execute(f"ALTER TABLE penalty ADD COLUMN {col} TEXT")
    have = {r[1] for r in con.execute("PRAGMA table_info(violation)")}
    for col in ("section_status", "section_note"):
        if col not in have:
            con.execute(f"ALTER TABLE violation ADD COLUMN {col} TEXT")


def source_id(con: sqlite3.Connection, p: dict) -> int | None:
    """One `source` row per URL the audit retrieved (key = the audit's source id)."""
    url = p.get("source_url")
    if not url:
        return None
    key = f"audit:{p.get('source_id') or url}"
    row = con.execute("SELECT id FROM source WHERE key = ?", (key,)).fetchone()
    if row:
        if "indiankanoon.org/doc/785258" in url:      # earlier runs titled it from one snippet
            con.execute("UPDATE source SET title = ? WHERE id = ?",
                        ("Motor Vehicles Act, 1988 — full text as amended", row[0]))
        return row[0]
    host = urlparse(url).netloc.replace("www.", "")
    title = ("Motor Vehicles Act, 1988 — full text as amended" if "indiankanoon.org/doc/785258" in url
             else f"Source retrieved by the dataset audit ({host})")
    con.execute(
        "INSERT INTO source(key, title, url, retrieved, note) VALUES (?,?,?,?,?)",
        (key, title, url, (p.get("retrieved_at") or "")[:10],
         "Retrieved and matched by the Sept-2026 dataset audit (docs/DATASET_VALIDATION_REPORT.md)."),
    )
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def main() -> None:
    nodes = json.loads(V5.read_text(encoding="utf-8"))["nodes"]
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_columns(con)
    counts: dict[str, int] = {}

    rows = con.execute("SELECT id, scope, first_offence, repeat_offence, source_id FROM penalty").fetchall()
    for r in rows:
        pid, scope = r["id"], r["scope"]
        f = fields(nodes.get(f"fine:{pid}"))
        first = f.get("first_offence") or {}
        repeat = f.get("repeat_offence") or {}
        upd: dict = {}

        if pid in CORRECTIONS:
            c = CORRECTIONS[pid]
            upd.update({k: v for k, v in c.items() if k in ("first_offence", "repeat_offence", "imprisonment")})
            upd["verify_status"] = "corrected"
            upd["verify_note"] = c["note"]
            ev = first if first.get("source_url") else repeat
            sid = source_id(con, ev)
            if sid:
                upd["source_id"] = sid
        elif first.get("status") == "verified" and first.get("source_url"):
            upd["verify_status"] = "verified"
            upd["verify_note"] = matched_section(first)
            upd["source_id"] = source_id(con, first)
            # A verified first amount can still carry a repeat amount the statute
            # contradicts — the whole §177A cluster said ₹1,500.
            if repeat.get("status") == "contradicted":
                upd["repeat_offence"] = None
                upd["verify_note"] = S177A if "177A" in (repeat.get("note") or "") else repeat.get("note")
            elif repeat.get("status") == "verified" and repeat.get("source_url"):
                pass                                   # both amounts match the same section
        else:
            upd["verify_status"] = "unverified"
            if pid in SECTION_NOTES:
                upd["verify_note"] = SECTION_NOTES[pid]
            elif scope in ("state", "city"):
                upd["verify_note"] = STATE_NOTE
            else:
                upd["verify_note"] = "No source for this amount could be retrieved — check the e-challan portal."
        if pid in SECTION_NOTES and upd.get("verify_status") != "unverified":
            upd["verify_note"] = " ".join(filter(None, [upd.get("verify_note"), SECTION_NOTES[pid]]))

        counts[upd["verify_status"]] = counts.get(upd["verify_status"], 0) + 1
        sets = ", ".join(f"{k} = ?" for k in upd)
        con.execute(f"UPDATE penalty SET {sets} WHERE id = ?", (*upd.values(), pid))

    vcounts: dict[str, int] = {}
    for (code,) in con.execute("SELECT code FROM violation").fetchall():
        p = fields(nodes.get(f"vio:{code}")).get("mv_section") or {}
        status = p.get("status") or "unaudited"
        vcounts[status] = vcounts.get(status, 0) + 1
        note = p.get("note") if status != "verified" else None
        con.execute("UPDATE violation SET section_status = ?, section_note = ? WHERE code = ?",
                    (status, note, code))

    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('provenance', ?)",
                (json.dumps({"applied_from": V5.name, "policy": "cite+label",
                             "penalties": counts, "sections": vcounts}),))
    con.commit()
    print("penalties:", counts)
    print("sections: ", vcounts)


if __name__ == "__main__":
    main()
