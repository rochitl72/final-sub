"""
Offline (rules) scenario extractor.

Fills the same Scenario schema as the LLM extractor, with no network:
clause split → actor lexicon + light coreference → per-clause multi-label
violation matching (existing resolver) → incident facts (age, licence,
outcome, fled, speed, zone, occupants, permission, questions asked).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from graph_engine import VEHICLE_KEYWORDS

# ── Lexicons ─────────────────────────────────────────────────────────────────

_KIN = r"(son|daughter|kid|child|boy|girl|brother|sister|cousin|friend|father|dad|mother|mom|mum|wife|husband|" \
       r"uncle|aunt|nephew|niece|colleague|neighbou?r|driver|employee|roommate|teenager|grandson|granddaughter)"
_MINOR_KIN = {"son", "daughter", "kid", "child", "boy", "girl", "teenager", "grandson", "granddaughter", "nephew", "niece"}
_VEH = r"(auto(?:\s?rickshaw)?|rickshaw|bus|truck|lorry|tanker|tempo|cab|taxi|ola|uber|rapido|car|bike|motorbike|motorcycle|" \
       r"scooter|scooty|van|jeep|suv|tractor|e-?rickshaw|two[- ]wheeler|activa)"

_SELF_RE = re.compile(r"\b(i|i'm|im|i've|i'd|me|my|myself|mine|mera|meri|mere|main|mai|mujhe|hum|humne|naan|en)\b", re.I)
_GROUP_RE = re.compile(r"\b(we|us|our|none of us|all of us|both of us|three of us|all three|each of us|nobody)\b", re.I)

_OUTCOME_DEATH = re.compile(r"\b(died|dead|death|killed|passed away|fatal(ly)?|lost (his|her|their) life)\b", re.I)
_OUTCOME_GRIEVOUS = re.compile(r"\b(fractur\w*|broke (his|her|their|my) (leg|arm|hand|bone)|seriously (injured|hurt)|"
                               r"badly (injured|hurt)|icu|critical(ly)?|unconscious|head injur\w*|grievous\w*|hospitali[sz]ed)\b", re.I)
_OUTCOME_INJURY = re.compile(r"\b(injur\w*|hurt|bleed\w*|wounded|bruis\w*|got hurt|fell (down|off)|hospital|stitches|"
                             r"(small |minor )?cuts?|scratch\w*|sprain\w*)\b", re.I)
_NO_INJURY = re.compile(r"\b((no ?one|nobody|none of us) (was |got |were )?(hurt|injured)|without (any )?injur\w*|"
                        r"no injur\w*|only (minor )?damage|nobody got hurt)\b", re.I)
_COLLISION = re.compile(r"(?<!nearly )(?<!almost )\b(hit|hits|hitting|crash\w*|collid\w*|collision|accident|rammed|rear[- ]?ended|knocked|"
                        r"bumped|dashed|ran over|ran into|banged)\b", re.I)
_FLED = re.compile(r"\b(ran away|run away|fled|flee\w*|drove off|drove away|sped (off|away)|rode off|rode away|"
                   r"left the (scene|spot)|didn'?t stop|did not stop|escaped|absconded|took off)\b", re.I)
_NIGHT = re.compile(r"\b(at night|night|midnight|late night|after dark|\d{1,2}\s?(am)\b)", re.I)
_ZONE = [("school", re.compile(r"\bschool\b", re.I)), ("hospital", re.compile(r"\bhospital zone|near (a |the )?hospital\b", re.I)),
         ("residential", re.compile(r"\bresidential\b", re.I)),
         ("highway", re.compile(r"\b(highway|expressway|nh\s?\d+)\b", re.I))]
_SPEED = re.compile(r"\b(\d{2,3})\s*(?:km/?h|kmph|kph)?\s*(?:in|on)\s*(?:a|the)?\s*(\d{2,3})\s*(?:km/?h|kmph)?\s*(?:zone|limit|road|area)?", re.I)
_LICENCE_NONE = re.compile(r"\b(without (a |any |his |her |my |valid )?(driving )?licen[cs]e|no (driving )?licen[cs]e|"
                           r"licen[cs]e (bhi |also )?(nahi|nahin|illa)|"
                           r"(don'?t|doesn'?t|didn'?t|does not|did not|do not) (have|hold) (a |any )?(valid )?(driving )?licen[cs]e|"
                           r"unlicen[cs]ed|bina licen[cs]e|no dl|without dl)\b", re.I)
_LICENCE_EXPIRED = re.compile(r"\b(expired (driving )?licen[cs]e|licen[cs]e (has |had |was )?expired)\b", re.I)
_LICENCE_LEARNER = re.compile(r"\blearner'?s? (licen[cs]e|permit)|\bll\b", re.I)
_LICENCE_VALID = re.compile(r"\b(has|had|have|with) (a |his |her |my )?(valid )?(driving )?licen[cs]e\b", re.I)
_PERMITTED_YES = re.compile(r"\b(borrowed|lent|gave (him|her|them) (my|the)|let (him|her|them|my \w+) (drive|ride|take)|"
                            r"with (my|the owner'?s?) permission|owner knew|knew about it|allowed (him|her|them))\b", re.I)
_PERMITTED_NO = re.compile(r"\b(without (asking|permission|telling)|stole|stolen|didn'?t know|did not know|behind my back|"
                           r"without my (knowledge|permission))\b", re.I)
_EXTRA_PAX = re.compile(r"\b(\d{1,3})\s+(extra|more|additional)\s+(people|passengers|persons)\b|"
                        r"\b(carrying|with|had|took)\s+(\d{1,2})\s+(?:school\s+)?(passengers|people|kids|children|students|persons)\b|"
                        r"\b(too many|excess|extra) (people|passengers)\b", re.I)
_OCCUPANTS = [
    (re.compile(r"\b(me and|i and) (two|2) (friends|others|people)\b", re.I), 3),
    (re.compile(r"\b(three|3) of us\b|\btriple\w*\b|\bteen log\b|\bwith his friends\b|\bwith her friends\b", re.I), 3),
    (re.compile(r"\b(three|3)\s+(people|persons|riders|guys|boys|girls|of them)\s+(on|riding)\b|"
                r"\bwith\s+(two|2)\s+(others|pillions|pillion riders|friends)\s+(on|behind)\b", re.I), 3),
    (re.compile(r"\b(four|4)\s+(people|persons|riders|guys|boys|girls|of them)\s+(on|riding)\b", re.I), 4),
    (re.compile(r"\b(four|4) of us\b", re.I), 4),
    (re.compile(r"\b(me and|i and) (my )?(a )?friend\b|\btwo of us\b|\bboth of us\b", re.I), 2),
]
_LIABILITY_Q = re.compile(r"\b(am i|are we|will i|would i|is he|is she|is my \w+)\b.{0,25}\b(liable|responsible|fined|charged|punished|in trouble|guilty)\b|"
                          r"\bwho (pays|will pay|has to pay|should pay|is liable|is responsible|gets fined)\b|\b(also )?liable\b|\bwho pays what\b", re.I)
_COMPLAINT_Q = re.compile(r"\b(complain\w*|report (him|her|them|the driver)|file (a )?(case|complaint|fir))\b", re.I)
_COMP_Q = re.compile(r"\b(compensation|claim|insurance claim|damages)\b", re.I)
_WHATDO_Q = re.compile(r"\bwhat (can|should|do) i do\b|\bwhat now\b|\bnext steps?\b", re.I)

_HELMET_CODES = {"SAFETY_NO_HELMET_RIDER", "SAFETY_NO_HELMET_PILLION"}
_SEATBELT_CODES = {"SAFETY_NO_SEATBELT_DRIVER", "SAFETY_NO_SEATBELT_PASSENGER"}
# Codes the clause matcher may surface but that are really derived from facts
# (handled by fact triggers in the reasoner) — avoid double/incorrect picks.
_FACT_DERIVED = {"DOC_UNDERAGE", "JUV_MINOR_DRIVING", "DOC_UNAUTHORIZED_USE_VEHICLE", "ACC_HIT_AND_RUN",
                 "ACC_HIT_AND_RUN_RELATED", "ACC_NOT_REPORT", "ACC_CAUSING_DEATH", "CRIM_HURT_RASH_NEGLIGENT",
                 "CRIM_RASH_DRIVING_PUBLIC_WAY", "SAFETY_MORE_THAN_2_ON_2W", "OVERLOAD_PASSENGER"}
# Weak / generic codes that should never come from a single noisy token.
_NOISY = {"MISC_GENERAL", "EV_RESTRICTED_ZONE_VIOLATION", "HWY_STOP_ON_SHOULDER", "SAFETY_NO_AIRBAG",
          "OVERLOAD_ROOFTOP_LUGGAGE", "HWY_TRUCK_LEFT_LANE", "DOC_UNAUTHORIZED_INTERFERENCE", "NOISE_HORN_SILENT_ZONE"}
# Gear and paperwork offences need an absence cue in the same clause
# ("no helmet", "insurance expired") — "I was a passenger in the back" is not one.
_ABSENCE = re.compile(r"(?:\b(no|not|without|off|expired|lapsed|fake|forged|missing|forgot|nahi|nahin|bina|illa|"
                      r"never|removed|tampered|old|invalid|none|lap|standing)\b|n'?t\b)", re.I)
_ABSENCE_EXEMPT = {"DOC_UNDERAGE", "DOC_TAKING_VEHICLE_WITHOUT_AUTH", "SAFETY_NO_CHILD_RESTRAINT",
                   "SAFETY_MORE_THAN_2_ON_2W", "SAFETY_NO_CHILD_2W"}
# Families that need their own subject word in the clause (precision guards).
_CUES = [("PARK_", re.compile(r"\b(park\w*|towed|hydrant|double|kept|left (it|the|my|his))\b|"
                             r"\b(was|were|is) (on|at|in) the (footpath|bus stop)\b", re.I)),
         ("SIGNAL_RED_LIGHT", re.compile(r"\b(jump\w*|ran|run|crossed|cross|broke|skipp?ed|tod|violat\w*)\b", re.I)),
         ("SPEED_", re.compile(r"(speed(?!\s+(governor|limiter|breaker|bump))|\bfast\b|\btez\b|kmph|km/?h|\bkph\b|\blimit\b|racing|"
                               r"\b\d{2,3}\s*(in|on)\s*(a|the)?\s*\d{2,3}\b)", re.I)),
         ("DIST_MOBILE", re.compile(r"\b(using|talking|texting|chatting|scroll\w*|call|calls|whatsapp|on (a|the|my|his|her) (phone|mobile)|phone (while|pe)|mobile (while|pe))\b", re.I)),
         ("LIGHT_", re.compile(r"\b(light|lights|lamp|lamps|headlights?|headlamps?)\b", re.I)),
         ("DOC_DISOBEY", re.compile(r"\b(refus\w*|disobey\w*|argu\w*|ignor\w*)\b", re.I)),
         ("PED_ZEBRA", re.compile(r"\b(zebra|pedestrian|crossing)\b", re.I)),
         ("EMERG_", re.compile(r"\b(ambulance|fire (engine|truck)|siren|emergency)\b", re.I)),
         ("DANGER_CARRYING_HAZARDOUS", re.compile(r"\b(hazard\w*|petrol|diesel|gas|chemical|explosive|lpg|tanker|acid)\b", re.I))]
_NEEDS_ABSENCE_GROUPS = {"safety_gear", "documents"}
_OBJECT_COLLISION = re.compile(r"\b(hit|crashed into|rammed|bumped|dashed)\s+(a |the |into )?(parked (car|bike|vehicle)|wall|divider|"
                               r"pole|tree|gate|barricade|median|footpath)\b", re.I)
_PERSON_WORDS = re.compile(r"\b(pedestrian|cyclist|rider|man|woman|boy|girl|child|kid|person|someone|people|him|her|them|me|us)\b", re.I)

# Clauses that only describe the collision / victim, not an offence.
_VICTIM_CLAUSE = re.compile(r"\b(hit|rammed|rear[- ]?ended|knocked|crashed into|ran into|bumped)\s+(me|us|my)\b|"
                            r"\b(i|we) (was|were|got) (hit|knocked|rammed)\b", re.I)

# Descriptions of lawful behaviour that share words with offences.
_INNOCENT = re.compile(r"\b(waiting|stopped|standing|parked|halted) (at|near) (the |a )?(red )?(signal|light|junction|crossing)\b|"
                       r"\bwhile i was (waiting|stopped|parked)\b|\bpolice (came|stopped us|stopped me|caught us)\s*$", re.I)

_SEG_USE = {"heavy_vehicle": "commercial_goods", "four_wheeler_plus": "commercial_passenger",
            "three_wheeler": "commercial_passenger"}
_COMMERCIAL_WORDS = re.compile(r"\b(taxi|cab|ola|uber|auto|rickshaw|bus|truck|lorry|tempo|goods|commercial)\b", re.I)

_CLAUSE_SPLIT = re.compile(r"(?:[.;!?]+\s+|,\s*(?:and\s+|but\s+|so\s+)?|\s+(?:and then|then|and|but|plus|while|after which)\s+)", re.I)


def _vehicle_segment(word: str) -> Optional[str]:
    w = word.lower().replace("-", " ")
    if w in VEHICLE_KEYWORDS:
        return VEHICLE_KEYWORDS[w]
    if w.startswith("auto") or "rickshaw" in w:
        return "three_wheeler"
    if w in ("scooty", "activa", "motorbike", "two wheeler"):
        return "two_wheeler"
    if w in ("van", "jeep", "suv", "ola", "uber", "rapido"):
        return "four_wheeler"
    if w in ("tempo", "tanker"):
        return "heavy_vehicle"
    return None


def _clauses(text: str) -> List[str]:
    parts = [p.strip(" ,.;") for p in _CLAUSE_SPLIT.split(text or "")]
    return [p for p in parts if p and len(p) > 2]


class _Builder:
    def __init__(self) -> None:
        self.actors: List[dict] = []
        self.vehicles: List[dict] = []
        self.events: List[dict] = []
        self._key: Dict[str, str] = {}

    # actors ------------------------------------------------------------------
    def actor(self, key: str, **kw) -> dict:
        if key in self._key:
            a = self.get(self._key[key])
            for k, v in kw.items():
                if v is not None and (a.get(k) in (None, [], "") or k == "is_victim" and v):
                    a[k] = v
            return a
        aid = f"A{len(self.actors) + 1}"
        a = {"id": aid, "label": kw.pop("label", key), "relation": kw.pop("relation", "other"),
             "roles": kw.pop("roles", []), "age": None, "licence": None, "is_victim": False,
             "vehicle": None, "count": 1, "ref": key}
        a.update({k: v for k, v in kw.items() if v is not None})
        self.actors.append(a)
        self._key[key] = aid
        return a

    def get(self, aid: Optional[str]) -> Optional[dict]:
        return next((a for a in self.actors if a["id"] == aid), None)

    def vehicle(self, segment: Optional[str], owner: Optional[str] = None, label: str = "") -> dict:
        vid = f"V{len(self.vehicles) + 1}"
        v = {"id": vid, "segment": segment, "use": _SEG_USE.get(segment), "owner": owner,
             "occupants": None, "label": label}
        self.vehicles.append(v)
        return v

    def vget(self, vid: Optional[str]) -> Optional[dict]:
        return next((v for v in self.vehicles if v["id"] == vid), None)


def _find_actor_mentions(cl: str) -> List[Tuple[int, str, dict]]:
    """(position, key, attributes) for each actor named in a clause."""
    out = []
    for m in re.finditer(rf"\bmy\s+(\d{{1,2}})[- ]?(?:year|yr)s?[- ]?old\s+{_KIN}\b", cl, re.I):
        kin = m.group(2).lower()
        out.append((m.start(), f"my {kin}", {"relation": kin, "age": int(m.group(1)), "label": f"Your {kin}"}))
    for m in re.finditer(rf"\bmy\s+(\d{{1,2}})[- ]?(?:year|yr)s?[- ]?old\b(?!\s+{_KIN})", cl, re.I):
        out.append((m.start(), "my child", {"relation": "child", "age": int(m.group(1)), "label": "Your child"}))
    for m in re.finditer(r"\b(?:the\s+)?(?:company|firm|transport company|travels)\s+owner\b|\bmy\s+(?:boss|employer|company)\b", cl, re.I):
        out.append((m.start(), "employer", {"relation": "employer", "roles": ["owner", "employer"], "label": "The company / owner"}))
    for m in re.finditer(rf"\bmy\s+(?:younger|elder|older|little|big|eldest|youngest|own)?\s*{_KIN}\b", cl, re.I):
        kin = m.group(1).lower()
        if any(k == f"my {kin}" for _, k, _ in out):
            continue
        out.append((m.start(), f"my {kin}", {"relation": kin, "label": f"Your {kin}"}))
    m0 = re.match(rf"\s*{_KIN}\b", cl, re.I)
    if m0 and not re.match(r"\s*(driver|friend)\b", cl, re.I):
        kin = m0.group(1).lower()
        out.append((0, f"my {kin}", {"relation": kin, "label": f"Your {kin}"}))
    for m in re.finditer(r"\b(?:his|her|their)\s+(father|dad|mother|mom|parents?|guardian)\b", cl, re.I):
        out.append((m.start(), f"parent {m.group(1).lower()}", {"relation": "parent", "roles": [], "label": f"The {m.group(1).lower()}"}))
    for m in re.finditer(r"\b(?:a|the)\s+(minor|teenager|kid|boy|girl)\b(?=\s+(?:was|is|had|drove|rode|driving|riding))", cl, re.I):
        out.append((m.start(), f"minor {m.group(1).lower()}", {"relation": "other", "roles": ["driver"], "age": 17,
                                                              "label": f"The {m.group(1).lower()}"}))
    for m in re.finditer(r"\b(?:a|an|the)\s+(?:drunk\s+|rash\s+|speeding\s+)?(driver|rider)\b(?!'s)", cl, re.I):
        who = m.group(1).lower()
        out.append((m.start(), "generic " + who, {"relation": "other", "roles": [who], "label": f"The {who}"}))
    m1 = re.match(rf"\s*(?:a|an|the|our|this)?\s*(?:school\s+|goods\s+|private\s+)?{_VEH}\b(?!\s+(?:driver|rider|wala|walla|guy|owner|conductor))", cl, re.I)
    if re.match(r"\s*(?:a|an|the)?\s*(ambulance|police|fire)", cl, re.I):
        m1 = None
    if m1 and re.search(r"\b(was|had|has|is|carrying|went|drove|came)\b", cl[m1.end():m1.end() + 30], re.I):
        veh = m1.group(1).lower()
        seg = _vehicle_segment(veh)
        role = "rider" if seg == "two_wheeler" else "driver"
        out.append((0, f"{veh} {role}", {"relation": "other", "roles": [role], "label": f"The {veh} {role}", "_veh": veh}))
    for m in re.finditer(rf"\b(?:the|a|an|that|this|his|her)?\s*{_VEH}\s+(driver|rider|wala|walla|guy|owner)\b", cl, re.I):
        veh, who = m.group(1).lower(), m.group(2).lower()
        role = "owner" if who == "owner" else ("rider" if _vehicle_segment(veh) == "two_wheeler" else "driver")
        label = f"The {veh} {'owner' if who == 'owner' else ('rider' if role == 'rider' else 'driver')}"
        out.append((m.start(), f"{veh} {role}", {"relation": "other", "roles": [role], "label": label,
                                                  "_veh": veh}))
    for m in re.finditer(r"\b(?:my|his|her)\s+pillion(?:\s+rider)?\s+(?:was|is)\s+(?:my|his|her)\s+(?:\d{1,2}[- ]?(?:year|yr)s?[- ]?old\s+)?" + _KIN, cl, re.I):
        kin = m.group(1).lower()
        out.append((m.start(), f"my {kin}", {"relation": kin, "roles": ["pillion"], "label": f"Your {kin}"}))
    for m in re.finditer(r"\b(?:the|a|an)\s+(conductor|owner|pillion(?: rider)?|passenger(?!\s+seat)|pedestrian|cyclist|guardian|parent)\b", cl, re.I):
        who = m.group(1).lower().split()[0]
        role = {"pillion": "pillion", "passenger": "passenger", "pedestrian": "pedestrian",
                "cyclist": "pedestrian", "parent": "guardian"}.get(who, who)
        out.append((m.start(), f"the {who}", {"relation": "other", "roles": [role], "label": f"The {who}"}))
    for m in re.finditer(r"\b(?:his|her|their|my)\s+friend\s+(?:on the back|behind|riding pillion|on the pillion)\b|"
                         r"\bfriend (?:on the back|behind (?:him|her|me))\b", cl, re.I):
        out.append((m.start(), "pillion friend", {"relation": "friend", "roles": ["pillion"], "label": "The pillion rider"}))
    for m in re.finditer(r"\b(?:a|another|some|the other)\s+" + _VEH + r"\b(?=\s+(?:hit|rammed|rear[- ]?ended|knocked|crashed|bumped|ran))", cl, re.I):
        veh = m.group(1).lower()
        out.append((m.start(), f"other {veh}", {"relation": "other", "roles": ["driver"],
                                                 "label": f"The other {veh} driver", "_veh": veh}))
    out.sort(key=lambda x: x[0])
    return out


def extract(text: str) -> dict:
    from violation_resolver import get_violation_resolver
    res = get_violation_resolver()
    b = _Builder()
    full = text or ""
    low = full.lower()

    self_a = None
    if _SELF_RE.search(full) or _GROUP_RE.search(full):
        self_a = b.actor("self", label="You", relation="self")

    facts = {"outcome": None, "fled": None, "collision": bool(_COLLISION.search(full)),
             "speed_over_pct": None, "zone": None, "time": None, "owner_permitted": None}
    if _OUTCOME_DEATH.search(full):
        facts["outcome"] = "death"
    elif _OUTCOME_GRIEVOUS.search(full):
        facts["outcome"] = "grievous_injury"
    elif _NO_INJURY.search(full):
        facts["outcome"] = "damage" if facts["collision"] else "none"
    elif _OUTCOME_INJURY.search(full):
        facts["outcome"] = "injury"
    elif not facts["collision"]:
        facts["outcome"] = "none"
    elif _OBJECT_COLLISION.search(full) and not _PERSON_WORDS.search(_OBJECT_COLLISION.search(full).group(0)) \
            and not re.search(r"\b(pedestrian|cyclist|rider|person|someone|kid|child)\b", full, re.I):
        facts["outcome"] = "damage"          # hit a wall / parked car — nobody to injure
    if _FLED.search(full):
        facts["fled"] = True
    for zone, rx in _ZONE:
        if rx.search(full):
            facts["zone"] = zone
            break
    if _NIGHT.search(full):
        facts["time"] = "night"
    sm = _SPEED.search(full)
    if sm:
        sp, lim = int(sm.group(1)), int(sm.group(2))
        if 5 <= lim < sp <= 250:
            facts["speed_over_pct"] = round((sp - lim) * 100 / lim)
    if _PERMITTED_NO.search(full):
        facts["owner_permitted"] = False
    elif _PERMITTED_YES.search(full):
        facts["owner_permitted"] = True

    asks = []
    if _LIABILITY_Q.search(full):
        asks.append("liability")
    if _COMPLAINT_Q.search(full):
        asks.append("complaint")
    if _COMP_Q.search(full):
        asks.append("compensation")
    if _WHATDO_Q.search(full):
        asks.append("what_to_do")
    if re.search(r"\b(fine|fines|challan|penalty|how much|kitna|what (will|would) (he|she|they|i) get)\b", low):
        asks.append("fine")

    occupants = None
    for rx, n in _OCCUPANTS:
        if rx.search(full):
            occupants = n
            break

    clauses = _clauses(full)
    if self_a is None and not any(_find_actor_mentions(c) for c in clauses):
        self_a = b.actor("self", label="You", relation="self")     # "daaru pi ke gaadi chala raha tha"
    subject: Optional[str] = self_a["id"] if self_a else None
    last_other: Optional[str] = None
    group_mode = False

    for cl in clauses:
        mentions = _find_actor_mentions(cl)
        clause_subject = None
        for pos, key, attrs in mentions:
            veh_word = attrs.pop("_veh", None)
            if key.startswith("generic "):
                role = key.split()[1]
                existing = next((x for x in reversed(b.actors) if role in x["roles"] and x["relation"] != "self"
                                 and not x.get("is_victim")), None)
                if existing:
                    key = next(k for k, v in b._key.items() if v == existing["id"])
                    attrs = {}
            a = b.actor(key, **attrs)
            if veh_word and not a.get("vehicle"):
                seg = _vehicle_segment(veh_word)
                v = b.vehicle(seg, owner=None, label=veh_word)
                if seg == "heavy_vehicle" or _COMMERCIAL_WORDS.search(veh_word):
                    v["use"] = _SEG_USE.get(seg) or "commercial_passenger"
                a["vehicle"] = v["id"]
            if "pillion" in a["roles"] or "passenger" in a["roles"]:
                host = b.get(clause_subject or subject)
                if host and host["id"] != a["id"] and not a.get("vehicle"):
                    a["vehicle"] = host.get("vehicle")
                    a["is_victim"] = a["is_victim"] or host.get("is_victim", False)
            last_other = a["id"]
            before = cl[max(0, pos - 18):pos].lower()
            possessive = pos > 3 and bool(re.match(r"(my|his|her)\s+\w+'s", cl[pos:pos + 30].lower()))
            is_object = possessive or bool(re.search(r"\b(hit|rammed|knocked|into|over|with|to|let|gave|helped|saw|and|behind|pillion was|was)\s*$", before))
            if pos <= 12 and clause_subject is None and not is_object:
                clause_subject = a["id"]
        head = cl[:14].lower()
        is_group = bool(re.match(r"\s*(we|none of us|all of us|both of us|three of us|me and|i and|us)\b", head))
        if clause_subject is None:
            if is_group and self_a:
                clause_subject = self_a["id"]
                group_mode = True
            elif re.match(r"\s*(i|i'm|im|i was|me)\b", head) and self_a:
                clause_subject = self_a["id"]
                group_mode = False
            elif re.match(r"\s*(he|she|him|his|her)\b", head) and last_other:
                clause_subject = last_other
            elif re.match(r"\s*(they|them)\b", head) and last_other:
                clause_subject = last_other
        if clause_subject is None and mentions:
            subj_like = [k for pos, k, _ in mentions
                         if not re.match(r"(my|his|her)\s+\w+'s\b", cl[pos:pos + 30].lower())
                         and not re.search(r"\b(hit|rammed|knocked|into|over|with|to|let|gave|helped|saw|behind|was)\s*$",
                                          cl[max(0, pos - 18):pos].lower())]
            if subj_like and b._key.get(subj_like[0]):
                clause_subject = b._key[subj_like[0]]
        if clause_subject:
            subject = clause_subject
        if subject is None:
            continue
        actor = b.get(subject)

        # actor facts --------------------------------------------------------
        age = re.search(r"\b(?:is|was|aged|age)\s+(\d{1,2})\b|\b(\d{1,2})\s*(?:years?|yrs?)[- ]?old\b", cl, re.I)
        own = re.search(r"\b(?:i am|i'm|im|i was|am)\s*(?:only\s*)?(\d{1,2})\b(?!\s*(?:km|kmph|%|kms|rs|₹))", cl, re.I)
        if own and self_a:
            self_a["age"] = int(own.group(1))
        elif age and actor.get("relation") == "self":
            if int(age.group(1) or age.group(2)) < 16 and not any(m[1].startswith("my ") for m in mentions):
                # "4 year old on my bike" — a child with the user, not the user
                child = b.actor("a child", relation="child", label="The child",
                                age=int(age.group(1) or age.group(2)), vehicle=actor.get("vehicle"))
                if re.search(r"\b(bike|scooter|scooty|motorcycle|two[- ]wheeler)\b", cl, re.I):
                    child["roles"] = ["pillion"]
                    if not actor["roles"]:
                        actor["roles"] = ["rider"]
        elif age and actor.get("age") is None:
            # "he is 16" / "aged 16" about the clause subject; "our 2 year old" is a different person
            ytxt = age.group(0)
            pre = cl[max(0, age.start() - 12):age.start()].lower()
            if re.search(r"\b(our|my|a|an|his|her|their)\s*$", pre) and age.group(2):
                kid = b.actor(f"child {age.group(2)}", relation="child", label="The child", age=int(age.group(2)))
                if re.search(r"\b(lap|front seat|back seat|seat|in the car)\b", cl, re.I):
                    kid["roles"] = kid["roles"] or ["passenger"]
                    kid["vehicle"] = kid.get("vehicle") or actor.get("vehicle")
            elif re.search(r"\b(is|was|aged|age|he's|she's)\b", ytxt + " " + pre) or age.group(1):
                actor["age"] = int(age.group(1) or age.group(2))
        if _LICENCE_NONE.search(cl):
            target = actor
            if re.search(r"\b(i|me)\b", cl, re.I) and re.search(r"\bi (don'?t|do not|didn'?t|did not)\b", cl, re.I) and self_a:
                target = self_a
            target["licence"] = "none"
        elif _LICENCE_EXPIRED.search(cl):
            actor["licence"] = "expired"
        elif _LICENCE_LEARNER.search(cl):
            actor["licence"] = "learner"
        elif _LICENCE_VALID.search(cl) and not re.search(r"\bnot?\b", cl, re.I):
            actor["licence"] = "valid"

        # vehicles -----------------------------------------------------------
        lent_vehicle = False
        lm = re.search(rf"\b(?:let|allowed|gave|lent)\b[^.]{{0,40}}?\b(?:take|drive|ride|use|to)?\s*(?:my|the|our|his|her)?\s*{_VEH}\b", cl, re.I)
        if lm and actor["relation"] == "self":
            taker = next((b.get(b._key[k]) for _, k, _ in mentions if b._key.get(k) and b._key[k] != actor["id"]), None)
            if taker:
                word = lm.group(lm.lastindex).lower()
                if not taker.get("vehicle"):
                    taker["vehicle"] = b.vehicle(_vehicle_segment(word), owner=actor["id"], label=word)["id"]
                taker["roles"] = taker["roles"] or ["rider" if _vehicle_segment(word) == "two_wheeler" else "driver"]
                lent_vehicle = True
        for pm in re.finditer(rf"\bmy\s+{_KIN}'s\s+(?:\w+\s+)?{_VEH}\b", cl, re.I):
            kin, word = pm.group(1).lower(), pm.group(2).lower()
            owner_a = b.actor(f"my {kin}", relation=kin, label=f"Your {kin}")
            if not actor.get("vehicle") and actor["id"] != owner_a["id"]:
                actor["vehicle"] = b.vehicle(_vehicle_segment(word), owner=owner_a["id"], label=word)["id"]
        for vm in ([] if lent_vehicle else re.finditer(rf"\b(my|his|her|their|our|a|an|the|one|same|two|both)?\s*{_VEH}s?\b", cl, re.I)):
            det, word = (vm.group(1) or "").lower(), vm.group(2).lower()
            seg = _vehicle_segment(word)
            if not seg:
                continue
            ctx_before = cl[max(0, vm.start() - 20):vm.start()].lower()
            if re.search(r"\b(hit|rammed|knocked|crashed into|ran into|into)\s+(a|an|the)?\s*$", ctx_before):
                # "hit a scooter" → the other vehicle's rider is a victim
                key = f"{word} rider" if seg == "two_wheeler" else f"{word} driver"
                victim = b.actor(key, relation="other", roles=["rider" if seg == "two_wheeler" else "driver"],
                                 label=f"The {word} {'rider' if seg == 'two_wheeler' else 'driver'}", is_victim=True)
                if not victim.get("vehicle"):
                    victim["vehicle"] = b.vehicle(seg, owner=victim["id"], label=word)["id"]
                continue
            if actor.get("vehicle"):
                v = b.vget(actor["vehicle"])
                if v and not v.get("segment"):
                    v["segment"], v["use"] = seg, _SEG_USE.get(seg)
                continue
            owner = None
            if det in ("my", "our") and self_a:
                owner = self_a["id"]
            elif det in ("his", "her", "their"):
                owner = actor["id"]
            v = b.vehicle(seg, owner=owner, label=word)
            if _COMMERCIAL_WORDS.search(word):
                v["use"] = _SEG_USE.get(seg) or ("commercial_passenger" if word in ("taxi", "cab") else v["use"])
            actor["vehicle"] = v["id"]

        # roles --------------------------------------------------------------
        if re.search(r"\b(driving|drove|drive|drives|took (my|the|his|her) (car|truck|bus|van|jeep))\b", cl, re.I) and not actor["roles"] \
                and not lent_vehicle and not re.search(r"\b(let|allowed|gave|lent)\b", cl, re.I):
            actor["roles"] = ["driver"]
        if re.search(r"\b(riding|rode|ride|rides|took (my|the|his|her) (bike|scooter|scooty))\b", cl, re.I) and not actor["roles"]:
            actor["roles"] = ["rider"]
        elif re.search(r"\bon (my|his|her|one|a|the) (bike|scooter|scooty|motorcycle|two[- ]wheeler)\b", cl, re.I) and not actor["roles"]:
            if actor["relation"] == "self" or not self_a:
                actor["roles"] = ["rider"]
                actor["_inferred_role"] = True
            else:
                actor["roles"] = ["pillion"]          # "my kid on my bike" = pillion; you ride
                if not self_a["roles"]:
                    self_a["roles"] = ["rider"]
                    self_a["vehicle"] = self_a.get("vehicle") or actor.get("vehicle")
        if re.search(r"\b(behind (me|him|her)|on the back|pillion)\b", cl, re.I) and actor["relation"] != "self" \
                and not actor["roles"]:
            actor["roles"] = ["pillion"]
        elif re.search(r"\b(back seat|rear seat|in the back|front seat|in front|sitting|sat)\b", cl, re.I) \
                and actor["relation"] != "self" and not actor["roles"]:
            actor["roles"] = ["passenger"]
        if re.search(r"\b(bi)?cycl(e|ing|ist)\b", cl, re.I) and not re.search(r"motor\s?cycl", cl, re.I) \
                and not re.search(r"\b(bike|scooter|scooty|car|auto)\b", cl, re.I):
            actor["roles"] = ["cyclist"]               # a pedal cycle is not a motor vehicle: no licence/age rules
            actor["vehicle"] = None
        if re.search(r"\b(hit|rammed|knocked|crashed into|ran into|bumped|dashed)\s+(him|her|them)\b", cl, re.I) \
                and actor["relation"] not in ("self", "other") and not (set(actor["roles"]) & {"driver"}):
            actor["is_victim"] = True
        if actor["relation"] == "self" and re.search(r"\b(i was|i sat|sitting) (a passenger|in the (passenger|front|back) seat|next to (him|her)|in the back)\b", cl, re.I):
            actor["roles"] = ["passenger"]
        if _VICTIM_CLAUSE.search(cl) and self_a:
            self_a["is_victim"] = True
            agent = next((b.get(b._key[k]) for _, k, _ in mentions if b.get(b._key[k])["id"] != self_a["id"]), None) \
                or (actor if actor["id"] != self_a["id"] and not actor.get("is_victim") else None)
            if agent and not facts.get("collision_agent"):
                facts["collision_agent"] = agent["id"]
        elif _COLLISION.search(cl) and not facts.get("collision_agent") and actor["relation"] != "other" or \
                (_COLLISION.search(cl) and not facts.get("collision_agent") and not actor["is_victim"]):
            if not actor["is_victim"]:
                facts["collision_agent"] = actor["id"]

        # offences -----------------------------------------------------------
        if _VICTIM_CLAUSE.search(cl) and not re.search(r"\b(no|without|not|n't)\b", cl, re.I):
            continue
        if _INNOCENT.search(cl):
            continue
        ranked = [(c, s) for c, s in res.resolve(cl) if c not in _NOISY]
        if not _ABSENCE.search(cl):
            from scenario.kb import get_kb as _gk
            ranked = [(c, s) for c, s in ranked if _gk().violation(c).get("grp") not in _NEEDS_ABSENCE_GROUPS
                      or c in _ABSENCE_EXEMPT]
        ranked = [(c, s) for c, s in ranked
                  if not any(c.startswith(pref) and not rx.search(cl) for pref, rx in _CUES)]
        for code in _example_hits(cl):
            ranked = [(code, 20)] + [(c, s) for c, s in ranked if c != code]
        if not ranked:
            continue
        top = ranked[0][1]
        if top < 3:
            continue
        floor = max(4, int(top * 0.4))
        picked = []
        from scenario.kb import get_kb
        _kb = get_kb()
        for c, sc_ in ranked:
            if not (sc_ >= floor or sc_ == top) or len(picked) >= 3:
                continue
            # a second label must be a different kind of offence (drunk + no seatbelt,
            # not "driver seatbelt" + "passenger seatbelt" for the same words)
            if picked and any(_kb.violation(c).get("grp") == _kb.violation(p_).get("grp") for p_ in picked):
                continue
            picked.append(c)
        codes = []
        for c in picked:
            if c in _FACT_DERIVED:
                continue
            codes.append(c)
        if not codes:
            continue
        # helmets for a group: rider + pillion(s)
        if group_mode and codes and set(codes) & _HELMET_CODES:
            codes = [c for c in codes if c not in _HELMET_CODES]
            partner_m = re.search(rf"\b(?:me|i) and my\s+{_KIN}\b", full, re.I)
            partner = b.get(b._key.get(f"my {partner_m.group(1).lower()}")) if partner_m else None
            if partner and (occupants or 2) <= 2:
                # "me and my sister … she was riding" — decided after all clauses
                b.events.append(_ev(b, subject, ["SAFETY_NO_HELMET_RIDER"], cl))
                partner["vehicle"] = partner.get("vehicle") or actor.get("vehicle")
                b.events.append(_ev(b, partner["id"], ["SAFETY_NO_HELMET_RIDER"], cl))
            else:
                b.events.append(_ev(b, subject, ["SAFETY_NO_HELMET_RIDER"], cl))
                pil = b.actor("group pillions", relation="friend", roles=["pillion"],
                              label="Your friends (pillion riders)" if (occupants or 2) > 2 else "Your pillion rider",
                              count=max(1, (occupants or 2) - 1), vehicle=actor.get("vehicle"))
                b.events.append(_ev(b, pil["id"], ["SAFETY_NO_HELMET_PILLION"], cl))
            if not codes:
                continue
        pair = re.match(rf"\s*(?:me|i) and my\s+{_KIN}\b|\s*my\s+{_KIN} and (?:i|me)\b", cl, re.I)
        if pair and not group_mode and codes:
            kin = (pair.group(1) or pair.group(2)).lower()
            partner = b.get(b._key.get(f"my {kin}"))
            if partner and self_a:
                both = [c for c in codes if c not in _HELMET_CODES | _SEATBELT_CODES]
                for who_ in {self_a["id"], partner["id"]}:
                    if both:
                        b.events.append(_ev(b, who_, both, cl))
                codes = [c for c in codes if c not in both]
                if not codes:
                    continue
        # "with my friend … without a helmet": gear named after another person is theirs
        gear = [c for c in codes if c in _HELMET_CODES | _SEATBELT_CODES]
        later = [(pos, k) for pos, k, _ in mentions if b._key.get(k) and b._key[k] != subject
                 and not re.match(r"(my|his|her)\s+\w+'s", cl[pos:pos + 30].lower())]
        neg = _ABSENCE.search(cl)
        if gear and later and neg and later[-1][0] < neg.start() and actor["relation"] == "self":
            other = b.get(b._key[later[-1][1]])
            for c in gear:
                codes.remove(c)
            moved = ["SAFETY_NO_HELMET_RIDER" if (c in _HELMET_CODES and other["roles"] != ["pillion"]) else c for c in gear]
            if other["roles"] == ["pillion"]:
                moved = ["SAFETY_NO_HELMET_PILLION" if c in _HELMET_CODES else c for c in moved]
            if not other["roles"]:
                riding = (set(actor["roles"]) & {"rider"} or (b.vget(actor.get("vehicle")) or {}).get("segment") == "two_wheeler") \
                    and not re.search(r"\b(his|her|their) own\b|\bfrom (his|her|their) (bike|scooter|scooty|motorcycle)\b|\bon (his|her|their) (bike|scooter|scooty)\b", cl, re.I)
                other["roles"] = (["pillion"] if riding else ["rider"]) if any(c in _HELMET_CODES for c in moved) else ["passenger"]
                if other["roles"] == ["pillion"]:
                    other["vehicle"] = other.get("vehicle") or actor.get("vehicle")
                    moved = ["SAFETY_NO_HELMET_PILLION" if c in _HELMET_CODES else c for c in moved]
            b.events.append(_ev(b, other["id"], list(dict.fromkeys(moved)), cl))
        # pillion / passenger codes land on that person, not the driver
        for c in list(codes):
            if c == "SAFETY_NO_HELMET_PILLION" and actor["relation"] == "self" and not group_mode:
                pil = b.actor("pillion friend", relation="friend", roles=["pillion"], label="The pillion rider",
                              vehicle=actor.get("vehicle"))
                codes.remove(c)
                b.events.append(_ev(b, pil["id"], [c], cl))
            if c in _SEATBELT_CODES and actor["relation"] != "self" and "passenger" not in actor["roles"] \
                    and re.search(r"\b(back|rear|passenger|friend)\b", cl, re.I):
                actor["roles"] = ["passenger"]
        if "passenger" in actor["roles"]:
            codes = [("SAFETY_NO_SEATBELT_PASSENGER" if c == "SAFETY_NO_SEATBELT_DRIVER" else c) for c in codes]
            codes = list(dict.fromkeys(codes))
        if "pillion" in actor["roles"]:
            codes = [("SAFETY_NO_HELMET_PILLION" if c == "SAFETY_NO_HELMET_RIDER" else c) for c in codes]
            codes = list(dict.fromkeys(codes))
        if "LIGHT_NO_HEADLIGHTS_NIGHT" in codes and re.search(r"\bpark\w*", cl, re.I):
            codes = ["PARK_NIGHT_NO_LIGHTS" if c == "LIGHT_NO_HEADLIGHTS_NIGHT" else c for c in codes]
        if any(c.startswith("SPEED_") and c != "SPEED_GOVERNOR_TAMPER" for c in codes) and re.search(r"governor|limiter", cl, re.I):
            codes = [c for c in codes if not c.startswith("SPEED_") or c == "SPEED_GOVERNOR_TAMPER"]
        if "COMM_REFUSAL" in codes and re.search(r"\b(ola|uber|rapido|app|booking|booked)\b", full, re.I):
            codes = ["COMM_RIDE_HAILING_REFUSAL" if c == "COMM_REFUSAL" else c for c in codes]
        if codes:
            b.events.append(_ev(b, subject, codes, cl))

    # "I let my driver take the car" / "my friend borrowed the bike" → the user owns it
    if self_a and facts["owner_permitted"] and re.search(r"\b(i|we) (let|lent|gave|allowed)\b|\bborrowed (my|our)\b", full, re.I):
        for v in b.vehicles:
            if v.get("owner") is None and not any(a.get("vehicle") == v["id"] and a.get("is_victim") for a in b.actors):
                v["owner"] = self_a["id"]
    emp = next((a for a in b.actors if a["relation"] == "employer"), None)
    if emp and self_a and re.search(r"\bour (truck|bus|lorry|van|cab|vehicle|vehicles|trucks)\b", full, re.I):
        for v in b.vehicles:
            if v.get("owner") == self_a["id"]:
                v["owner"] = emp["id"]
    # "his father owns the car"
    om = re.search(rf"\b(?:his|her|their)\s+(father|dad|mother|mom|parents?)\s+owns?\s+the\s+{_VEH}\b", full, re.I)
    if om:
        par = next((a for a in b.actors if a["relation"] == "parent"), None)
        seg = _vehicle_segment(om.group(2))
        for v in b.vehicles:
            if par and v.get("segment") == seg and v.get("owner") is None:
                v["owner"] = par["id"]
    # driving conduct belongs to whoever was driving that vehicle
    _CONDUCT = {"impaired_driving", "speeding", "signal_and_signage", "distracted_driving", "dangerous_driving",
                "lane_and_direction"}
    from scenario.kb import get_kb as _gk2
    for e in b.events:
        doer = b.get(e["actor"])
        if not doer or not set(doer["roles"]) & {"passenger"}:
            continue
        drivers_ = [x for x in b.actors if set(x["roles"]) & {"driver", "rider"} and x["id"] != doer["id"]
                    and not x.get("is_victim")]
        drv = next((x for x in drivers_ if x.get("vehicle") and x.get("vehicle") == doer.get("vehicle")), None) \
            or (drivers_[0] if len(drivers_) == 1 else None)
        if not drv:
            continue
        move = [c for c in e["offences"] if _gk2().violation(c).get("grp") in _CONDUCT]
        if move:
            e["offences"] = [c for c in e["offences"] if c not in move]
            b.events.append({"id": f"E{len(b.events) + 1}", "actor": drv["id"], "vehicle": drv.get("vehicle"),
                             "offences": move, "text": e["text"]})
    b.events = [e for e in b.events if e["offences"]]

    # "my friend riding" beats an inferred "you were on the scooter → you rode it"
    explicit_riders = [a for a in b.actors if a["relation"] != "self" and "rider" in a["roles"]
                       and re.search(rf"\b({re.escape(a['relation'])}|he|she)\s+(was\s+)?(riding|rode)\b", full, re.I)]
    if self_a and self_a.get("_inferred_role") and explicit_riders:
        self_a["roles"] = ["pillion"]
        explicit_riders[0]["vehicle"] = explicit_riders[0].get("vehicle") or self_a.get("vehicle")

    # vehicle sharing: passengers / pillions ride with the self driver
    driver_like = [a for a in b.actors if set(a["roles"]) & {"driver", "rider"} or a["relation"] == "self"]
    for a in b.actors:
        if set(a["roles"]) & {"passenger", "pillion"} and not a.get("vehicle") and driver_like:
            a["vehicle"] = driver_like[0].get("vehicle")
    # self with a vehicle but no role → driver/rider of it
    for a in b.actors:
        v = b.vget(a.get("vehicle"))
        if v and not a["roles"] and not a["is_victim"]:
            a["roles"] = ["rider" if v.get("segment") == "two_wheeler" else "driver"]
    if occupants:
        twos = [v for v in b.vehicles if v.get("segment") == "two_wheeler"]
        for a in b.actors:
            v = b.vget(a.get("vehicle"))
            if v and (a["relation"] == "self" or len(twos) == 1) and (v.get("segment") == "two_wheeler" or a["relation"] == "self"):
                v["occupants"] = occupants
    # a minor kin with no age: keep unknown (planner may ask)
    for a in b.actors:
        if a["relation"] in _MINOR_KIN and a["age"] is None and re.search(r"\b(minor|underage|under 18|under-age)\b", low):
            a["age"] = 17
    # extra passengers on a commercial vehicle
    xm = _EXTRA_PAX.search(full)
    if xm:
        drv = next((a for a in b.actors if set(a["roles"]) & {"driver"} and a.get("vehicle")), None)
        if drv and (b.vget(drv["vehicle"]) or {}).get("segment") != "two_wheeler":
            n = int(xm.group(5)) if xm.group(5) else None
            seg = (b.vget(drv["vehicle"]) or {}).get("segment")
            cap = {"three_wheeler": 3, "four_wheeler": 4}.get(seg)
            if xm.group(1) or xm.group(6) or (n and cap and n > cap):
                b.events.append(_ev(b, drv["id"], ["OVERLOAD_PASSENGER"], xm.group(0)))
    if facts["owner_permitted"] is None and re.search(r"\btook my (car|bike|scooter|scooty|vehicle)\b", low):
        facts["owner_permitted"] = None   # unknown: may be with or without permission

    return {"source": "rules", "actors": b.actors, "vehicles": b.vehicles, "events": b.events,
            "facts": facts, "asks": asks, "text": full}


def _example_hits(clause: str) -> List[str]:
    """Violations whose curated example phrasing appears in the clause."""
    from scenario.kb import get_kb
    n = " " + re.sub(r"[^a-z0-9 ]+", " ", clause.lower()) + " "
    n = re.sub(r"\s+", " ", n)
    hits = []
    for code, exs in get_kb().examples.items():
        for ex in exs:
            e = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", ex.lower())).strip() + " "
            if len(e) > 6 and e in n:
                hits.append(code)
                break
    return hits


def _ev(b: _Builder, actor_id: str, codes: List[str], text: str) -> dict:
    a = b.get(actor_id)
    return {"id": f"E{len(b.events) + 1}", "actor": actor_id, "vehicle": a.get("vehicle") if a else None,
            "offences": list(codes), "text": text.strip()[:160]}
