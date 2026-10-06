"""
LLM scenario extractor (AI mode).

One Groq call turns the story into the Scenario schema. The model may only
use violation codes from the catalogue it is given, and every actor and event
must quote the user's own words as evidence — anything it cannot quote is
dropped. It never states amounts or sections; the reasoner does.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from .kb import get_kb

log = logging.getLogger("drivelegal.scenario")

_SYSTEM = """You read Indian road-incident stories and convert them to JSON facts. You never give legal advice, fines or section numbers.

Output ONE JSON object with exactly these keys:
{
 "actors": [{"id":"A1","label":"short name e.g. 'You', 'Your son', 'The truck driver'","relation":"self|son|daughter|friend|cousin|brother|sister|father|mother|spouse|employee|other",
             "roles":["driver|rider|pillion|passenger|owner|guardian|conductor|operator|employer|pedestrian"],
             "age":null,"licence":"valid|none|learner|expired|suspended|null","is_victim":false,"vehicle":"V1|null","count":1,
             "evidence":"exact words from the story"}],
 "vehicles": [{"id":"V1","segment":"two_wheeler|three_wheeler|four_wheeler|four_wheeler_plus|heavy_vehicle|special|null",
               "use":"private|commercial_passenger|commercial_goods|null","owner":"A1|null","occupants":null,"label":"car|bike|truck|..."}],
 "events": [{"id":"E1","actor":"A1","vehicle":"V1","offences":["CODE"],"evidence":"exact words from the story"}],
 "facts": {"outcome":"none|damage|injury|grievous_injury|death|null","fled":true|false|null,"collision":true|false,
           "collision_agent":"A?|null","speed_over_pct":null,"zone":"school|hospital|residential|highway|city|null",
           "time":"day|night|null","owner_permitted":true|false|null},
 "asks": ["fine","liability","complaint","compensation","what_to_do"]
}

Rules:
- One actor per distinct person or group ("my two friends" = one actor with count 2). "I/me/my" = relation "self".
- roles: what the person was doing. A person hit by someone else is is_victim=true.
- offences: ONLY codes from the CATALOGUE that the person's own conduct matches. Put an offence on the person who did it (pillion's missing helmet on the pillion; rear passenger's seatbelt on the passenger).
- Do NOT add offences that follow from facts — the engine derives these itself: underage driving, owner allowing an unlicensed driver, guardian liability, hit-and-run, causing hurt/death, triple riding, overspeeding 50%+. Just record the facts (age, licence, fled, outcome, occupants, speed).
- Unknown = null. Never guess ages, licences or injuries that the story does not state.
- Be thorough: a story often contains several offences (documents, safety gear, signals, noise, parking, commercial rules) — list every one the story describes.
- evidence must be copied word-for-word from the story (a short span).
- speed_over_pct = (speed - limit) / limit * 100 when both are stated.
- Hinglish/Tamil-English is common: "bina helmet" = without helmet, "daaru" = alcohol, "teen log" = three people.

