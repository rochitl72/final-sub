#!/usr/bin/env python3
"""
CI gate for the DriveLegal dataset. Exit non-zero blocks the build.

Every check here exists because the failure it catches already happened once.
"""
import json, sqlite3, sys, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
SERVING = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"
DB = ROOT / "data" / "compiled" / "drivelegal.sqlite"

fail, warn = [], []


def check(cond, msg):
    (fail if not cond else warn).append(msg) if not cond else None


G = json.loads(SERVING.read_text())
NODES = G["nodes"]

# 1 ── no money without a source, anywhere in the serving graph -------------
unsourced = []
for nid, n in NODES.items():
    if n.get("type") != "fine":
        continue
    m = n.get("fine_first") or {}
    if m.get("amount_inr") is None:
        continue
    v = (n.get("_verification") or {}).get("fields", {}).get("first_offence", {})
    if v.get("status") != "verified" or not v.get("source_url"):
        unsourced.append(nid)
if unsourced:
    fail.append(f"[money-without-source] {len(unsourced)} serving fine rows carry an "
                f"amount with no verified source: {unsourced[:5]}")

# 2 ── no section citation without a source --------------------------------
badcite = []
for nid, n in NODES.items():
    if n.get("type") != "violation" or not n.get("mv_section"):
        continue
    v = (n.get("_verification") or {}).get("fields", {}).get("mv_section", {})
    if v.get("status") != "verified":
        badcite.append(nid)
if badcite:
    fail.append(f"[citation-without-source] {len(badcite)} serving violations expose an "
                f"mv_section that did not pass verification: {badcite[:5]}")

# 3 ── derived fields must not survive their source field's withholding -----
# (this is the bug that silently re-opened the tier gate once already)
DERIVED = {"first_offence": "fine_first", "repeat_offence": "fine_repeat",
           "permitted_vehicles": "permitted_vehicles_codes",
           "prohibited_vehicles": "prohibited_vehicles_codes"}
leaks = []
for nid, n in NODES.items():
    wh = n.get("_withheld") or {}
    for src, der in DERIVED.items():
        if src in wh and der in n:
            leaks.append(f"{nid}.{der}")
if leaks:
    fail.append(f"[derived-field-leak] {len(leaks)} nodes withhold a field but still "
                f"expose its derived copy — the tier gate is bypassed: {leaks[:5]}")

# 4 ── money is always typed, never a bare string --------------------------
untyped = []
for nid, n in NODES.items():
    for fld in ("fine_first", "fine_repeat"):
        m = n.get(fld)
        if m is None:
            continue
        if not isinstance(m, dict) or "amount_inr" not in m:
            untyped.append(f"{nid}.{fld}")
        elif m["amount_inr"] is not None and not isinstance(m["amount_inr"], int):
            untyped.append(f"{nid}.{fld} (amount_inr is {type(m['amount_inr']).__name__})")
if untyped:
    fail.append(f"[untyped-money] {len(untyped)}: {untyped[:5]}")

# 5 ── speed limits must never be cascadable -------------------------------
pol = G["meta"]["resolution_policy"]["field_classes"]
if pol.get("speed_limit", {}).get("cascade"):
    fail.append("[unsafe-cascade] speed_limit is marked cascade:true. Cascading a speed "
                "limit upward can tell a user a higher speed is lawful than the road "
                "permits. This must stay false.")
if pol.get("city_rule", {}).get("cascade"):
    fail.append("[unsafe-cascade] city_rule is marked cascade:true; no central equivalent "
                "exists, so absence must mean silence.")

# 6 ── referential integrity ------------------------------------------------
codes = {n.get("code") for n in NODES.values()}
dangling = []
for nid, n in NODES.items():
    if n.get("type") in ("city", "district") and n.get("state_code") not in codes:
        dangling.append(nid)
if dangling:
    fail.append(f"[dangling-jurisdiction] {len(dangling)}: {dangling[:5]}")

# 7 ── the compiled DB must match the serving graph -------------------------
if DB.exists():
    con = sqlite3.connect(f"file:{DB}?immutable=1", uri=True)
    n_db = con.execute("SELECT COUNT(*) FROM fine").fetchone()[0]
    n_json = sum(1 for n in NODES.values()
                 if n.get("type") == "fine"
                 and (n.get("fine_first") or {}).get("amount_inr") is not None
                 and (n.get("_verification") or {}).get("fields", {})
                 .get("first_offence", {}).get("source_url"))
    if n_db != n_json:
        fail.append(f"[stale-artifact] compiled SQLite has {n_db} fine rows but the "
                    f"serving graph has {n_json}. Recompile before shipping.")
    bad = con.execute("SELECT COUNT(*) FROM fine WHERE amount_inr IS NOT NULL "
                      "AND (source_url IS NULL OR source_url='')").fetchone()[0]
    if bad:
        fail.append(f"[money-without-source/sqlite] {bad} rows")
    con.close()
else:
    fail.append("[missing-artifact] drivelegal.sqlite not compiled")

# 8 ── advisory: temporal validity still absent ----------------------------
if "effective_from" not in json.dumps(G["meta"]):
    warn.append("[no-temporal-validity] No fact carries an effective date. The dataset "
                "cannot answer about past challans or mark a value superseded. Deferred "
                "by decision — revisit before public launch.")

print("DriveLegal dataset CI gate\n" + "=" * 52)
for w in warn:
    print(f"  WARN  {w}")
for f in fail:
    print(f"  FAIL  {f}")
if not fail:
    n = sum(1 for x in NODES.values() if x.get("type") == "fine")
    print(f"  PASS  {len(NODES)} serving nodes, {n} fine rows, "
          f"0 unsourced amounts, 0 gate bypasses.")
print("=" * 52)
sys.exit(1 if fail else 0)
