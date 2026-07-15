#!/usr/bin/env python3
"""
build_offline_bundle.py — compile the on-device offline data bundle
===================================================================
Extracts the minimum slice of data/compiled/drivelegal_graph.json needed for the
client-side offline engine (resolver + fine lookup + narration) to answer with
ZERO network. Output is shipped inside the mobile app / precached by the PWA.

Run:  python3 scripts/build_offline_bundle.py
Out:  apps/mobile/src/offline/drivelegal_offline.json
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC  = ROOT / "data" / "compiled" / "drivelegal_graph.json"
OUT  = ROOT / "apps" / "mobile" / "src" / "offline" / "drivelegal_offline.json"

# Violation fields the offline engine actually uses (keeps the bundle small).
VIO_FIELDS = [
    "name", "mv_section", "grp", "compoundable", "vehicle_applicability",
    "keywords", "tips_to_avoid", "what_to_do_next", "consequence",
    "dl_consequence", "common_misconception",
]
FINE_FIELDS = [
    "first_offence", "repeat_offence", "imprisonment",
    "vehicle_class", "state_code", "city_code", "note",
]


def main() -> None:
    g = json.loads(SRC.read_text(encoding="utf-8"))
    nodes, idx = g["nodes"], g["indexes"]

    violations = {}
    for nid, n in nodes.items():
        if n.get("type") == "violation":
            violations[n["code"]] = {k: n.get(k) for k in VIO_FIELDS}

    states = {}
    for nid, n in nodes.items():
        if n.get("type") == "state":
            states[n["code"]] = {
                "name": n.get("name"),
                "multiplier": n.get("multiplier", 1.0),
                "fallback_central": n.get("fallback_central", True),
            }

    # Only keep fine nodes referenced by fine_by_violation (drops nothing used).
    fbv = idx["fine_by_violation"]
    referenced = set()
    for entry in fbv.values():
        referenced.update(entry.get("central", []))
        for lst in entry.get("state", {}).values():
            referenced.update(lst)
        for lst in entry.get("city", {}).values():
            referenced.update(lst)

    fines = {}
    for nid in referenced:
        n = nodes.get(nid)
        if n:
            fines[nid] = {k: n.get(k) for k in FINE_FIELDS}

    bundle = {
        "meta": {
            "version": g.get("meta", {}).get("version"),
            "generated_from": "drivelegal_graph.json",
            "counts": {
                "violations": len(violations),
                "states": len(states),
                "fines": len(fines),
            },
        },
        "violations": violations,
        "states": states,
        "fines": fines,
        "fine_by_violation": fbv,
        "keyword_to_vio": idx["keyword_to_vio"],
        "vio_by_bucket": idx["vio_by_bucket"],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.relative_to(ROOT)} ({kb:.0f} KB)")
    print("Counts:", bundle["meta"]["counts"])


if __name__ == "__main__":
    main()
