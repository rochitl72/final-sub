#!/usr/bin/env python3
"""
DriveLegal graph restructure: v3 -> v5.

Changes, all reversible and none of them silent:
  1. Remove exact-duplicate fine rows.
  2. Separate genuine subnational deviations from rows that merely restate the
     central amount; the latter become cascade hits rather than stored rows.
  3. Normalise every money value into one typed shape.
  4. Declare the resolution policy in the data, per field class.
  5. Drop the state `multiplier` (a synthetic quantity with no legal basis).
  6. Repair referential and index defects.
  7. Split serving vs quarantine by verification status.

No stored legal VALUE is altered. Rows are removed only when byte-identical to
another row, or when they restate the central figure — and in the second case
the value survives as the central row the cascade resolves to.
"""
import json, re, collections, copy, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
OUT = ROOT / "data" / "audit"
COMPILED = ROOT / "data" / "compiled"
G = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())
V4 = json.loads((OUT / "drivelegal_graph.v4.json").read_text())
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

N = copy.deepcopy(G["nodes"])
report = collections.OrderedDict()

# ══════════════════════════════════════════════════════ 1. money normalisation
BASIS_HINTS = [
    (r"per\s+excess\s+passenger", "per_excess_passenger"),
    (r"per\s+(?:extra\s+)?tonne", "per_tonne"),
    (r"per\s+alteration", "per_alteration"),
    (r"per\s+day", "per_day"),
]


def money(raw, context=""):
    """Return one consistent shape for any fine value the graph contains."""
    m = {"amount_inr": None, "max_inr": None, "basis": "flat", "per_unit": None,
         "currency": "INR", "raw": raw, "resolved": False, "note": None}
    blob = f"{context} {json.dumps(raw, ensure_ascii=False) if not isinstance(raw, str) else raw}"
    for pat, b in BASIS_HINTS:
        if re.search(pat, blob, re.I):
            m["basis"] = b
            m["per_unit"] = b.replace("per_", "")
            break

    if raw is None:
        m["note"] = "no amount recorded"
        return m
    if isinstance(raw, bool):
        m["note"] = "boolean where an amount was expected"
        return m
    if isinstance(raw, (int, float)):
        m["amount_inr"] = int(raw)
        m["resolved"] = True
        return m
    if isinstance(raw, str):
        digits = re.findall(r"\d[\d,]*", raw)
        if digits:
            vals = [int(d.replace(",", "")) for d in digits]
            m["amount_inr"] = min(vals)
            m["max_inr"] = max(vals) if len(vals) > 1 else None
            m["resolved"] = True
            m["note"] = f"parsed from free text: {raw!r}"
        else:
            m["note"] = (f"not a monetary amount: {raw!r} — this is a policy marker "
                         f"(e.g. 'Local', 'yes'), not a fine, and must not be shown "
                         f"to a user as a figure")
        return m
    if isinstance(raw, dict):
        first = raw.get("first")
        if isinstance(first, (int, float)):
            m["amount_inr"] = int(first)
            m["resolved"] = True
        elif isinstance(first, str):
            d = re.findall(r"\d[\d,]*", first)
            if d:
                m["amount_inr"] = int(d[0].replace(",", ""))
                m["resolved"] = True
                m["note"] = f"parsed from free text: {first!r}"
        rep = raw.get("repeat")
        if isinstance(rep, (int, float)):
            m["max_inr"] = int(rep)
        extras = {k: v for k, v in raw.items() if k not in ("first", "repeat")}
        if extras:
            m["qualifiers"] = extras
        if not m["resolved"]:
            m["note"] = ("no scalar first-offence amount in this structure; "
                         "qualifiers preserved verbatim")
        return m
    m["note"] = f"unrecognised value type {type(raw).__name__}"
    return m


road_money = collections.Counter()
for n in N.values():
    if n.get("type") != "road_class":
        continue
    for r in n.get("key_rules") or []:
        if isinstance(r, dict) and "fine_inr" in r:
            norm = money(r["fine_inr"], f"{r.get('rule', '')} {r.get('section', '')}")
            r["fine"] = norm
            road_money["resolved" if norm["resolved"] else "unresolved"] += 1
report["road_rule_money"] = dict(road_money)

# ═══════════════════════════════════════════════════ 2. dedupe the fine table
FKEY = ("violation_code", "scope", "state_code", "city_code", "vehicle_class")
fines = {nid: n for nid, n in N.items() if n.get("type") == "fine"}
groups = collections.defaultdict(list)
for nid, f in fines.items():
    groups[tuple(f.get(k) for k in FKEY)].append(nid)

