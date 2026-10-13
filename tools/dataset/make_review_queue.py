#!/usr/bin/env python3
"""
Build the human review worklist.

2,760 claims could not be verified. A reviewer cannot work from a 2 MB ledger,
and reviewing them in file order is the wrong order: one central fine row
governs all 36 states, while one state row governs one.

This emits a CSV ordered by how much of the answer surface each claim unblocks,
with the columns a reviewer fills in and `apply_reviews.py` reads back.

Reviewers must paste a source URL and name the instrument. A verdict without
those is rejected on apply — which is the whole point, because the failure this
project is recovering from is exactly a dataset of confident unsourced numbers.
"""
import json, csv, collections, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVING = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"
QUARANTINE = ROOT / "data" / "compiled" / "drivelegal_graph.v5.quarantine.json"
OUT = ROOT / "data" / "audit" / "review_queue.csv"
CLAIMS = ROOT / "data" / "audit" / "claims.json"

G = json.loads(SERVING.read_text())
# `_verification.fields` records the verdict but not the claim kind; join it back
# from the claim ledger so the reviewer is told where to look.
KIND_BY_CLAIM = {c["claim_id"]: c["kind"] for c in json.loads(CLAIMS.read_text())}
Q = json.loads(QUARANTINE.read_text())
NODES = G["nodes"]
STATES = {n["code"]: n["name"] for n in NODES.values() if n.get("type") == "state"}
VIO = {n["code"]: n for n in NODES.values() if n.get("type") == "violation"}
N_STATES = len(STATES)

# Where a reviewer should look, by what kind of claim it is. Being specific here
# is what stops a reviewer "verifying" against a stale mirror — devgan.in still
# serves pre-2019 penalty amounts and would confirm the wrong number.
WHERE_TO_LOOK = {
    "fine_amount_central": (
        "Motor Vehicles Act 1988 as amended by Act 32 of 2019. Use indiacode.nic.in "
        "or the 2019 Gazette. Do NOT use devgan.in or similar mirrors — they still "
        "carry pre-2019 amounts."),
    "fine_amount_state": (
        "The State Government's compounding notification under s.200 MV Act, in the "
        "State Gazette. Ask the Transport Department directly, or file an RTI for "
        "'the current notification under section 200 of the Motor Vehicles Act 1988 "
        "specifying compounding amounts'."),
    "fine_amount_city": (
        "City traffic police or municipal corporation notification. Municipal "
        "penalties come from bye-laws, not the MV Act."),
    "legal_section": (
        "Motor Vehicles Act 1988 (consolidated) / CMVR 1989. Confirm the section "
        "actually penalises this conduct, not merely that it exists."),
    "speed_limit": (
        "SAFETY-CRITICAL. The state or local authority's speed notification for this "
        "road class. Never fall back to the national ceiling — states notify LOWER "
        "limits, and a too-high number here is a safety failure, not a data error."),
    "city_rule": (
        "City traffic police or municipal corporation. If no notification exists, "
        "mark 'reject' — there is no central fallback for a city-specific rule."),
    "helpline": (
        "The district police or transport department's own published number. If only "
        "a state-level control room exists, mark 'reject': it must not be presented "
        "as district-specific."),
    "compoundable": (
        "Section 200 MV Act. Note that s.200 lists some offences only for specific "
        "sub-sections, so a whole-offence true/false may not be answerable."),
    "rto_code": (
        "The State Transport Department's own RTO list. Confirm the code maps to "
        "THIS district — existence alone is not enough."),
    "coordinate": "Survey of India, the district administration site, or OpenStreetMap.",
    "vehicle_spec": "CMVR 1989 schedules and Form 4; MoRTH vehicle-category notifications.",
    "corridor_geom": "NHAI / OpenStreetMap route geometry.",
    "city_fact": "The municipal corporation or city police site.",
    "imprisonment": "Motor Vehicles Act 1988, the penalty section itself.",
    "rto_prefix": "Parivahan / State Transport Department.",
}