CATALOGUE (CODE=offence):
"""


_LICENCE_WORDS = re.compile(r"\b(licen[cs]e|dl|learner|unlicen[cs]ed|ll)\b", re.I)
_FIRST_PERSON = re.compile(r"\b(i|i'm|im|me|my|we|us|our)\b", re.I)
_KIN_WORD = re.compile(r"\b(son|daughter|friend|cousin|brother|sister|father|mother|wife|husband|kid|child|nephew|niece|uncle|employee|driver)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


def _quoted(evidence: str, story_n: str) -> bool:
    ev = _norm(evidence)
    if not ev:
        return False
    if ev in story_n:
        return True
    toks = [t for t in ev.split() if len(t) > 2]
    if not toks:
        return False
    hit = sum(1 for t in toks if f" {t} " in f" {story_n} ")
    return hit / len(toks) >= 0.75


def _catalogue() -> str:
    return "\n".join(get_kb().catalogue_lines())


def extract(text: str, call=None) -> Optional[dict]:
    """Scenario dict from the LLM, or None on any failure (caller falls back to rules)."""
    import llm_chatbot
    call = call or llm_chatbot._call_groq
    hints = "\n".join(get_kb().hint_lines(text))
    messages = [{"role": "system", "content": _SYSTEM + _catalogue()},
                {"role": "user", "content": f"STORY:\n{text.strip()[:1500]}\n\nMOST LIKELY CODES for this story (full wording — "
                                            f"check EACH against what the story says and include every one whose conduct is described, "
                                            f"on the right person):\n{hints}\n\nReturn the JSON object only."}]
    raw = None
    # gpt-oss-120b first; on a rate limit fall back to gpt-oss-20b (separate
    # free-tier token budget) before giving up to the rules extractor.
    import time
    for attempt, reasoning in enumerate((True, False, True)):
        if attempt == 2:
            time.sleep(7)             # free tier is tokens-per-minute: a short wait usually clears it
        try:
            raw = call(messages, max_tokens=700, temperature=0.0, json_mode=True, reasoning=reasoning)
            break
        except Exception as exc:      # offline, rate-limited, bad key …
            log.info("scenario LLM extraction (%s) unavailable: %s", "120b" if reasoning else "default", exc)
            if "rate limit" not in str(exc).lower():
                return None
    if raw is None:
        return None
    try:
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:
        log.info("scenario LLM returned non-JSON")
        return None
    return validate(data, text)


# Offences that are only plausible when the story mentions their subject at all — stops the model
# "pattern-matching" a rare code onto an unrelated story (e.g. old-diesel ban on a scooter).
_NEEDS = [
    ("EMIT_OLD_DIESEL", r"diesel|petrol|\bncr\b|delhi|\bold (car|vehicle|truck|bike|bus)\b|years? old (car|vehicle|truck|bus)"),
    ("EMIT_BS", r"\bbs\b|bs-?(iv|vi|6|4)|emission|banned|\bncr\b|delhi"),
    ("SCHOOL_BUS", r"school"), ("COMM_SCHOOL_BUS", r"school"),
    # "hit me at a signal" is not signal jumping — the story must say the light was jumped / red
    ("SIGNAL_RED", r"\bred\b|jump\w*|\bran\b.{0,15}(signal|light)|\bbroke\b.{0,15}(signal|light)|"
                   r"skip\w*.{0,15}(signal|light)|cross\w*.{0,15}(signal|light)|signal (jump|break)\w*|"
                   r"without stopping"),
    ("EV_", r"\bev\b|electric|charg|battery|retrofit"),
    ("FASTAG", r"fastag|fast tag|toll"), ("TOLL", r"toll|fastag"),
    ("MOD_BULL_BARS", r"bull|crash.?guard"), ("MOD_LASER", r"jammer|radar|laser"),
    ("SPEED_GOVERNOR", r"governor"), ("COMM_METER_TAMPERING", r"meter"),
]


def _story_supports(code: str, text: str) -> bool:
    low = (text or "").lower()
    for prefix, pat in _NEEDS:
        if code.startswith(prefix) and not re.search(pat, low):
            return False
    return True


def validate(data: dict, text: str) -> Optional[dict]:
    kb = get_kb()
    story_n = _norm(text)
    actors, vehicles, events = [], [], []
    ids = set()
    for a in data.get("actors") or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        if a.get("relation") != "self" and not _quoted(a.get("evidence", ""), story_n):
            continue
        age = a.get("age")
        if age is not None:
            try:
                age = int(age)
            except (TypeError, ValueError):
                age = None
            # an age must appear in the story
            if age is not None and not re.search(rf"\b{age}\b", text):
                age = None
        lic = a.get("licence") if a.get("licence") in ("valid", "none", "learner", "expired", "suspended") else None
        if lic and not _LICENCE_WORDS.search(text):
            lic = None            # never infer a licence status the story doesn't mention
        rel = (a.get("relation") or "other").lower()
        if (a.get("label") or "").strip().lower() in ("you", "me", "i", "myself", "user", "narrator"):
            rel = "self"
        if rel == "self" and not _FIRST_PERSON.search(text):
            rel = "other"
        if rel == "other":
            kin = _KIN_WORD.search(f"{a.get('label', '')} {a.get('evidence', '')}")
            if kin and re.search(rf"\bmy\s+(\d+[- ]?(year|yr)s?[- ]?old\s+)?{kin.group(1)}", text, re.I):
                rel = kin.group(1).lower()
        roles = [r for r in (a.get("roles") or []) if r in ("driver", "rider", "pillion", "passenger", "owner", "guardian",
                                                             "conductor", "operator", "employer", "pedestrian")]
        actors.append({"id": str(a["id"]), "label": (a.get("label") or "Someone")[:40], "relation": rel,
                       "roles": roles, "age": age, "licence": lic, "is_victim": bool(a.get("is_victim")),
                       "vehicle": a.get("vehicle"), "count": max(1, int(a.get("count") or 1)), "ref": a.get("evidence", "")[:60]})
        ids.add(str(a["id"]))
    vids = set()
    for v in data.get("vehicles") or []:
        if not isinstance(v, dict) or not v.get("id"):
            continue
        seg = v.get("segment") if v.get("segment") in ("two_wheeler", "three_wheeler", "four_wheeler",
                                                        "four_wheeler_plus", "heavy_vehicle", "special") else None
        use = v.get("use") if v.get("use") in ("private", "commercial_passenger", "commercial_goods") else None
        occ = v.get("occupants")
        try:
            occ = int(occ) if occ is not None else None
        except (TypeError, ValueError):
            occ = None
        vehicles.append({"id": str(v["id"]), "segment": seg, "use": use,
                         "owner": v.get("owner") if v.get("owner") in ids else None,
                         "occupants": occ, "label": (v.get("label") or "")[:20]})
        vids.add(str(v["id"]))
    for a in actors:
        if a["vehicle"] not in vids:
            a["vehicle"] = None
    for e in data.get("events") or []:
        if not isinstance(e, dict) or e.get("actor") not in ids:
            continue
        if not _quoted(e.get("evidence", ""), story_n):
            continue
        codes = [c for c in (e.get("offences") or []) if isinstance(c, str) and kb.exists(c) and _story_supports(c, text)]
        if not codes:
            continue
        events.append({"id": str(e.get("id") or f"E{len(events) + 1}"), "actor": e["actor"],
                       "vehicle": e.get("vehicle") if e.get("vehicle") in vids else None,
                       "offences": codes, "text": e.get("evidence", "")[:160]})
    f = data.get("facts") or {}
    facts = {
        "outcome": f.get("outcome") if f.get("outcome") in ("none", "damage", "injury", "grievous_injury", "death") else None,
        "fled": f.get("fled") if isinstance(f.get("fled"), bool) else None,
        "collision": bool(f.get("collision")),
        "collision_agent": f.get("collision_agent") if f.get("collision_agent") in ids else None,
        "speed_over_pct": f.get("speed_over_pct") if isinstance(f.get("speed_over_pct"), (int, float)) else None,
        "zone": f.get("zone") if f.get("zone") in ("school", "hospital", "residential", "highway", "city") else None,
        "time": f.get("time") if f.get("time") in ("day", "night") else None,
        "owner_permitted": f.get("owner_permitted") if isinstance(f.get("owner_permitted"), bool) else None,
    }
    asks = [x for x in (data.get("asks") or []) if x in ("fine", "liability", "complaint", "compensation", "what_to_do")]
    if not actors:
        return None
    return {"source": "llm", "actors": actors, "vehicles": vehicles, "events": events, "facts": facts,
            "asks": asks, "text": text}


_KIN_LABEL = {"son", "daughter", "friend", "cousin", "brother", "sister", "father", "mother", "spouse",
              "employee", "kid", "child", "nephew", "niece", "uncle", "wife", "husband"}


def _label(a: dict) -> str:
    rel = a.get("relation")
    if rel == "self":
        return "You"
    if rel in _KIN_LABEL:
        return f"Your {rel}" + ("s" if a.get("count", 1) > 1 and not rel.endswith("s") else "")
    lab = (a.get("label") or "Someone").strip()
    lab = re.sub(r"^(my|the|a|an)\s+", "", lab, flags=re.I)
    return "The " + lab[0].lower() + lab[1:] if lab else "Someone"


def merge(llm: dict, rules: dict) -> dict:
    """Add high-confidence rule findings the LLM missed, attached to the LLM's
    matching person (same relation / role), and fill facts the LLM left null."""
    out = llm
    have = {c for e in out["events"] for c in e["offences"]}
    llm_have = set(have)          # the LLM's attribution is authoritative: rules never re-assign its offences
    # The user is always a party when they speak in the first person.
    r_self = next((a for a in rules.get("actors", []) if a.get("relation") == "self"), None)
    l_self = next((a for a in out["actors"] if a.get("relation") == "self"), None)
    if r_self and not l_self:
        l_self = {**{k: r_self.get(k) for k in ("roles", "age", "licence", "is_victim", "count")},
                  "id": "A0", "label": "You", "relation": "self", "vehicle": None, "ref": "self"}
        out["actors"].insert(0, l_self)

    rveh = {v["id"]: v for v in rules.get("vehicles", [])}
    lveh = {v["id"]: v for v in out.get("vehicles", [])}

    def seg(actor: dict, vmap: dict) -> Optional[str]:
        return (vmap.get(actor.get("vehicle") or "") or {}).get("segment")

    def match(actor: dict) -> Optional[dict]:
        if not actor:
            return None
        rel = actor.get("relation")
        if rel == "self":
            return next((a for a in out["actors"] if a.get("relation") == "self"), None)
        if rel not in ("other", None):
            m = next((a for a in out["actors"] if a.get("relation") == rel), None)
            if m:
                return m
        roles = set(actor.get("roles") or [])
        for a in out["actors"]:
            if a.get("relation") == "self":
                continue
            if roles & set(a.get("roles") or []) and bool(a.get("is_victim")) == bool(actor.get("is_victim")):
                s1, s2 = seg(actor, rveh), seg(a, lveh)
                if not s1 or not s2 or s1 == s2:
                    return a
        words = set(re.findall(r"[a-z]+", (actor.get("label") or "").lower())) - {"the", "your", "a", "an"}
        for a in out["actors"]:
            if words & set(re.findall(r"[a-z]+", (a.get("label") or "").lower())):
                return a
        return None

    ra = {a["id"]: a for a in rules.get("actors", [])}
    for e in rules.get("events", []):
        r_actor = ra.get(e["actor"], {})
        tgt = match(r_actor)
        if tgt is None and r_actor:
            # a person the LLM didn't list (e.g. "my two friends" on the pillion)
            tgt = {**r_actor, "id": f"R{r_actor['id']}"}
            if r_actor.get("vehicle"):
                host = next((x for x in rules["actors"] if x.get("vehicle") == r_actor["vehicle"] and x is not r_actor), None)
                host_l = match(host) if host else None
                tgt["vehicle"] = host_l.get("vehicle") if host_l else None
            out["actors"].append(tgt)
        if not tgt:
            continue
        mine = {c for ev in out["events"] if ev["actor"] == tgt["id"] for c in ev["offences"]}
        new = [c for c in e["offences"] if c not in mine and c not in llm_have]
        if not new:
            continue
        out["events"].append({"id": f"R{len(out['events']) + 1}", "actor": tgt["id"], "vehicle": tgt.get("vehicle"),
                              "offences": new, "text": e.get("text", ""), "from_rules": True})
        have.update(new)
    for k, v in (rules.get("facts") or {}).items():
        if out["facts"].get(k) in (None, False) and v not in (None, False) and k != "collision_agent":
            out["facts"][k] = v
    # injuries must be stated, not inferred from "hit me": the rules reading is the authority
    out["facts"]["outcome"] = (rules.get("facts") or {}).get("outcome")
    out["facts"]["collision"] = bool(out["facts"].get("collision") or (rules.get("facts") or {}).get("collision"))
    for ra_ in rules.get("actors", []):
        tgt = match(ra_)
        if tgt:
            if tgt.get("age") is None and ra_.get("age") is not None:
                tgt["age"] = ra_["age"]
            if tgt.get("licence") is None and ra_.get("licence"):
                tgt["licence"] = ra_["licence"]
    # vehicle ownership the rules saw ("my car") → the LLM's matching vehicle
    rv = {v["id"]: v for v in rules.get("vehicles", [])}
    lv = {v["id"]: v for v in out.get("vehicles", [])}
    for ra_ in rules.get("actors", []):
        v = rv.get(ra_.get("vehicle"))
        tgt = match(ra_)
        if not v or not tgt:
            continue
        if not tgt.get("vehicle"):
            nv = {"id": f"V{len(out['vehicles']) + 1}r", "segment": v.get("segment"), "use": v.get("use"),
                  "owner": None, "occupants": v.get("occupants"), "label": v.get("label")}
            out["vehicles"].append(nv)
            lv[nv["id"]] = nv
            tgt["vehicle"] = nv["id"]
        lvv = lv.get(tgt["vehicle"])
        if lvv is not None:
            if not lvv.get("owner") and v.get("owner"):
                owner = match(next((a for a in rules["actors"] if a["id"] == v["owner"]), {}))
                if owner:
                    lvv["owner"] = owner["id"]
            for k in ("segment", "use", "occupants", "label"):
                if not lvv.get(k) and v.get(k):
                    lvv[k] = v[k]
    # the collision agent can't be the person who was hit
    agent = next((a for a in out["actors"] if a["id"] == out["facts"].get("collision_agent")), None)
    if agent is None or agent.get("is_victim"):
        r_agent = next((a for a in rules.get("actors", []) if a["id"] == (rules.get("facts") or {}).get("collision_agent")), None)
        m_agent = match(r_agent) if r_agent else None
        out["facts"]["collision_agent"] = m_agent["id"] if m_agent and not m_agent.get("is_victim") else None
    for a in out["actors"]:
        a["label"] = _label(a)
    out["asks"] = list(dict.fromkeys((out.get("asks") or []) + (rules.get("asks") or [])))
    return out
