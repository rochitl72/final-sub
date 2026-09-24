#!/usr/bin/env python3
"""
Structural audit of drivelegal_graph.json — defects that exist independently of
provenance. These are the ones a human review team cannot fix by adding a URL.
"""
import json, collections, re
from pathlib import Path

G = json.loads((Path(__file__).parent / "graph.json").read_text())
N, E, IDX, SP = G["nodes"], G["edges"], G["indexes"], G["spatial"]
byt = collections.defaultdict(dict)
for nid, n in N.items():
    byt[n.get("type")][n.get("code", nid)] = n

F = [n for n in N.values() if n.get("type") == "fine"]
V = {n["code"]: n for n in N.values() if n.get("type") == "violation"}
CITY = {n["code"]: n for n in N.values() if n.get("type") == "city"}
ST = {n["code"]: n for n in N.values() if n.get("type") == "state"}
DIS = [n for n in N.values() if n.get("type") == "district"]

R = {}


def sec(t):
    print(f"\n{'='*74}\n{t}\n{'='*74}")


# ------------------------------------------------ 1. referential integrity
sec("1. REFERENTIAL INTEGRITY")
dang = collections.Counter()
for e in E:
    s, t = (e[0], e[1]) if isinstance(e, list) else (e.get("s"), e.get("t"))
    if s not in N:
        dang["edge source missing"] += 1
    if t not in N:
        dang["edge target missing"] += 1
for f in F:
    if f.get("violation_code") not in V:
        dang["fine -> unknown violation"] += 1
    if f.get("state_code") and f["state_code"] not in ST:
        dang["fine -> unknown state"] += 1
    if f.get("city_code") and f["city_code"] not in CITY:
        dang["fine -> unknown city"] += 1
for c in CITY.values():
    if c.get("state_code") not in ST:
        dang["city -> unknown state"] += 1
for d in DIS:
    if d.get("state_code") not in ST:
        dang["district -> unknown state"] += 1
    if d.get("city_override") and d["city_override"] not in CITY:
        dang["district.city_override -> unknown city"] += 1
print("dangling references:", dict(dang) or "none")
R["dangling"] = dict(dang)

# --------------------------------------- 2. ambiguous / conflicting fines
sec("2. FINE RESOLUTION — CONFLICTS AND AMBIGUITY")
key = collections.defaultdict(list)
for f in F:
    key[(f.get("violation_code"), f.get("scope"), f.get("state_code"),
         f.get("city_code"), f.get("vehicle_class"))].append(f)
dupes = {k: v for k, v in key.items() if len(v) > 1}
conflict = {k: v for k, v in dupes.items()
            if len({(x.get("first_offence"), x.get("repeat_offence")) for x in v}) > 1}
print(f"fine rows: {len(F)}")
print(f"duplicate lookup keys: {len(dupes)}")
print(f"  of which CONFLICT (same key, different amounts): {len(conflict)}")
for k, v in list(conflict.items())[:6]:
    print(f"    {k} -> {[(x.get('first_offence'), x.get('repeat_offence')) for x in v]}")
R["fine_conflicts"] = len(conflict)

# precedence: is there a deterministic winner when city/state/central all match?
overlap = collections.Counter()
for vc in V:
    sc = {f.get("scope") for f in F if f.get("violation_code") == vc}
    overlap[tuple(sorted(x for x in sc if x))] += 1
print("\nscope combinations present per violation:")
for k, n in overlap.most_common():
    print(f"  {k or '(none)'}: {n} violations")
print("\nNOTE: the graph encodes no precedence rule. Nothing in the data says whether a")
print("city row overrides a state row overrides central. That is implicit in code.")

# ------------------------------------------------------- 3. coverage gaps
sec("3. COVERAGE GAPS")
vio_with_fine = {f.get("violation_code") for f in F}
nofine = [c for c in V if c not in vio_with_fine]
print(f"violations with NO fine row at all: {len(nofine)}")
for c in nofine[:8]:
    print(f"    {c}: {V[c].get('name')}")
st_fines = collections.Counter(f.get("state_code") for f in F if f.get("state_code"))
print(f"\nstates with at least one state-scope fine: {len(st_fines)} / {len(ST)}")
print(f"median state fine rows: {sorted(st_fines.values())[len(st_fines)//2]}")
print(f"min/max: {min(st_fines.values())} / {max(st_fines.values())}")
enriched = [c for c in CITY.values() if c.get("municipal_body")]
print(f"\ncities with any enrichment: {len(enriched)} / {len(CITY)} "
      f"({100*len(enriched)/len(CITY):.0f}%)")
print(f"cities with a city-scope fine row: "
      f"{len({f.get('city_code') for f in F if f.get('city_code')})} / {len(CITY)}")
