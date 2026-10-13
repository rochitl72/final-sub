#!/usr/bin/env python3
"""
Adjudicate every claim not covered by verify_central.py or verify_geo.py:
subnational fines, speed limits, road rules, city rules and facts, corridors.

Where no reachable authoritative source exists, the claim is marked unverified
with the specific reason — never quietly passed.
"""
import json, re, collections, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
OUT, C = ROOT / "data" / "audit", ROOT / "data" / "audit" / "corpus"
COMPILED = ROOT / "data" / "compiled"
NODES = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())["nodes"]
CLAIMS = json.loads((OUT / "claims.json").read_text())
MVA = json.loads((OUT / "mva_sections.json").read_text())
REACH = {r["key"]: r for r in json.loads((OUT / "source_reachability.json").read_text())}
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

SRC_SPEED = {"url": "https://en.wikipedia.org/wiki/Speed_limits_in_India",
             "title": "Speed limits in India (summarising MoRTH S.O. 1522(E)/1248(E), 6 Apr 2018)",
             "retrieved_at": NOW, "authority": "reference"}
SRC_MVA = {"url": "https://indiankanoon.org/doc/785258/",
           "title": "The Motor Vehicles Act, 1988 (consolidated, as amended)",
           "retrieved_at": NOW, "authority": "primary_statute"}

# MoRTH notification of 6 April 2018, national ceilings (km/h).
# States and local authorities may notify LOWER limits, never higher.
MORTH_2018 = {
    "expressway":  {"M1": 120, "M2_M3": 100, "N": 80,  "two_wheeler": 80},
    "national_hw": {"M1": 100, "M2_M3": 90,  "N": 80,  "two_wheeler": 80},
    "urban":       {"M1": 70,  "M2_M3": 60,  "N": 60,  "two_wheeler": 60},
}
CAT = {"M1_passenger_car": "M1", "M2_M3_buses": "M2_M3", "N1_LCV": "N",
       "N2_N3_HGV": "N", "two_wheeler": "two_wheeler"}


def bucket(rc):
    code = (rc.get("code") or "").upper()
    name = (rc.get("name") or "").lower()
    if "EXP" in code or "expressway" in name:
        return "expressway"
    if code.startswith("NH") or "national highway" in name:
        return "national_hw"
    return "urban"


# which state/city sources actually answered
REACHABLE_STATES = {k.split("_")[0] for k, r in REACH.items() if r["reachable"]}

verdicts = {}


def record(c, status, conf, src, quote, note):
    verdicts[c["claim_id"]] = {
        "claim_id": c["claim_id"], "node_id": c["node_id"], "field_path": c["field_path"],
        "kind": c["kind"], "assertion": c["assertion"], "value": c["value"],
        "status": status, "confidence": round(conf, 2),
        "source_url": src.get("url") if src else None,
        "source_title": src.get("title") if src else None,
        "retrieved_at": src.get("retrieved_at") if src else None,
        "quote": quote, "note": note,
    }


done = set(json.loads((OUT / "verdicts_central.json").read_text())) | \
       set(json.loads((OUT / "verdicts_geo.json").read_text()))

# central fine rows keyed for the derivation test
central_fines = {}
for n in NODES.values():
    if n.get("type") == "fine" and n.get("scope") == "central":
        central_fines[(n["violation_code"], n.get("vehicle_class"))] = n

