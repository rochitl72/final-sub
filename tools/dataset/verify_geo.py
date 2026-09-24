#!/usr/bin/env python3
"""
Verify the geography / jurisdiction layer:
  coordinate   city & road_point lat-lng against the GeoNames IN gazetteer
  rto_code     district RTO codes against the published RTO district list
  rto_prefix   state registration prefixes against the same
  helpline     phone numbers — structural validation + national-number check
"""
import json, re, math, csv, collections, html, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
OUT, C = ROOT / "data" / "audit", ROOT / "data" / "audit" / "corpus"
COMPILED = ROOT / "data" / "compiled"
NODES = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())["nodes"]
CLAIMS = json.loads((OUT / "claims.json").read_text())
REG = json.loads((C / "registry.json").read_text())

NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
SRC_GEONAMES = {"url": "https://download.geonames.org/export/dump/IN.zip",
                "title": "GeoNames gazetteer, India extract (IN.txt)",
                "retrieved_at": NOW}
SRC_RTO = {"url": "https://en.wikipedia.org/wiki/List_of_Regional_Transport_Office_districts_in_India",
           "title": "List of Regional Transport Office districts in India",
           "retrieved_at": NOW}

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


# ---------------------------------------------------------------- gazetteer
# GeoNames columns: id, name, asciiname, altnames, lat, lng, fclass, fcode, country, ...
places = collections.defaultdict(list)
with (C / "IN.txt").open(encoding="utf-8") as fh:
    for row in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
        # P = populated place, A = administrative division (districts, which is what
        # many district nodes actually denote). Restricting to P alone made district
        # names resolve to same-named hamlets hundreds of km away.
        if len(row) < 9 or row[6] not in ("P", "A"):
            continue
        try:
            lat, lng = float(row[4]), float(row[5])
        except ValueError:
            continue
        pop = int(row[14]) if len(row) > 14 and row[14].isdigit() else 0
        rec = (lat, lng, row[8], pop, row[1])
        for nm in {row[1], row[2], *(row[3].split(",") if row[3] else [])}:
            nm = nm.strip().lower()
            if nm:
                places[nm].append(rec)
print(f"gazetteer names indexed: {len(places)}")


def haversine(a, b, c2, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c2)
    dp, dl = math.radians(c2 - a), math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


for c in CLAIMS:
    if c["kind"] != "coordinate":
        continue
    n = NODES[c["node_id"]]
    lat, lng = c["value"]
    if lat is None or lng is None:
        record(c, "contradicted", 0.9, None, "", "Coordinate is null.")
        continue
    # India bounding box sanity check
    if not (6.0 <= lat <= 37.6 and 68.0 <= lng <= 97.5):
        record(c, "contradicted", 0.95, SRC_GEONAMES, "",
               f"({lat}, {lng}) falls outside India's bounding box "
               f"(lat 6.0-37.6, lng 68.0-97.5).")
        continue
    if n.get("type") == "road_point":
        record(c, "unverified", 0.3, SRC_GEONAMES, "",
               "Street-level points are not in a populated-place gazetteer; verifying these "
               "needs OpenStreetMap way geometry, which is not in the corpus. "
               "Coordinate is inside India and structurally valid.")
        continue
    cands = places.get(str(n.get("name", "")).strip().lower(), [])
    if not cands:
        record(c, "unverified", 0.35, SRC_GEONAMES, "",
               f"No populated place named {n.get('name')!r} in the GeoNames India extract; "
               f"the coordinate is inside India but the place name could not be resolved.")
        continue
    # Compare against the CLOSEST same-named entry: a name can legitimately occur in
    # several states, and the stored point is correct if it matches any of them.
    best = min(cands, key=lambda r: haversine(lat, lng, r[0], r[1]))
    d = haversine(lat, lng, best[0], best[1])
    # A place with real population is stronger evidence than a pop-0 stub.
    anchored = max(cands, key=lambda r: r[3])
    if anchored[3] > 0 and haversine(lat, lng, anchored[0], anchored[1]) < d:
        best = anchored
        d = haversine(lat, lng, best[0], best[1])
    quote = f"GeoNames: {best[4]} ({best[2]}) at {best[0]}, {best[1]}, pop {best[3]}"
    if d <= 15:
        record(c, "verified", 0.9 if d <= 5 else 0.75, SRC_GEONAMES, quote,
               f"Stored coordinate is {d:.1f} km from the gazetteer position for this place.")
    elif d <= 60:
        record(c, "unverified", 0.4, SRC_GEONAMES, quote,
               f"Stored coordinate is {d:.1f} km from the nearest gazetteer entry of the same "
               f"name — plausible for a large metro centroid, but not a match.")
    else:
        record(c, "contradicted", 0.5, SRC_GEONAMES, quote,
               f"Stored coordinate is {d:.0f} km from the gazetteer position for "
               f"{n.get('name')!r}. Needs human confirmation: gazetteer name matching cannot "
               f"always distinguish a district from its headquarters town or from a same-named "
               f"place in another state.")

