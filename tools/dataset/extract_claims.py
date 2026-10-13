#!/usr/bin/env python3
"""
DriveLegal claim extractor.

Decomposes drivelegal_graph.json into atomic, individually-verifiable claims.
Each claim is a single assertion that can be matched against exactly one
passage of a primary source. Nothing in the graph is modified.

Claim kinds:
  legal_section   MV Act / CMVR section cited for a violation
  fine_amount     a rupee amount for (violation, scope, vehicle_class)
  imprisonment    a custodial term attached to a fine row
  compoundable    whether a violation is compoundable
  speed_limit     a km/h limit for (road_class, vehicle_category)
  road_rule       a per-rule fine + section on a road_class
  vehicle_spec    DL class / min age / engine cc / GVW for a vehicle class
  rto_prefix      state RTO prefix
  rto_code        district RTO code
  helpline        a phone number for a district/city
  coordinate      lat/lng of a city or road_point
  city_rule       a city-specific fine/zone/rule
  city_fact       municipal body, traffic police name, portal, population
  corridor_geom   corridor waypoint chain
"""
import json, hashlib, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
G = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())
NODES = G["nodes"]

claims = []


def cid(*parts):
    return "c_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def add(node_id, node_type, kind, field_path, assertion, value, jurisdiction, hints):
    claims.append({
        "claim_id": cid(node_id, field_path, assertion),
        "node_id": node_id,
        "node_type": node_type,
        "kind": kind,
        "field_path": field_path,
        "assertion": assertion,
        "value": value,
        "jurisdiction": jurisdiction,
        "search_hints": hints,
    })


def norm_section(s):
    """Normalise an MV Act section string into comparable tokens."""
    if not s:
        return []
    return re.findall(r"\d+[A-Z]*", str(s).upper())


# ---------------------------------------------------------------- violations
for nid, n in NODES.items():
    if n.get("type") != "violation":
        continue
    name = n.get("name")
    if n.get("mv_section"):
        add(nid, "violation", "legal_section", "mv_section",
            f"'{name}' is penalised under Motor Vehicles Act section {n['mv_section']}",
            n["mv_section"], "central",
            [f"Motor Vehicles Act section {n['mv_section']}", name])
    if not n.get("mv_section"):
        add(nid, "violation", "legal_section", "mv_section",
            f"'{name}' is presented as an enforceable offence but the graph cites "
            f"no Motor Vehicles Act section for it",
            None, "central", [f"{name} India legal basis penalty"])
    if n.get("cmvr_rule"):
        add(nid, "violation", "legal_section", "cmvr_rule",
            f"'{name}' is governed by Central Motor Vehicles Rules rule {n['cmvr_rule']}",
            n["cmvr_rule"], "central",
            [f"CMVR 1989 rule {n['cmvr_rule']}", name])
    if n.get("compoundable") is not None:
        add(nid, "violation", "compoundable", "compoundable",
            f"'{name}' is {'' if n['compoundable'] else 'not '}compoundable",
            n["compoundable"], "central",
            [f"compounding of offences {n.get('mv_section')} Motor Vehicles Act"])
    if n.get("irc_sign_ref"):
        add(nid, "violation", "legal_section", "irc_sign_ref",
            f"'{name}' references IRC {n['irc_sign_ref']}",
            n["irc_sign_ref"], "central", [f"IRC {n['irc_sign_ref']}"])

# --------------------------------------------------------------------- fines
for nid, n in NODES.items():
    if n.get("type") != "fine":
        continue
    vc = n.get("violation_code")
    vnode = NODES.get(f"vio:{vc}", {})
    vname = vnode.get("name", vc)
    sec = vnode.get("mv_section")
    scope = n.get("scope")
    st, ct = n.get("state_code"), n.get("city_code")
    if scope == "central":
        juris, jname = "central", "India (central)"
    elif ct:
        juris = f"city:{ct}"
        jname = NODES.get(f"city:{ct}", {}).get("name", ct)
    elif st:
        juris = f"state:{st}"
        jname = NODES.get(f"state:{st}", {}).get("name", st)
    else:
        juris, jname = scope or "unknown", scope or "unknown"

    vclass = n.get("vehicle_class")
    vsuffix = f" for vehicle class {vclass}" if vclass else ""

    for slot in ("first_offence", "repeat_offence"):
        amt = n.get(slot)
        if amt in (None, "", 0):
            continue
        label = "first offence" if slot == "first_offence" else "repeat offence"
        add(nid, "fine", "fine_amount", slot,
            f"In {jname}, '{vname}' carries a {label} fine of Rs {amt}{vsuffix}",
            amt, juris,
            [f"{vname} fine amount {jname}",
             f"Motor Vehicles Act section {sec} penalty Rs {amt}" if sec else f"{vname} penalty"])

    if n.get("imprisonment"):
        add(nid, "fine", "imprisonment", "imprisonment",
            f"In {jname}, '{vname}' carries imprisonment: {n['imprisonment']}",
            n["imprisonment"], juris,
            [f"Motor Vehicles Act section {sec} imprisonment" if sec else f"{vname} imprisonment"])