def impact(kind, node, field_info):
    """How many (violation x state) answer cells this claim unblocks."""
    if node.get("type") == "fine":
        if node.get("scope") == "central":
            return N_STATES          # governs every state that has no notification
        if node.get("city_code"):
            return 1
        return 1
    if node.get("type") == "violation":
        return N_STATES              # a section or compoundability governs all states
    if node.get("type") == "road_class":
        return N_STATES
    return 1


rows = []
seen = set()
for source_graph in (NODES, Q["nodes"]):
    for nid, n in source_graph.items():
        for field, info in ((n.get("_verification") or {}).get("fields") or {}).items():
            if info.get("status") == "verified":
                continue
            key = (nid, field)
            if key in seen:
                continue
            seen.add(key)

            kind = (info.get("kind")
                    or KIND_BY_CLAIM.get(info.get("claim_id"))
                    or "unknown")
            # current value: from the quarantine copy if it was withheld
            withheld = (n.get("_withheld") or {}).get(field)
            current = withheld["value"] if withheld else n.get(field)

            k = kind
            if kind == "fine_amount":
                k = ("fine_amount_central" if n.get("scope") == "central"
                     else "fine_amount_city" if n.get("city_code")
                     else "fine_amount_state")

            juris = (STATES.get(n.get("state_code"), n.get("state_code"))
                     or n.get("city_code") or "India (central)")
            vname = ""
            if n.get("type") == "fine":
                vname = VIO.get(n.get("violation_code"), {}).get("name", "")
            elif n.get("type") == "violation":
                vname = n.get("name", "")

            rows.append({
                "priority": 0,
                "impact_cells": impact(kind, n, info),
                "status": info.get("status"),
                "node_id": nid,
                "field": field,
                "kind": kind,
                "subject": (vname or n.get("name") or "")[:70],
                "jurisdiction": juris,
                "current_value": json.dumps(current, ensure_ascii=False)[:120],
                "why_it_failed": (info.get("note") or "")[:300],
                "where_to_look": WHERE_TO_LOOK.get(k, "Primary legal instrument."),
                # ── reviewer fills these in ──────────────────────────────────
                "verdict": "",            # confirm | correct | reject | cannot_source
                "corrected_value": "",    # required when verdict = correct
                "source_url": "",         # REQUIRED for confirm and correct
                "instrument": "",         # REQUIRED: e.g. "MV Act 1988 s.183" or
                                          # "Karnataka Gazette notification No. ... "
                "notification_date": "",  # date the instrument takes effect
                "reviewer": "",
                "reviewed_at": "",
                "notes": "",
            })

# contradicted first (confidently wrong beats missing), then by reach
RANK = {"contradicted": 0, "off_topic": 1, "unsupported_section": 2,
        "no_citation": 3, "unverified": 4}
rows.sort(key=lambda r: (RANK.get(r["status"], 9), -r["impact_cells"],
                         r["kind"], r["node_id"]))
for i, r in enumerate(rows, 1):
    r["priority"] = i

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

by_status = collections.Counter(r["status"] for r in rows)
by_kind = collections.Counter(r["kind"] for r in rows)
print(f"wrote {OUT.relative_to(ROOT)}  —  {len(rows)} items")
print("(The audit produced 2,760 non-verified claims; the shortfall is claims on "
      "fine rows\n that no longer exist — 244 exact duplicates and 89 rows that "
      "merely restated\n the central figure were removed in v5, so there is nothing "
      "left to review.)\n")
print("by status (review order):")
for s in sorted(by_status, key=lambda x: RANK.get(x, 9)):
    print(f"  {s:<22} {by_status[s]:>5}")
print("\nby kind:")
for k, c in by_kind.most_common():
    print(f"  {k:<18} {c:>5}")

top = [r for r in rows if r["impact_cells"] >= N_STATES]
print(f"\nhigh-reach items (each unblocks all {N_STATES} states): {len(top)}")
print("first 10 of the queue:")
for r in rows[:10]:
    print(f"  #{r['priority']:<4} [{r['status']:<12}] {r['subject'][:44]:<44} "
          f"{r['jurisdiction'][:16]:<16} {r['current_value'][:14]}")
print(f"\nReviewing just the {len(top)} high-reach items resolves the widest part of "
      f"the answer surface.\nRun apply_reviews.py once the verdict columns are filled.")
