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
# Built from the SERVING graph, which withholds every field that failed source
# verification — so the offline bundle can never state something the online
# service would refuse. tools/dataset/bench_parity.py asserts they agree.
SRC  = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"
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


def _sourced(node, field):
    """True only if this field passed verification AND carries a source URL."""
    p = ((node.get("_verification") or {}).get("fields") or {}).get(field) or {}
    return p.get("status") == "verified" and bool(p.get("source_url"))


def _citation(node, field):
    p = ((node.get("_verification") or {}).get("fields") or {}).get(field) or {}
    return {"source_url": p.get("source_url"), "retrieved_at": p.get("retrieved_at")}


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
            # `multiplier` removed in v5: scaling central fines by a per-state
            # coefficient has no basis in s.200 and was never consistent with the
            # stored state fines. Offline must not reintroduce it.
            states[n["code"]] = {
                "name": n.get("name"),
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

    fines, withheld = {}, 0
    for nid in referenced:
        n = nodes.get(nid)
        if not n:
            continue
        # Same gate as the online service and the compiled SQLite: an amount with
        # no verified source is not shipped to the device at all, so the offline
        # engine cannot state it even if its own logic is wrong.
        if not _sourced(n, "first_offence"):
            withheld += 1
            continue
        row = {k: n.get(k) for k in FINE_FIELDS}
        row["citation"] = _citation(n, "first_offence")
        fines[nid] = row

    bundle = {
        "meta": {
            "version": g.get("meta", {}).get("version"),
            "generated_from": "drivelegal_graph.v5.serving.json",
            "counts": {
                "violations": len(violations),
                "states": len(states),
                "fines": len(fines),
                "fines_withheld_unsourced": withheld,
            },
            "provenance": "every fine in this bundle carries a source_url",
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