# --------------------------------------------------------------- road_class
for nid, n in NODES.items():
    if n.get("type") != "road_class":
        continue
    rname = n.get("name")
    for cat, lim in (n.get("speed_limits") or {}).items():
        if not isinstance(lim, (int, float)):
            continue
        add(nid, "road_class", "speed_limit", f"speed_limits.{cat}",
            f"Speed limit on '{rname}' for {cat} is {lim} km/h",
            lim, "central",
            [f"MoRTH speed limit notification {cat} {rname}",
             f"maximum speed limit India {rname} {cat}"])
    if isinstance(n.get("default_speed"), (int, float)):
        add(nid, "road_class", "speed_limit", "default_speed",
            f"Default speed limit on '{rname}' is {n['default_speed']} km/h",
            n["default_speed"], "central",
            [f"speed limit {rname} India notification"])
    for i, r in enumerate(n.get("key_rules") or []):
        if not isinstance(r, dict):
            continue
        add(nid, "road_class", "road_rule", f"key_rules[{i}]",
            f"On '{rname}': {r.get('rule')} — {r.get('section')} — fine {json.dumps(r.get('fine_inr'))}",
            r, "central",
            [f"{r.get('rule')} {r.get('section')} fine India"])

# ------------------------------------------------------------------ vehicles
for nid, n in NODES.items():
    if n.get("type") != "vehicle":
        continue
    vname = n.get("name")
    if n.get("dl_class"):
        add(nid, "vehicle", "vehicle_spec", "dl_class",
            f"'{vname}' requires driving licence class {n['dl_class']}",
            n["dl_class"], "central",
            [f"driving licence class {n['dl_class']} CMVR Form 4"])
    if n.get("min_age_years"):
        add(nid, "vehicle", "vehicle_spec", "min_age_years",
            f"Minimum age to drive '{vname}' is {n['min_age_years']} years",
            n["min_age_years"], "central",
            [f"Motor Vehicles Act section 4 minimum age {n['min_age_years']}"])
    for f in ("engine_cc_max", "gvw_kg_max"):
        if n.get(f):
            add(nid, "vehicle", "vehicle_spec", f,
                f"'{vname}' has {f.replace('_', ' ')} of {n[f]}",
                n[f], "central", [f"CMVR vehicle category {vname} {f}"])

# -------------------------------------------------------------------- states
for nid, n in NODES.items():
    if n.get("type") != "state":
        continue
    sname = n.get("name")
    if n.get("rto_prefix"):
        add(nid, "state", "rto_prefix", "rto_prefix",
            f"The RTO registration prefix for {sname} is {n['rto_prefix']}",
            n["rto_prefix"], f"state:{n.get('code')}",
            [f"{sname} RTO code {n['rto_prefix']} vehicle registration"])
    if n.get("capital"):
        add(nid, "state", "city_fact", "capital",
            f"The capital of {sname} is {n['capital']}",
            n["capital"], f"state:{n.get('code')}", [f"{sname} capital city"])
    if n.get("multiplier") is not None:
        add(nid, "state", "fine_amount", "multiplier",
            f"{sname} applies a fine multiplier of {n['multiplier']} to central fines",
            n["multiplier"], f"state:{n.get('code')}",
            [f"{sname} motor vehicles amendment fine notification"])

# ----------------------------------------------------------------- districts
for nid, n in NODES.items():
    if n.get("type") != "district":
        continue
    dname, sname = n.get("name"), n.get("state_name")
    for code in (n.get("rto_codes") or []):
        add(nid, "district", "rto_code", "rto_codes",
            f"RTO code {code} corresponds to {dname} district, {sname}",
            code, f"state:{n.get('state_code')}",
            [f"RTO code {code} {dname} {sname}"])
    hl = n.get("traffic_helpline")
    if hl:
        add(nid, "district", "helpline", "traffic_helpline",
            f"The traffic helpline for {dname}, {sname} is {hl}",
            hl, f"state:{n.get('state_code')}",
            [f"{dname} traffic police helpline number {sname}"])
    for k, v in (n.get("emergency_numbers") or {}).items():
        add(nid, "district", "helpline", f"emergency_numbers.{k}",
            f"The {k.replace('_', ' ')} number for {dname}, {sname} is {v}",
            v, f"state:{n.get('state_code')}",
            [f"India {k.replace('_', ' ')} number {v}"])