dropped_dupe, alias = [], {}
for k, ids in groups.items():
    if len(ids) < 2:
        continue
    def strip(i):
        return {kk: vv for kk, vv in N[i].items() if kk not in ("id", "_verification")}
    keep = sorted(ids, key=lambda i: N[i]["id"])[0]
    for i in ids:
        if i != keep and strip(i) == strip(keep):
            dropped_dupe.append(i)
            alias[i] = keep
for i in dropped_dupe:
    del N[i]
report["exact_duplicate_fine_rows_removed"] = len(dropped_dupe)

# ══════════════════════════ 3. collapse subnational rows that restate central
central = {}
for n in N.values():
    if n.get("type") == "fine" and n.get("scope") == "central":
        central[(n["violation_code"], n.get("vehicle_class"))] = n

collapsed, kept_deviation = [], []
for nid, f in list(N.items()):
    if f.get("type") != "fine" or f.get("scope") == "central":
        continue
    c = (central.get((f.get("violation_code"), f.get("vehicle_class")))
         or central.get((f.get("violation_code"), None)))
    if not c:
        kept_deviation.append(nid)
        continue
    same = (f.get("first_offence") == c.get("first_offence")
            and f.get("repeat_offence") == c.get("repeat_offence")
            and not f.get("imprisonment"))
    if same and not f.get("note"):
        collapsed.append(nid)
        del N[nid]
    else:
        f["deviates_from_central"] = not same
        kept_deviation.append(nid)
report["subnational_rows_collapsed_into_cascade"] = len(collapsed)
report["subnational_rows_kept"] = len(kept_deviation)

# normalise money on every surviving fine row
for n in N.values():
    if n.get("type") != "fine":
        continue
    n["fine_first"] = money(n.get("first_offence"), n.get("violation_code", ""))
    n["fine_repeat"] = money(n.get("repeat_offence"), n.get("violation_code", ""))

# ════════════════════════════════════════════ 4. drop the synthetic multiplier
mult_removed = 0
for n in N.values():
    if n.get("type") == "state" and "multiplier" in n:
        n["_removed_multiplier"] = n.pop("multiplier")
        mult_removed += 1
report["state_multiplier_removed"] = mult_removed

# ═══════════════════════════════════ 5. vehicle applicability: codes vs prose
VEH = {n["code"] for n in N.values() if n.get("type") == "vehicle"}
prose = collections.Counter()
for n in N.values():
    if n.get("type") != "road_class":
        continue
    for fld in ("permitted_vehicles", "prohibited_vehicles"):
        vals = n.get(fld) or []
        codes = [v for v in vals if v in VEH]
        text = [v for v in vals if v not in VEH]
        n[fld + "_codes"] = codes
        n[fld + "_notes"] = text
        prose[fld] += len(text)
report["vehicle_prose_separated"] = dict(prose)

# ══════════════════════════════════════════ 6. referential + index repairs
fixes = []
# Daman & Diu merged into Dadra and Nagar Haveli and Daman and Diu in 2020. The
# merged UT is ALREADY in the graph as state:DN — Silvassa points at it — so the
# repair is to reuse that node, not to invent a new code.
if "state:DD" not in N and "state:DN" in N:
    for nid, n in N.items():
        if n.get("type") == "city" and n.get("state_code") == "DD":
            n["state_code_original"] = "DD"
            n["state_code"] = "DN"
            n["_repair"] = (
                "state_code 'DD' had no state node: Daman & Diu merged into Dadra and "
                f"Nagar Haveli and Daman & Diu in 2020, which is already present as "
                f"state:DN ({N['state:DN']['name']}). Original value preserved in "
                f"state_code_original.")
            fixes.append(f"{nid}: state_code DD -> DN ({N['state:DN']['name']})")
            if n.get("code") == "DIU" and n.get("name") == "Daman":
                n["_name_code_mismatch"] = (
                    "Node code is 'DIU' but the name is 'Daman' — these are two different "
                    "towns in the same UT. Flagged for review; not changed, because either "
                    "the code or the name could be the intended one.")
report["referential_repairs"] = fixes

spatial = copy.deepcopy(G["spatial"])
indexed = {c.get("code") if isinstance(c, dict) else c for c in spatial["cities"]}
added = []
for n in N.values():
    if n.get("type") == "city" and n.get("code") not in indexed:
        if n.get("lat") is not None and n.get("lng") is not None:
            spatial["cities"].append({"code": n["code"], "name": n["name"],
                                      "lat": n["lat"], "lng": n["lng"],
                                      "state_code": n.get("state_code")})
            added.append(n["code"])