R["violations_without_fine"] = len(nofine)

# ------------------------------------------- 4. index / spatial integrity
sec("4. INDEX AND SPATIAL INTEGRITY")
print(f"spatial.cities entries: {len(SP['cities'])}  vs  city nodes: {len(CITY)}")
missing_sp = len(CITY) - len(SP["cities"])
print(f"  -> {missing_sp} city nodes are NOT in the spatial index: "
      f"location lookup cannot reach them")
fbv = IDX["fine_by_violation"]
print(f"\nfine_by_violation keys: {len(fbv)}  vs  violations: {len(V)}")
bad_idx = [k for k in fbv if k not in V]
print(f"  index keys not matching any violation: {len(bad_idx)}")
tot_idx = sum(len(x) for x in fbv.values())
print(f"  fine ids referenced by index: {tot_idx}  vs  fine nodes: {len(F)}")
dbs = IDX["districts_by_state"]
print(f"\ndistricts_by_state: {sum(len(x) for x in dbs.values())} entries vs "
      f"{len(DIS)} district nodes")
R["cities_missing_from_spatial"] = missing_sp

# ------------------------------------------- 5. keyword routing ambiguity
sec("5. KEYWORD ROUTING — THE CHATBOT'S ENTRY POINT")
k2v = IDX["keyword_to_vio"]
multi = {k: v for k, v in k2v.items() if isinstance(v, list) and len(v) > 1}
print(f"keyword_to_vio entries: {len(k2v)}")
print(f"  keywords mapping to MORE THAN ONE violation: {len(multi)}")
for k, v in list(multi.items())[:5]:
    print(f"    {k!r} -> {v}")
covered = set()
for v in k2v.values():
    covered.update(v if isinstance(v, list) else [v])
print(f"\nviolations reachable by ANY keyword: {len(covered & set(V))} / {len(V)}")
unreachable = sorted(set(V) - covered)
print(f"violations UNREACHABLE by keyword lookup: {len(unreachable)}")
for c in unreachable[:8]:
    print(f"    {c}: {V[c].get('name')}")
R["unreachable_violations"] = len(unreachable)

# ------------------------------------------------ 6. type / schema hygiene
sec("6. TYPE AND SCHEMA CONSISTENCY")
tally = collections.Counter()
for f in F:
    for fld in ("first_offence", "repeat_offence"):
        tally[(fld, type(f.get(fld)).__name__)] += 1
for k, n in sorted(tally.items()):
    print(f"  fine.{k[0]:<16} {k[1]:<8} {n}")
rule_types = collections.Counter()
for rc in [n for n in N.values() if n.get("type") == "road_class"]:
    for r in rc.get("key_rules") or []:
        rule_types[type(r.get("fine_inr")).__name__] += 1
        if isinstance(r.get("fine_inr"), dict):
            for kk, vv in r["fine_inr"].items():
                rule_types[f"  dict.{kk}:{type(vv).__name__}"] += 1
print("\n  road_class.key_rules[].fine_inr value types:")
for k, n in sorted(rule_types.items()):
    print(f"    {k:<24} {n}")
print("\n  -> a money field that is sometimes int, sometimes str ('Local'), sometimes a")
print("     nested dict cannot be compared, summed, or safely formatted without casing.")

# ----------------------------------------------- 7. temporal / versioning
sec("7. TEMPORAL VALIDITY — THE BIGGEST NON-PROVENANCE DEFECT")
blob = json.dumps(G)
for probe in ("effective_date", "effective_from", "valid_from", "valid_until",
              "as_of", "amended", "version_date", "superseded", "last_updated"):
    print(f"  {probe:<16} occurrences: {blob.count(probe)}")
print(f"\n  meta: {json.dumps(G['meta'])[:120]}")
print("\n  -> Not one legal fact carries an effective date. The 2019 amendment changed")
print("     most penalties; Telangana changed its RTO series in 2024. The dataset cannot")
print("     express 'this was the fine before X and this after', cannot answer about a")
print("     challan issued last year, and cannot be diffed when the law next changes.")

# --------------------------------------------- 8. null density / emptiness
sec("8. EMPTY AND PLACEHOLDER FIELDS")
for t, sample in (("city", CITY.values()), ("violation", V.values())):
    empt = collections.Counter()
    tot = 0
    for n in sample:
        tot += 1
        for k, v in n.items():
            if v in (None, "", [], {}):
                empt[k] += 1
    print(f"\n  {t} ({tot} nodes) — fields empty in >30% of nodes:")
    for k, c in empt.most_common():
        if c / tot > 0.30:
            print(f"    {k:<32} empty in {c}/{tot} ({100*c/tot:.0f}%)")

print("\n" + "="*74)
print("SUMMARY:", json.dumps(R))