# -------------------------------------------------------------------- cities
for nid, n in NODES.items():
    if n.get("type") != "city":
        continue
    cname = n.get("name")
    sc = n.get("state_code")
    sname = NODES.get(f"state:{sc}", {}).get("name", sc)
    if n.get("lat") is not None and n.get("lng") is not None:
        add(nid, "city", "coordinate", "lat,lng",
            f"{cname}, {sname} is located at {n['lat']}, {n['lng']}",
            [n["lat"], n["lng"]], f"state:{sc}",
            [f"{cname} {sname} latitude longitude"])
    for f, tmpl in (
        ("municipal_body", "The municipal body of {c} is {v}"),
        ("traffic_police", "The traffic police force for {c} is {v}"),
        ("e_challan_portal", "The e-challan portal for {c} is {v}"),
        ("population_est", "The estimated population of {c} is {v}"),
    ):
        if n.get(f):
            add(nid, "city", "city_fact", f,
                tmpl.format(c=f"{cname}, {sname}", v=n[f]),
                n[f], f"city:{n.get('code')}", [f"{cname} {f.replace('_', ' ')}"])
    if n.get("has_ai_cameras") is not None or n.get("has_anpr") is not None:
        add(nid, "city", "city_fact", "enforcement_tech",
            f"{cname} enforcement tech: AI cameras={n.get('has_ai_cameras')}, ANPR={n.get('has_anpr')}",
            {"ai": n.get("has_ai_cameras"), "anpr": n.get("has_anpr")},
            f"city:{n.get('code')}", [f"{cname} ANPR AI traffic camera enforcement"])
    if n.get("traffic_helpline"):
        add(nid, "city", "helpline", "traffic_helpline",
            f"The traffic helpline for {cname} is {n['traffic_helpline']}",
            n["traffic_helpline"], f"city:{n.get('code')}",
            [f"{cname} traffic police helpline"])
    for grp in ("city_specific_rules", "key_fines_2025", "parking_zones",
                "air_quality_rules", "notable_violations_high_fine"):
        blk = n.get(grp)
        if not isinstance(blk, dict):
            continue
        for k, v in blk.items():
            add(nid, "city", "city_rule", f"{grp}.{k}",
                f"In {cname}: {k.replace('_', ' ')} = {json.dumps(v, ensure_ascii=False)}",
                v, f"city:{n.get('code')}",
                [f"{cname} {k.replace('_', ' ')} traffic fine"])
    for i, h in enumerate(n.get("accident_hotspots") or []):
        add(nid, "city", "city_fact", f"accident_hotspots[{i}]",
            f"{json.dumps(h, ensure_ascii=False)} is a listed accident hotspot in {cname}",
            h, f"city:{n.get('code')}", [f"{cname} accident black spot"])

# --------------------------------------------------- road_points / corridors
for nid, n in NODES.items():
    t = n.get("type")
    if t == "road_point":
        add(nid, "road_point", "coordinate", "lat,lng",
            f"Road point '{n.get('name')}' is at {n.get('lat')}, {n.get('lng')}",
            [n.get("lat"), n.get("lng")], f"city:{n.get('city_code')}",
            [f"{n.get('name')} road location coordinates"])
    elif t == "corridor":
        wp = n.get("waypoints") or []
        add(nid, "corridor", "corridor_geom", "waypoints",
            f"Corridor '{n.get('name')}' ({n.get('road_class')}) traced by {len(wp)} waypoints "
            f"from {wp[0] if wp else None} to {wp[-1] if wp else None}",
            wp, "central", [f"{n.get('name')} expressway highway route alignment"])

out = ROOT / "data" / "audit" / "claims.json"
out.write_text(json.dumps(claims, ensure_ascii=False, indent=1))

import collections
print(f"claims: {len(claims)}")
print("by kind:", dict(collections.Counter(c["kind"] for c in claims).most_common()))
print("by node_type:", dict(collections.Counter(c["node_type"] for c in claims).most_common()))
dupes = len(claims) - len({c["claim_id"] for c in claims})
print("duplicate claim_ids:", dupes)