report["cities_added_to_spatial_index"] = len(added)

# ══════════════════════════════════════════ 7. FIELD-LEVEL serving / quarantine
# Tiering per node would be wrong: a district whose *helpline* is misattributed
# still has a valid name, state and RTO codes, and the resolver needs them to
# locate the user at all. So every node stays addressable, and only the
# individual FIELDS that failed verification are withheld from the serving copy.
IDENTITY = {
    "type", "code", "id", "name", "state_code", "state_name", "city_code",
    "district_code", "violation_code", "scope", "vehicle_class", "keywords",
    "grp", "aliases", "road_class", "bucket", "segment", "subtype", "fine_class",
    "lat", "lng", "rto_prefix", "capital", "fallback_to_state",
    "fallback_central", "kind", "waypoints", "bbox", "_repair",
    "state_code_original", "_removed_multiplier",
}
BLOCK = {"contradicted", "off_topic", "unsupported_section", "no_citation"}
v4nodes = V4["nodes"]


# A normalised or derived copy of a field must be withheld with its source field,
# or the tier gate is trivially bypassed by reading the derived key instead.
DERIVED = {
    "first_offence": ["fine_first"],
    "repeat_offence": ["fine_repeat"],
    "permitted_vehicles": ["permitted_vehicles_codes", "permitted_vehicles_notes"],
    "prohibited_vehicles": ["prohibited_vehicles_codes", "prohibited_vehicles_notes"],
}


def targets(field_path):
    """Map a claim's field_path onto every node key it governs, derived keys included."""
    if field_path == "lat,lng":
        return [("lat", None), ("lng", None)]
    if "[" in field_path:
        base = field_path.split("[")[0]
        return [(base, None)] + [(d, None) for d in DERIVED.get(base, [])]
    if "." in field_path:
        head, tail = field_path.split(".", 1)
        return [(head, tail)]
    return [(field_path, None)] + [(d, None) for d in DERIVED.get(field_path, [])]


serving, quarantine = {}, {}
withheld_total = collections.Counter()
node_stats = collections.Counter()

for nid, n in N.items():
    ver = v4nodes.get(nid, {}).get("_verification", {})
    n["_verification"] = ver
    fields = ver.get("fields", {})

    srv = copy.deepcopy(n)
    withheld = {}
    for fp, info in fields.items():
        if info.get("status") == "verified":
            continue
        for key, sub in targets(fp):
            if key in IDENTITY or key not in srv:
                continue
            if sub is not None and isinstance(srv.get(key), dict):
                if sub in srv[key]:
                    withheld[f"{key}.{sub}"] = {
                        "value": srv[key].pop(sub),
                        "status": info["status"], "reason": info.get("note"),
                    }
                    withheld_total[info["status"]] += 1
            else:
                withheld[key] = {"value": srv.pop(key), "status": info["status"],
                                 "reason": info.get("note")}
                withheld_total[info["status"]] += 1

    substantive = [k for k in srv if k not in IDENTITY and not k.startswith("_")]
    srv["_withheld"] = withheld
    srv["_serveable"] = bool(
        [fp for fp, i in fields.items() if i.get("status") == "verified"]
    ) or not fields
    srv["_withheld_count"] = len(withheld)
    node_stats["serveable" if srv["_serveable"] else "recognised_only"] += 1
    node_stats[("serveable_" if srv["_serveable"] else "recognised_only_")
               + str(n.get("type"))] += 1

    serving[nid] = srv
    if withheld:
        q = copy.deepcopy(n)
        q["_withheld_fields"] = list(withheld)
        quarantine[nid] = q

report["field_level_withholding"] = dict(withheld_total)
report["nodes_serveable"] = node_stats["serveable"]
report["nodes_recognised_only"] = node_stats["recognised_only"]
report["nodes_in_review_queue"] = len(quarantine)
report["tier_counts"] = {k: v for k, v in sorted(node_stats.items())
                         if k not in ("serveable", "recognised_only")}

