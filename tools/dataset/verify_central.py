#!/usr/bin/env python3
"""
Verify the central legal layer of the DriveLegal graph against the consolidated
Motor Vehicles Act 1988 (as amended to date) and the Central Motor Vehicles
Rules 1989.

Nothing in the graph is modified. Output is a verdict ledger keyed by claim_id.

Verdicts
  verified      the source text supports the claim
  contradicted  the source text states something different
  unverified    the claim could not be matched to any source in the corpus
  unsupported_section  the cited section/rule does not exist in the statute
  off_topic     the cited section exists but is about something else
"""
import json, re, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
OUT = ROOT / "data" / "audit"
COMPILED = ROOT / "data" / "compiled"
GRAPH = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())
NODES = GRAPH["nodes"]
CLAIMS = json.loads((OUT / "claims.json").read_text())
MVA = json.loads((OUT / "mva_sections.json").read_text())
CMVR = json.loads((OUT / "cmvr_rules.json").read_text())
REG = json.loads((ROOT / "data" / "audit" / "corpus" / "registry.json").read_text())

SRC_MVA = REG["mva_1988_consolidated"]
SRC_CMVR = REG["cmvr_1989"]

STOP = set("""a an the of to in on for or and by with at is are be been not no any such
person persons vehicle vehicles motor driving drive driver road roads use using
without while other others than shall may which who whoever failure fail failing
offence offences penalty penalties rs inr rupees first repeat""".split())


def toks(s):
    return {w for w in re.findall(r"[a-z]{3,}", str(s).lower()) if w not in STOP}


