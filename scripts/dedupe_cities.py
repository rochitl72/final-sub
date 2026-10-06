#!/usr/bin/env python3
"""
dedupe_cities.py — merge duplicate city nodes in the compiled knowledge graph.

The graph had 36 cities listed twice (e.g. Chennai as CHN *and* CHE): one
enriched node plus a sparse GPS-only twin. Users saw two identical chips
("Chennai (Tamil Nadu)" ×2). This keeps the richest node per (state, name),
back-fills missing lat/lng from its twin, and removes the twin's node, edges
and spatial entry. Idempotent.

Run:  python3 scripts/dedupe_cities.py
Then: python3 scripts/build_offline_bundle.py
"""
import json
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "data" / "compiled" / "drivelegal_graph.json"


def main() -> None:
    g = json.loads(SRC.read_text(encoding="utf-8"))
    nodes = g["nodes"]
    groups = defaultdict(list)
    for nid, n in nodes.items():
        if n.get("type") == "city":
            groups[(n.get("state_code"), (n.get("name") or "").strip().lower())].append(nid)

    drop = set()
    for ids in groups.values():
        if len(ids) < 2:
            continue
        ids.sort(key=lambda i: len(json.dumps(nodes[i])), reverse=True)
        keep = nodes[ids[0]]
        for other in ids[1:]:
            o = nodes[other]
            if keep.get("lat") is None and o.get("lat") is not None:
                keep["lat"], keep["lng"] = o["lat"], o["lng"]
            drop.add(other)

    if not drop:
        print("No duplicate cities — nothing to do.")
        return

    for nid in drop:
        del nodes[nid]
    g["edges"] = [e for e in g["edges"] if e["src"] not in drop and e["dst"] not in drop]

    sp = [c for c in g["spatial"]["cities"] if c["id"] not in drop]
    have = {c["id"] for c in sp}
    for nid, n in nodes.items():   # make sure every kept city with GPS is indexed
        if n.get("type") == "city" and nid not in have and n.get("lat") is not None:
            sp.append({"id": nid, "code": n["code"], "name": n["name"],
                       "state": n["state_code"], "lat": n["lat"], "lng": n["lng"]})
    g["spatial"]["cities"] = sp

    counts = g["meta"]["counts"]
    counts["nodes"] = len(nodes)
    counts["edges"] = len(g["edges"])
    counts["cities"] = sum(1 for n in nodes.values() if n.get("type") == "city")

    SRC.write_text(json.dumps(g, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Removed {len(drop)} duplicate cities → {counts['cities']} cities, "
          f"{len(sp)} with GPS")


if __name__ == "__main__":
    main()