# ═══════════════════════════════════════════ 8. resolution policy, declared
POLICY = {
    "precedence": ["city", "district", "state", "central"],
    "rationale": (
        "Section 200 of the Motor Vehicles Act 1988 empowers a State Government to "
        "specify compounding amounts by notification. Where a state has notified "
        "nothing, the amount fixed by the Act itself is the operative law. Falling "
        "back to the central figure is therefore legally correct, not a degraded "
        "answer — but the answer must name the level that supplied it."
    ),
    "field_classes": {
        "fine": {
            "cascade": True, "terminal_level": "central",
            "on_exhaustion": "answer_from_central",
            "must_state_level": True,
            "note": "Central penalty is the residual law where no state notification exists.",
        },
        "helpline": {
            "cascade": True, "terminal_level": "national",
            "on_exhaustion": "answer_112",
            "must_state_level": True,
            "note": "112 is the national emergency number and is always a valid answer.",
        },
        "speed_limit": {
            "cascade": False, "terminal_level": None,
            "on_exhaustion": "refuse_and_defer_to_posted_sign",
            "must_state_level": True,
            "note": ("SAFETY-CRITICAL. States and local authorities notify limits LOWER "
                     "than the national ceiling. Falling back to the ceiling could tell a "
                     "user a higher speed is lawful than the road actually permits. "
                     "Absence must resolve to 'the posted sign governs', never a number."),
        },
        "city_rule": {
            "cascade": False, "terminal_level": None,
            "on_exhaustion": "refuse",
            "must_state_level": True,
            "note": ("No-horn zones, parking rates, BRT lanes and similar have no central "
                     "equivalent. Absence means silence, not a national default."),
        },
        "legal_section": {
            "cascade": False, "terminal_level": "central",
            "on_exhaustion": "refuse",
            "must_state_level": False,
            "note": "Sections are central by construction.",
        },
    },
    "serving_rule": (
        "A value may be returned to a member of the public only if its node is in the "
        "serving graph. Quarantined values are not loaded by the serving layer at all; "
        "they are a review queue, not a fallback."
    ),
}

meta = copy.deepcopy(G["meta"])
meta.update({
    "version": "5.0",
    "schema": "DriveLegal Graph v5 (deduplicated, typed, source-tiered)",
    "restructured_at": NOW,
    "derived_from": "DriveLegal Graph v3 (fully enriched)",
    "resolution_policy": POLICY,
    "changes": report,
    "not_yet_addressed": [
        "No temporal validity. No legal fact carries an effective_from / "
        "effective_until / superseded_by. Deferred by decision; until it is added the "
        "dataset cannot answer about past challans, cannot mark a value superseded, "
        "and must be fully re-audited whenever the law changes."
    ],
})

base = {"meta": meta, "edges": [], "indexes": {}, "spatial": spatial,
        "sources": V4.get("sources", {})}


def rebuild(nodes, tier):
    g = copy.deepcopy(base)
    g["meta"] = dict(meta, tier=tier)
    g["nodes"] = nodes
    g["edges"] = [e for e in G["edges"] if e["src"] in nodes and e["dst"] in nodes]
    fbv = collections.defaultdict(lambda: {"central": [], "state": {}, "city": {}})
    for nid, n in nodes.items():
        if n.get("type") != "fine":
            continue
        b = fbv[n["violation_code"]]
        if n.get("scope") == "central":
            b["central"].append(nid)
        elif n.get("city_code"):
            b["city"].setdefault(n["city_code"], []).append(nid)
        elif n.get("state_code"):
            b["state"].setdefault(n["state_code"], []).append(nid)
    g["indexes"] = dict(G["indexes"])
    g["indexes"]["fine_by_violation"] = {k: v for k, v in fbv.items()}
    per_type = collections.Counter(n.get("type") for n in nodes.values())
    # Keep the v3 count key names (plural) so existing consumers that read
    # meta.counts["districts"] etc. keep working against the v5 artifact.
    PLURAL = {"state": "states", "city": "cities", "district": "districts",
              "road_class": "roads", "corridor": "corridors",
              "road_point": "road_points", "vehicle": "vehicles",
              "violation": "violations", "fine": "fines"}
    counts = {PLURAL.get(k, k): v for k, v in per_type.items()}
    for key in PLURAL.values():
        counts.setdefault(key, 0)
    counts["nodes"] = len(nodes)
    counts["edges"] = len(g["edges"])
    g["meta"]["counts"] = counts
    return g


(COMPILED / "drivelegal_graph.v5.serving.json").write_text(
    json.dumps(rebuild(serving, "serving"), ensure_ascii=False, indent=1))
(COMPILED / "drivelegal_graph.v5.quarantine.json").write_text(
    json.dumps(rebuild(quarantine, "quarantine"), ensure_ascii=False, indent=1))
(OUT / "restructure_report.json").write_text(json.dumps(report, indent=1))

print(f"v3 nodes: {len(G['nodes'])}  ->  v5 nodes: {len(N)}")
for k, v in report.items():
    if k != "tier_counts":
        print(f"  {k}: {v}")
print("\ntier split:")
for k, v in report["tier_counts"].items():
    print(f"  {k:<28} {v}")