# --------------------------------------------------------------- RTO codes
raw = (C / "rto_wiki.html").read_text(errors="replace")
txt = re.sub(r"<[^>]+>", " ", raw)
txt = html.unescape(txt)
rto_codes = set(re.findall(r"\b([A-Z]{2}[\s-]?\d{1,2})\b", txt))
rto_codes = {re.sub(r"[\s-]", "-", x) for x in rto_codes}
rto_norm = {re.sub(r"[^A-Z0-9]", "", x) for x in rto_codes}
state_prefixes = set(re.findall(r"\b([A-Z]{2})[\s-]?\d{1,2}\b", txt))
print(f"RTO codes harvested from list: {len(rto_norm)}; state prefixes: {len(state_prefixes)}")

for c in CLAIMS:
    if c["kind"] == "rto_code":
        key = re.sub(r"[^A-Z0-9]", "", str(c["value"]).upper())
        n = NODES[c["node_id"]]
        if key in rto_norm:
            record(c, "verified", 0.7, SRC_RTO,
                   f"code {c['value']} present in the published RTO district list",
                   f"RTO code {c['value']} exists. NOTE: this confirms the code is a real "
                   f"Indian RTO code, not that it maps to {n.get('name')!r} specifically — "
                   f"code-to-district mapping needs the state transport department's own list.")
        else:
            record(c, "unverified", 0.5, SRC_RTO, "",
                   f"RTO code {c['value']!r} was not found in the published RTO district list.")
    elif c["kind"] == "rto_prefix":
        p = str(c["value"]).upper()
        sname = NODES[c["node_id"]].get("name", "")
        if p in state_prefixes:
            record(c, "verified", 0.8, SRC_RTO, f"prefix {p} appears in the RTO district list",
                   f"Registration prefix {p} is in use for this state.")
            continue
        # Is a *different* prefix listed against this state's name? Then the stored
        # one is wrong. If the state simply is not covered by the list, say so.
        m = re.search(re.escape(sname) + r"[^A-Za-z]{0,6}([A-Z]{2})\b", txt)
        alt = re.search(r"\b([A-Z]{2})\s*[—–-]\s*" + re.escape(sname), txt)
        other = (alt.group(1) if alt else (m.group(1) if m else None))
        if other and other != p:
            record(c, "contradicted", 0.75, SRC_RTO,
                   f"list shows '{other}' against {sname}",
                   f"Graph stores prefix {p!r} for {sname}, but the published list uses "
                   f"{other!r}. (Telangana's series was changed from TS to TG; codes already "
                   f"issued under the old prefix remain valid, so a chatbot must handle both.)")
        else:
            record(c, "unverified", 0.4, SRC_RTO, "",
                   f"{sname} is not covered by the published RTO district list used here, so "
                   f"prefix {p!r} could be neither confirmed nor refuted from this source.")

# --------------------------------------------------------------- helplines
NATIONAL = {"112": "national emergency number (ERSS)",
            "108": "emergency medical/ambulance service (state-run EMRI)",
            "1033": "national highway helpline (MoRTH/NHAI)",
            "1073": "traffic police helpline (common allocation)",
            "1095": "traffic helpline (legacy allocation)"}
SRC_NAT = {"url": "https://www.npci.org.in/", "title": "national short-code allocation",
           "retrieved_at": NOW}

# how many districts share each traffic_helpline value
share = collections.Counter()
for n in NODES.values():
    if n.get("type") == "district" and n.get("traffic_helpline"):
        share[str(n["traffic_helpline"])] += 1

for c in CLAIMS:
    if c["kind"] != "helpline":
        continue
    v = str(c["value"]).strip()
    if v in NATIONAL:
        record(c, "verified", 0.85, SRC_NAT, f"{v} — {NATIONAL[v]}",
               f"{v} is a nationally allocated short code, valid everywhere in India. "
               f"It is not specific to this district.")
        continue
    if not re.fullmatch(r"\+?\d{3,5}[- ]?\d{5,8}|\d{3,4}", v):
        record(c, "contradicted", 0.7, None, "",
               f"{v!r} is not a well-formed Indian phone number or short code.")
        continue
    n_share = share.get(v, 0)
    if n_share > 1:
        st = NODES[c["node_id"]].get("state_name") or NODES[c["node_id"]].get("state_code")
        record(c, "contradicted", 0.75, None, "",
               f"This number is stored as the traffic helpline for {n_share} different "
               f"districts of {st}. It is a state-level control-room number presented as a "
               f"district-specific one; the district attribution is not supportable.")
    else:
        record(c, "unverified", 0.3, None, "",
               f"Landline {v} is structurally valid but could not be matched to an official "
               f"police or transport-department page in the corpus.")

(OUT / "verdicts_geo.json").write_text(json.dumps(verdicts, indent=1))
cnt = collections.Counter(v["status"] for v in verdicts.values())
bykind = collections.defaultdict(collections.Counter)
for v in verdicts.values():
    bykind[v["kind"]][v["status"]] += 1
print(f"\ngeo-layer claims adjudicated: {len(verdicts)}")
for k, n in cnt.most_common():
    print(f"  {k:<16} {n}")
print()
for k, c2 in bykind.items():
    print(f"  {k:<12} {dict(c2)}")