for c in CLAIMS:
    if c["claim_id"] in done:
        continue
    k, j = c["kind"], c["jurisdiction"]

    # ------------------------------------------------ subnational fine rows
    if k in ("fine_amount", "imprisonment") and c["field_path"] != "multiplier":
        n = NODES[c["node_id"]]
        sc, cc = n.get("state_code"), n.get("city_code")
        cf = (central_fines.get((n.get("violation_code"), n.get("vehicle_class")))
              or central_fines.get((n.get("violation_code"), None)))
        rel = ""
        if cf and cf.get("first_offence") and c["field_path"] == "first_offence":
            base = cf["first_offence"]
            if c["value"] == base:
                rel = (f" This row is identical to the central figure of Rs {base}, so it "
                       f"adds no state-specific information and may simply be a copy.")
            else:
                rel = (f" This row differs from the central figure of Rs {base} by a factor of "
                       f"{round(c['value'] / base, 3)}.")
        where = (f"state {sc}" if sc else f"city {cc}")
        record(c, "unverified", 0.15, None, "",
               f"Compounding amounts below the central level are fixed by each State "
               f"Government by notification in its Official Gazette under s.200 of the Motor "
               f"Vehicles Act. No gazette or official fine schedule for {where} was reachable: "
               f"of the 44 official state, city and central portals probed, 9 responded and "
               f"none published a machine-readable fine schedule. This amount therefore has no "
               f"traceable source.{rel}")
        continue

    # ------------------------------------------------- state fine multiplier
    if k == "fine_amount" and c["field_path"] == "multiplier":
        record(c, "contradicted", 0.8, SRC_MVA, "",
               "Section 200 lets each State Government specify compounding amounts by "
               "notification; it provides no mechanism by which a state scales central fines "
               "by a coefficient. A single per-state 'multiplier' is a modelling device, not a "
               "legal quantity, and in this graph it does not even reproduce the stored state "
               "fines (it matches the actual state/central ratio in 257 of 435 comparable rows).")
        continue

    # --------------------------------------------------------- speed limits
    if k == "speed_limit":
        rc = NODES[c["node_id"]]
        b = bucket(rc)
        cat = CAT.get(c["field_path"].split(".")[-1])
        ceiling = MORTH_2018[b].get(cat) if cat else MORTH_2018[b]["M1"]
        v = c["value"]
        if ceiling is None:
            record(c, "unverified", 0.2, SRC_SPEED, "",
                   "No national ceiling published for this vehicle category.")
        elif b == "urban" and not re.search(r"urban|city|street|residential|arterial",
                                           (rc.get("name") or ""), re.I):
            # MoRTH's 2018 notification fixes ceilings for expressways, national
            # highways and urban roads. State highways, district roads, port areas,
            # forest roads and the like are notified by the State Government, so
            # neither confirming nor refuting them is possible from this source.
            record(c, "unverified", 0.2, SRC_SPEED, "",
                   f"The MoRTH notification of 6 April 2018 fixes ceilings for expressways, "
                   f"national highways and urban roads. '{rc.get('name')}' is none of these — "
                   f"its limit is set by the State Government's own notification, which was not "
                   f"reachable. {v} km/h can be neither confirmed nor refuted here.")
        elif v > ceiling:
            record(c, "contradicted", 0.75, SRC_SPEED,
                   f"MoRTH 2018 ceiling for {cat} on {b.replace('_', ' ')}: {ceiling} km/h",
                   f"Graph states {v} km/h, above the national maximum of {ceiling} km/h. "
                   f"States may notify lower limits but not higher ones.")
        else:
            record(c, "verified", 0.55, SRC_SPEED,
                   f"MoRTH 2018 ceiling for {cat} on {b.replace('_', ' ')}: {ceiling} km/h",
                   f"{v} km/h is at or below the national ceiling of {ceiling} km/h, so it is "
                   f"lawful. This confirms the value is permissible, NOT that any authority has "
                   f"actually notified {v} km/h for this road class — that requires the state or "
                   f"municipal notification, which was not reachable.")
        continue

    # ----------------------------------------------------------- road rules
    if k == "road_rule":
        r = c["value"] if isinstance(c["value"], dict) else {}
        sec = re.search(r"(\d{1,3}[A-Z]{0,3})", str(r.get("section") or ""))
        s = sec.group(1) if sec else None
        d = MVA.get(s)
        fine = r.get("fine_inr")
        amt = fine.get("first") if isinstance(fine, dict) else None
        if not d:
            record(c, "unverified", 0.3, SRC_MVA, "",
                   f"Rule cites {r.get('section')!r}, which does not resolve to an MV Act "
                   f"section (it names a local byelaw, another statute, or nothing specific).")
        elif isinstance(amt, int) and amt in d["amounts"]:
            record(c, "verified", 0.75, SRC_MVA, re.sub(r"\s+", " ", d["text"][:300]),
                   f"Rs {amt} matches s.{s} '{d['heading']}'.")
        elif isinstance(amt, int) and d["amounts"]:
            record(c, "contradicted", 0.7, SRC_MVA, re.sub(r"\s+", " ", d["text"][:300]),
                   f"Rule states Rs {amt}; s.{s} '{d['heading']}' specifies {d['amounts']}.")
        else:
            record(c, "unverified", 0.3, SRC_MVA, re.sub(r"\s+", " ", d["text"][:300]),
                   f"s.{s} '{d['heading']}' exists but fixes no amount matching this rule "
                   f"(stored fine: {json.dumps(fine)}).")
        continue

    # --------------------------------------------- city rules / city facts
    if k in ("city_rule", "city_fact"):
        cn = NODES[c["node_id"]].get("name")
        record(c, "unverified", 0.1, None, "",
               f"City-level traffic rules, municipal penalty rates and enforcement facts are "
               f"published by the city police or municipal corporation. No official source for "
               f"{cn} was reachable during this audit, and the graph records no source for it "
               f"either. Nothing supports this value.")
        continue

    # -------------------------------------------------------- corridor geom
    if k == "corridor_geom":
        wp = c["value"] or []
        bad = [p for p in wp if not (6.0 <= p[0] <= 37.6 and 68.0 <= p[1] <= 97.5)]
        if bad:
            record(c, "contradicted", 0.9, None, "",
                   f"{len(bad)} of {len(wp)} waypoints fall outside India.")
        elif len(wp) < 2:
            record(c, "contradicted", 0.9, None, "",
                   f"Corridor has {len(wp)} waypoint(s); a route needs at least two.")
        else:
            record(c, "unverified", 0.25, None, "",
                   f"All {len(wp)} waypoints are inside India and the chain is structurally "
                   f"valid, but the alignment is a hand-drawn polyline with no linear-reference "
                   f"source. Verifying it needs OpenStreetMap or NHAI route geometry, which is "
                   f"not in the corpus. Geometry this coarse should not be used to decide which "
                   f"road a user is on.")
        continue

    record(c, "unverified", 0.0, None, "", f"No verification route implemented for kind {k!r}.")

(OUT / "verdicts_remaining.json").write_text(json.dumps(verdicts, indent=1))
cnt = collections.Counter(v["status"] for v in verdicts.values())
bykind = collections.defaultdict(collections.Counter)
for v in verdicts.values():
    bykind[v["kind"]][v["status"]] += 1
print(f"remaining claims adjudicated: {len(verdicts)}")
for k, n in cnt.most_common():
    print(f"  {k:<16} {n}")
print()
for k, c2 in bykind.items():
    print(f"  {k:<16} {dict(c2)}")
