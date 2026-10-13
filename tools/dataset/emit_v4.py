#!/usr/bin/env python3
"""
Write provenance back into the graph.

Produces drivelegal_graph.v4.json: byte-identical node values, plus a
`_verification` block on every node and a top-level `sources` registry.
No stored value is altered, added to, or removed.
"""
import json, collections, hashlib, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
OUT = ROOT / "data" / "audit"
COMPILED = ROOT / "data" / "compiled"
G = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())
CLAIMS = {c["claim_id"]: c for c in json.loads((OUT / "claims.json").read_text())}

V = {}
for f in ("verdicts_central.json", "verdicts_geo.json", "verdicts_remaining.json"):
    V.update(json.loads((OUT / f).read_text()))
assert len(V) == len(CLAIMS), f"{len(V)} verdicts for {len(CLAIMS)} claims"

NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

# ---------------------------------------------------------- source registry
sources = {}
for v in V.values():
    if not v.get("source_url"):
        continue
    sid = "src_" + hashlib.sha1(v["source_url"].encode()).hexdigest()[:10]
    sources.setdefault(sid, {
        "source_id": sid, "url": v["source_url"], "title": v["source_title"],
        "retrieved_at": v["retrieved_at"], "claims_supported": 0,
    })
    sources[sid]["claims_supported"] += 1
    v["source_id"] = sid

# ------------------------------------------------- attach to nodes in place
per_node = collections.defaultdict(list)
for v in V.values():
    per_node[v["node_id"]].append(v)

RANK = {"contradicted": 0, "no_citation": 1, "unsupported_section": 2,
        "off_topic": 3, "unverified": 4, "verified": 5}

for nid, node in G["nodes"].items():
    vs = per_node.get(nid, [])
    if not vs:
        node["_verification"] = {
            "status": "not_assessed", "claims": 0,
            "note": "No individually verifiable factual claim was extracted from this node.",
            "assessed_at": NOW,
        }
        continue
    counts = collections.Counter(v["status"] for v in vs)
    worst = min(vs, key=lambda v: RANK.get(v["status"], 9))["status"]
    node["_verification"] = {
        "status": worst,                       # worst-case status on the node
        "claims": len(vs),
        "breakdown": dict(counts),
        "verified_fraction": round(counts.get("verified", 0) / len(vs), 3),
        "assessed_at": NOW,
        "fields": {
            v["field_path"]: {
                "status": v["status"],
                "confidence": v["confidence"],
                "source_id": v.get("source_id"),
                "source_url": v.get("source_url"),
                "retrieved_at": v.get("retrieved_at"),
                "evidence": (v.get("quote") or "")[:400] or None,
                "note": v.get("note"),
                "claim_id": v["claim_id"],
            } for v in sorted(vs, key=lambda x: RANK.get(x["status"], 9))
        },
    }

# --------------------------------------------------------------- top matter
overall = collections.Counter(v["status"] for v in V.values())
G["sources"] = sources
G["meta"]["verification"] = {
    "schema": "DriveLegal provenance v1",
    "assessed_at": NOW,
    "total_claims": len(V),
    "status_counts": dict(overall),
    "verified_fraction": round(overall.get("verified", 0) / len(V), 4),
    "method": (
        "Every node was decomposed into atomic claims; each claim was matched against "
        "a retrieved primary or reference source. Statuses: verified (source supports "
        "the stored value), contradicted (source states otherwise), off_topic (cited "
        "section is about something else), unsupported_section (cited section does not "
        "exist), no_citation (the graph cites no legal authority at all), unverified "
        "(no reachable source could confirm or refute). No stored value was changed."
    ),
    "known_limits": [
        "State compounding notifications under s.200 MV Act were unreachable: of 44 "
        "official portals probed, 9 responded and none published a machine-readable "
        "fine schedule. All subnational fine amounts are therefore unverified.",
        "IRC standards are paywalled and absent from the corpus.",
        "RTO code verification confirms a code exists, not that it maps to the stored "
        "district.",
        "Coordinate checks use a name-matched gazetteer and cannot always separate a "
        "district from its headquarters town.",
    ],
}

dest = OUT / "drivelegal_graph.v4.json"
dest.write_text(json.dumps(G, ensure_ascii=False, indent=1))

# ---------------------------------------------------------------- integrity
orig = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())
new = json.loads(dest.read_text())
changed = []
for nid, on in orig["nodes"].items():
    nn = {k: v for k, v in new["nodes"][nid].items() if k != "_verification"}
    if nn != on:
        changed.append(nid)
assert new["edges"] == orig["edges"], "edges changed"
assert new["indexes"] == orig["indexes"], "indexes changed"
print(f"nodes whose original values changed: {len(changed)}  (must be 0)")
print(f"wrote {dest} ({dest.stat().st_size/1e6:.2f} MB)")
print(f"sources registered: {len(sources)}")
for k, n in overall.most_common():
    print(f"  {k:<22} {n:>5}  {100*n/len(V):5.1f}%")

# unverified ledger, ordered by severity then node
ledger = sorted(V.values(), key=lambda v: (RANK.get(v["status"], 9), v["node_id"]))
(OUT / "ledger.json").write_text(json.dumps(
    [v for v in ledger if v["status"] != "verified"], ensure_ascii=False, indent=1))
print(f"ledger entries (everything not verified): "
      f"{sum(1 for v in V.values() if v['status'] != 'verified')}")