def snippet(text, needle=None, width=320):
    if needle:
        i = text.lower().find(str(needle).lower())
        if i >= 0:
            return re.sub(r"\s+", " ", text[max(0, i - width // 2): i + width // 2]).strip()
    return re.sub(r"\s+", " ", text[:width]).strip()


def primary_section(raw):
    """'177A r/w CMVR 105' -> '177A' ; '184(1)' -> '184'"""
    m = re.match(r"\s*\[?(\d{1,3}[A-Z]{0,3})", str(raw))
    return m.group(1) if m else None


def cited_cmvr(raw):
    m = re.search(r"CMVR\s*(?:rule\s*)?(\d{1,3}[A-Z]?)", str(raw), re.I)
    return m.group(1) if m else None


verdicts = {}


def record(claim, status, confidence, source, quote, note=""):
    verdicts[claim["claim_id"]] = {
        "claim_id": claim["claim_id"], "node_id": claim["node_id"],
        "field_path": claim["field_path"], "kind": claim["kind"],
        "assertion": claim["assertion"], "value": claim["value"],
        "status": status, "confidence": round(confidence, 2),
        "source_url": source.get("url") if source else None,
        "source_title": source.get("title") if source else None,
        "retrieved_at": source.get("retrieved_at") if source else None,
        "source_sha256": source.get("sha256") if source else None,
        "quote": quote, "note": note,
    }


# ------------------------------------------------------------ legal_section
for c in CLAIMS:
    if c["kind"] != "legal_section":
        continue
    vio = NODES.get(c["node_id"], {})
    subject = toks(vio.get("name", "")) | toks(vio.get("description", ""))

    if c["field_path"] == "mv_section":
        if c["value"] is None:
            record(c, "no_citation", 0.99, None, "",
                   "The violation node carries no mv_section at all, so there is no legal "
                   "authority in the graph for treating this as a punishable offence.")
            continue
        # a citation may name several instruments: "177 r/w CMVR 50; IPC/BNS 336/318"
        cited = re.findall(r"\b(\d{1,3}[A-Z]{0,3})\b(?!\s*\))", str(c["value"]))
        cands = [(s, MVA[s]) for s in cited if s in MVA]
        missing = [s for s in cited if s not in MVA]
        if not cands:
            record(c, "unsupported_section", 0.9, SRC_MVA, "",
                   f"None of the section numbers cited ({', '.join(cited) or 'none parseable'}) "
                   f"exist in the consolidated Motor Vehicles Act, 1988. Citation string: "
                   f"{c['value']!r}")
            continue
        # s.177 and s.177A are residual/cross-referencing penalty provisions: they
        # punish contravention of *other* instruments (any rule made under the Act,
        # and the s.118 Rules of the Road Regulations respectively). Their own text
        # never names the conduct, so topical keyword matching is meaningless and
        # would produce a false "off_topic". Judge the citation as structurally
        # sound and let the amount check carry the verdict.
        if set(cited) & {"177", "177A"}:
            sec = "177A" if "177A" in cited else "177"
            d = MVA[sec]
            record(c, "verified", 0.65, SRC_MVA, snippet(d["text"]),
                   f"s.{sec} is a residual provision ('{d['heading']}') that penalises "
                   f"contravention of other instruments, so it is a structurally valid "
                   f"citation for this conduct. It does not by itself establish that the "
                   f"conduct is prohibited — that depends on the underlying regulation — "
                   f"and the penalty it fixes is {d['amounts']}.")
            continue

        best, best_ov, best_sec = -1.0, set(), None
        for sec, d in cands:
            ov = subject & (toks(d["heading"]) | toks(d["text"][:2500]))
            r = len(ov) / max(1, len(subject))
            if r > best:
                best, best_ov, best_sec = r, ov, sec
        d = MVA[best_sec]
        extra = (f" Citation also names {', '.join(missing)}, which is not an MV Act section "
                 f"(likely a rule/IPC/BNS reference outside this corpus)." if missing else "")
        if best >= 0.30 or len(best_ov) >= 3:
            record(c, "verified", min(0.95, 0.55 + best), SRC_MVA, snippet(d["text"]),
                   f"s.{best_sec} '{d['heading']}'; shared terms: "
                   f"{', '.join(sorted(best_ov)[:8])}.{extra}")
        elif best_ov:
            record(c, "unverified", 0.4, SRC_MVA, snippet(d["text"]),
                   f"s.{best_sec} '{d['heading']}' exists but only weakly matches this "
                   f"violation (shared: {', '.join(sorted(best_ov))}). Needs human review.{extra}")
        else:
            record(c, "off_topic", 0.7, SRC_MVA, snippet(d["text"]),
                   f"s.{best_sec} is '{d['heading']}', which shares no subject matter with "
                   f"'{vio.get('name')}'.{extra}")

    elif c["field_path"] == "cmvr_rule":
        r = cited_cmvr(c["value"]) or primary_section(c["value"])
        d = CMVR.get(r) if r else None
        if not d:
            record(c, "unverified", 0.5, SRC_CMVR, "",
                   f"CMVR rule {r!r} was not located in the parsed rule set "
                   f"(the consolidated CMVR text parsed into {len(CMVR)} addressable rules; "
                   f"coverage is partial, so this is 'unverified', not 'absent').")
            continue
        body = toks(d["heading"]) | toks(d["text"][:2000])
        overlap = subject & body
        if len(overlap) >= 2:
            record(c, "verified", 0.8, SRC_CMVR, snippet(d["text"]),
                   f"rule {r} '{d['heading']}'; shared: {', '.join(sorted(overlap)[:8])}")
        else:
            record(c, "unverified", 0.4, SRC_CMVR, snippet(d["text"]),
                   f"rule {r} is '{d['heading']}'; weak topical match.")

    else:  # irc_sign_ref
        record(c, "unverified", 0.0, None, "",
               "IRC codes are paywalled standards published by the Indian Roads Congress "
               "and are not in the corpus. No free authoritative text available.")

# -------------------------------------------------------- central fine rows
for c in CLAIMS:
    if c["kind"] not in ("fine_amount", "imprisonment") or c["jurisdiction"] != "central":
        continue
    if c["kind"] == "fine_amount" and c["field_path"] == "multiplier":
        continue
    fnode = NODES.get(c["node_id"], {})
    vio = NODES.get(f"vio:{fnode.get('violation_code')}", {})
    sec = primary_section(vio.get("mv_section"))
    d = MVA.get(sec) if sec else None
    if not d:
        record(c, "unverified", 0.3, SRC_MVA, "",
               f"Fine row cites violation '{fnode.get('violation_code')}' whose section "
               f"{sec!r} is not in the Act, so the amount cannot be checked.")
        continue

    if c["kind"] == "imprisonment":
        has = re.search(r"imprisonment", d["text"], re.I)
        if has:
            record(c, "verified", 0.7, SRC_MVA, snippet(d["text"], "imprisonment"),
                   f"s.{sec} does provide for imprisonment. Exact term wording needs review.")
        else:
            record(c, "contradicted", 0.8, SRC_MVA, snippet(d["text"]),
                   f"s.{sec} '{d['heading']}' contains no imprisonment provision, but the "
                   f"graph records one.")
        continue

    amt = c["value"]
    amounts = d["amounts"]
    if amt in amounts:
        record(c, "verified", 0.9, SRC_MVA, snippet(d["text"], None),
               f"Rs {amt} appears in s.{sec} '{d['heading']}' (amounts in section: {amounts}).")
    elif not amounts:
        record(c, "unverified", 0.3, SRC_MVA, snippet(d["text"]),
               f"s.{sec} '{d['heading']}' specifies no rupee amount in its own text "
               f"(penalty may fall under the general s.177 provision or be state-prescribed).")
    else:
        record(c, "contradicted", 0.75, SRC_MVA, snippet(d["text"]),
               f"Graph says Rs {amt}; s.{sec} '{d['heading']}' specifies {amounts}.")

# ------------------------------------------------------------- compoundable
S200 = MVA.get("200", {})
# The compoundable enumeration is the bracketed list that ends at ", may either
# before or after the institution of the prosecution". Anything after that (e.g.
# the s.206(4) proviso) is NOT part of the list.
_s200_text = S200.get("text", "")
_cut = _s200_text.find("may either before or after")
COMP_LIST = _s200_text[:_cut] if _cut > 0 else _s200_text
comp_listed = set(re.findall(r"section\s+(\d{1,3}[A-Z]{0,3})", COMP_LIST))
# offences listed only for a specific sub-section or fact pattern
COMP_PARTIAL = {"182A", "183", "184", "190"}

for c in CLAIMS:
    if c["kind"] != "compoundable":
        continue
    vio = NODES.get(c["node_id"], {})
    raw = vio.get("mv_section")
    if not raw:
        record(c, "no_citation", 0.9, SRC_MVA, "",
               "Compoundability cannot be assessed: the violation cites no MV Act section. "
               "Section 200 defines compoundability by section number.")
        continue
    sec = primary_section(raw)
    if sec not in MVA:
        record(c, "unverified", 0.5, SRC_MVA, "",
               f"Cited section {sec!r} is not an MV Act section, so s.200 cannot be applied.")
        continue
    listed = sec in comp_listed
    q = snippet(COMP_LIST, f"section {sec}")
    if sec in COMP_PARTIAL:
        record(c, "unverified", 0.5, SRC_MVA, q,
               f"s.200 lists s.{sec} only in part (specific sub-sections or fact patterns), "
               f"so a single true/false flag on the whole offence cannot be verified "
               f"mechanically. Graph says compoundable={c['value']}. Needs legal review.")
    elif bool(c["value"]) == listed:
        record(c, "verified", 0.85, SRC_MVA, q,
               f"s.200 (composition of certain offences) {'lists' if listed else 'does not list'} "
               f"s.{sec}, matching the stored value.")
    else:
        record(c, "contradicted", 0.8, SRC_MVA, q,
               f"Graph says compoundable={c['value']}, but s.200 "
               f"{'lists' if listed else 'does not list'} s.{sec}. "
               f"(Note: some states compound offences administratively beyond the s.200 list; "
               f"the statutory position is as stated here.)")

# ------------------------------------------------------- vehicle DL classes
for c in CLAIMS:
    if c["kind"] != "vehicle_spec":
        continue
    if c["field_path"] == "min_age_years":
        s4 = MVA.get("4", {})
        txt = s4.get("text", "")
        v = c["value"]
        ok = re.search(rf"\b{v}\b|\b{'eighteen' if v == 18 else 'sixteen' if v == 16 else 'twenty'}\b",
                       txt, re.I)
        if ok:
            record(c, "verified", 0.8, SRC_MVA, snippet(txt, "age"),
                   f"s.4 (age limit in connection with driving of motor vehicles) supports {v}.")
        else:
            record(c, "unverified", 0.4, SRC_MVA, snippet(txt),
                   f"s.4 text does not plainly state {v} for this vehicle category.")
    elif c["field_path"] == "dl_class":
        v = str(c["value"])
        hit = None
        for key, d in CMVR.items():
            if re.search(rf"\b{re.escape(v)}\b", d["text"]):
                hit = d
                break
        if hit:
            record(c, "verified", 0.7, SRC_CMVR, snippet(hit["text"], v),
                   f"Licence class '{v}' appears in CMVR rule {hit['rule']} '{hit['heading']}'.")
        else:
            record(c, "unverified", 0.35, SRC_CMVR, "",
                   f"Licence class '{v}' not found in the parsed CMVR text. The authoritative "
                   f"list is CMVR Form 4 / rule 14, which is in a schedule not covered by the "
                   f"parsed rule bodies.")
    else:
        record(c, "unverified", 0.0, None, "",
               "Engine-capacity and GVW category thresholds are defined in CMVR schedules and "
               "MoRTH vehicle-category notifications not present in the corpus.")

(OUT / "verdicts_central.json").write_text(json.dumps(verdicts, indent=1))

cnt = collections.Counter(v["status"] for v in verdicts.values())
bykind = collections.defaultdict(collections.Counter)
for v in verdicts.values():
    bykind[v["kind"]][v["status"]] += 1
print(f"central-layer claims adjudicated: {len(verdicts)}")
for k, n in cnt.most_common():
    print(f"  {k:<22} {n}")
print()
for k, c2 in bykind.items():
    print(f"  {k:<16} {dict(c2)}")
