"""
Grounded reply for a scenario: one section per person, every amount and
section taken from the computed result (never from an LLM).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .kb import get_kb
from .planner import who

_ROLE_WORD = {"driver": "driving", "rider": "riding", "pillion": "pillion rider", "passenger": "passenger",
              "owner": "owner", "guardian": "parent / guardian", "conductor": "conductor",
              "operator": "operator", "employer": "employer"}
_COND_PLAIN = {
    "actor.age": "if {who} is under 18",
    "actor.licence": "if {who} had no valid licence",
    "event.outcome": "if someone was hurt",
    "event.fled": "if {who} left without reporting",
    "owner.permitted": "if the owner allowed it",
    "vehicle.segment": "depending on the vehicle",
    "vehicle.use": "if it is a commercial vehicle",
    "event.speed_over_pct": "if you were 50% or more over the limit",
}


def _rupees(n: Optional[int]) -> str:
    if n is None:
        return "—"
    s = str(int(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"₹{s}"


def _sec(f: dict) -> str:
    sec = f.get("section") or ""
    if not sec:
        return ""
    if sec.upper().startswith("BNS"):
        return sec.replace("BNS", "BNS §", 1).replace("§ ", "§")
    return f"MV Act §{sec}"


def _person_header(p: dict, actors: Dict[str, dict], vehicles: Dict[str, dict]) -> str:
    a = p["actor"]
    bits = []
    if a.get("age") is not None:
        bits.append(str(a["age"]))
    roles = [r for r in a.get("roles", []) if r in _ROLE_WORD]
    v = vehicles.get(a.get("vehicle") or "") or {}
    vlabel = v.get("label") or {"two_wheeler": "two-wheeler", "four_wheeler": "car", "heavy_vehicle": "truck",
                                 "four_wheeler_plus": "bus", "three_wheeler": "auto"}.get(v.get("segment") or "", "")
    if roles:
        r = roles[0]
        if r in ("driver", "rider") and vlabel:
            bits.append(f"{_ROLE_WORD[r]} the {vlabel}")
        else:
            bits.append(_ROLE_WORD[r])
    owned = [x for x in vehicles.values() if x.get("owner") == a["id"]]
    if owned and "owner" not in roles and not any(r in ("driver", "rider") and v.get("owner") == a["id"] for r in roles):
        bits.append(f"owner of the {owned[0].get('label') or 'vehicle'}")
    if any(f.get("role") == "guardian" for f in p["findings"]) and "parent / guardian" not in bits:
        bits.append("parent / guardian")
    label = a.get("label") or "Someone"
    if a.get("count", 1) > 1:
        label = f"{label} ×{a['count']}"
    return f"**{label}**" + (f" ({', '.join(bits)})" if bits else "")


def _finding_line(f: dict, repeat: bool = False) -> str:
    sec = _sec(f)
    name = f["name"]
    amount = _rupees(f.get("fine_first"))
    each = " each" if f.get("count", 1) > 1 else ""
    parts = [f"**{name}**" + (f" ({sec})" if sec else "")]
    money = f"**{amount}**{each} first offence" if f.get("fine_first") else "fine set by the court"
    if f.get("fine_repeat") and f.get("fine_repeat") != f.get("fine_first"):
        if repeat:
            money = f"**{_rupees(f['fine_repeat'])}**{each} repeat offence (first offence: {amount})"
        else:
            money += f", {_rupees(f['fine_repeat'])} repeat"
    parts.append(money)
    if f.get("imprisonment"):
        parts.append(f"jail: {f['imprisonment']}")
    if f.get("compoundable") is False:
        parts.append("not compoundable — goes to court")
    line = "• " + " — ".join(parts[:1]) + ": " + "; ".join(parts[1:])
    tags = []
    if f.get("deemed"):
        tags.append("deemed liable under s.199A")
    if f.get("certainty") == "may_be_liable":
        tags.append("may apply")
    if tags:
        line += f" ({', '.join(tags)})"
    return line + "."


def compose(result: dict, *, city_name: Optional[str], question: Optional[dict],
            asks: List[str], lead: str = "") -> Tuple[str, dict]:
    kb = get_kb()
    actors, vehicles, facts = result["actors"], result["vehicles"], result["facts"]
    offenders = [p for p in result["persons"] if not p["is_victim"]]
    victims = [p for p in result["persons"] if p["is_victim"]]
    loc = f" in {city_name}" if city_name else ""
    lines: List[str] = []
    n_people = len([p for p in offenders if any(not f.get("juvenile") for f in p["findings"])])
    n_off = len({f["code"] for p in offenders for f in p["findings"]})
    if lead:
        lines.append(lead.strip())
    if offenders and len(offenders) > 1:
        lines.append(f"Here's who is liable for what{loc} — **{n_off} offence{'s' if n_off != 1 else ''}**, "
                     "person by person:")
    elif offenders or victims or result.get("conditional"):
        lines.append(f"Here's what applies{loc}:")

    head_end = len(lines)
    payload_people = []
    for p in offenders:
        lines.append("")
        lines.append(_person_header(p, actors, vehicles))
        juvenile = [f for f in p["findings"] if f.get("juvenile")]
        normal = [f for f in p["findings"] if not f.get("juvenile")]
        for f in normal:
            lines.append(_finding_line(f, repeat=bool(facts.get("repeat"))))
        if juvenile:
            names = ", ".join(f["name"].lower() for f in juvenile)
            lines.append(f"• Offences committed: {names}. Because the driver is a juvenile, s.199A charges the "
                         "guardian and the vehicle owner for these; the child is dealt with under the Juvenile "
                         "Justice Act and cannot get a licence until 25.")
        acts = list(dict.fromkeys(f["licence_action"] for f in normal if f.get("licence_action")
                                  and f["certainty"] == "liable" and not f.get("deemed")
                                  and f.get("role") in ("driver", "rider", "guardian", "owner")))
        for la in acts[:2]:
            lines.append(f"• Licence / vehicle: {la}.")
        liable = [f for f in normal if f["certainty"] == "liable"]
        if len(liable) >= 2 or any(f.get("count", 1) > 1 for f in liable):
            if facts.get("repeat"):
                tot = sum((f.get("fine_repeat") or f.get("fine_first") or 0) * f.get("count", 1) for f in liable)
                lines.append(f"• Total as repeat offences: **{_rupees(tot)}**")
            else:
                lines.append(f"• Total if first offences: **{_rupees(p['total_first'])}**")
        payload_people.append(_person_payload(p, "offender"))

    for p in victims:
        lines.append("")
        a = p["actor"]
        lines.append(f"**Also note — {a.get('label', 'the other person')}** (the party who was hit): their own "
                     "offences are dealt with separately and don't excuse the other driver.")
        for f in p["findings"]:
            if f["certainty"] == "liable":
                lines.append(_finding_line(f))
        payload_people.append(_person_payload(p, "victim"))

    tail_start = len(lines)
    if not offenders and question:
        # Nothing definite yet — say what hinges on the question instead of an empty answer.
        pending = [f for f in result.get("conditional", []) if f["code"] in question.get("codes", [])]
        by_actor: Dict[str, List[str]] = {}
        for f in pending:
            names = by_actor.setdefault(f["actor"], [])
            if f["name"].lower() not in names:
                names.append(f["name"].lower())
        for aid, names in by_actor.items():
            a = actors.get(aid) or {}
            lines.append("")
            lines.append(f"**{a.get('label') or 'The other party'}** could face: {' or '.join(names)} — "
                         "which one depends on your answer below.")
    cond = [f for f in result.get("conditional", []) if not question or f["code"] not in question.get("codes", [])]
    if cond:
        lines.append("")
        lines.append("**May also apply** (depends on details):")
        seen = set()
        for f in cond:
            k = (f["code"], f["actor"])
            if k in seen:
                continue
            seen.add(k)
            target = actors.get(f["actor"])
            doer = actors.get(f.get("doer"))
            cond_txt = ", ".join(_COND_PLAIN.get(m, m).format(who=who(doer)) for m in f.get("missing", [])) or "depending on details"
            amt = f" — {_rupees(f['fine_first'])}" if f.get("fine_first") else ""
            lines.append(f"• {f['name']} for {who(target)}{amt}, {cond_txt}.")

    steps = _next_steps(result, asks, actors, kb)
    if steps:
        lines.append("")
        lines.append("**What to do now**")
        lines.extend(f"• {s}" for s in steps)

    me = next((a for a in actors.values() if a.get("relation") == "self"), None)
    if "liability" in asks and me:
        mine = next((p for p in offenders if p["actor"]["id"] == me["id"]), None)
        lines.append("")
        if mine:
            names = ", ".join(f["name"].lower() for f in mine["findings"][:3])
            lines.append(f"**Are you liable?** Yes — on these facts you can be charged for: {names}.")
        elif any(f["actor"] == me["id"] for f in result.get("conditional", [])):
            lines.append("**Are you liable?** Possibly — it depends on the question below.")
        else:
            lines.append("**Are you liable?** Not on these facts — no offence is charged to you.")

    assumptions = (["repeat offence — the higher repeat fines apply"] if facts.get("repeat")
                   else ["first offence for everyone"]) + result.get("assumptions", [])
    if city_name:
        assumptions.append(f"fines from the {city_name} schedule where it has one, else state / central")
    lines.append("")
    lines.append("Assumed: " + "; ".join(assumptions) + ".")
    if any(f["severity"] == "criminal" for p in offenders for f in p["findings"]):
        lines.append("Criminal charges (BNS) are decided by a court — talk to a lawyer before giving any statement.")

    if question:
        lines.append("")
        lines.append(f"**One question so I can be exact:** {question['question']}")

    payload = {"people": payload_people,
               "may_apply": [{"code": f["code"], "name": f["name"], "for": who(actors.get(f["actor"])),
                              "missing": f.get("missing", []), "fine_first": f.get("fine_first")}
                             for f in cond],
               "question": ({k: question[k] for k in ("fact", "question", "chips")} if question else None),
               "assumptions": assumptions, "facts": facts,
               # split for card UIs: intro text, per-person cards (people[]), then the tail
               "head": "\n".join(lines[:head_end]).strip(), "tail": "\n".join(lines[tail_start:]).strip()}
    return "\n".join(lines).strip(), payload


def _person_payload(p: dict, kind: str) -> dict:
    a = p["actor"]
    return {"id": a["id"], "label": a.get("label"), "relation": a.get("relation"), "roles": a.get("roles"),
            "age": a.get("age"), "count": a.get("count", 1), "kind": kind, "total_first": p.get("total_first"),
            "offences": [{"code": f["code"], "name": f["name"], "section": _sec(f), "fine_first": f.get("fine_first"),
                          "fine_repeat": f.get("fine_repeat"), "imprisonment": f.get("imprisonment"),
                          "compoundable": f.get("compoundable"), "certainty": f["certainty"],
                          "deemed": bool(f.get("deemed")), "juvenile": bool(f.get("juvenile")),
                          "licence_action": f.get("licence_action"), "severity": f.get("severity")}
                         for f in p["findings"]]}


def _next_steps(result: dict, asks: List[str], actors: Dict[str, dict], kb) -> List[str]:
    facts = result["facts"]
    steps = []
    me = next((a for a in actors.values() if a.get("relation") == "self"), None)
    hurt = facts.get("outcome") in ("injury", "grievous_injury", "death")
    if facts.get("collision") and facts.get("outcome") == "death":
        steps.append("Report the death at the nearest police station so an FIR is registered (MV Act §134), and "
                     "keep the post-mortem report and death certificate — they're needed for the case and any claim.")
    elif facts.get("collision") and (hurt or facts.get("outcome") in (None, "unknown")):
        steps.append(("Make sure the injured get medical help" if hurt else "If anyone is hurt, get medical help first")
                     + " (call 112, or 108 for an ambulance) and report the accident at the nearest police "
                     "station within 24 hours (MV Act §134).")
    if facts.get("fled") and me and me.get("is_victim"):
        steps.append("Note the other vehicle's number, colour and direction, keep photos and any CCTV/dashcam "
                     "footage, and file a complaint or FIR at the police station.")
        steps.append("If the driver can't be traced and someone was killed or grievously hurt, MV Act §161 "
                     "provides fixed hit-and-run compensation (₹2,00,000 for death, ₹50,000 for grievous hurt).")
    elif facts.get("collision") and me and me.get("is_victim"):
        steps.append("Exchange details, take photos, report it to the police and inform your insurer.")
    if "complaint" in asks:
        steps.append("To complain, call the city traffic police helpline or write to the RTO with the vehicle "
                     "number, date, time and place; many cities also take complaints on their traffic police "
                     "app or WhatsApp number.")
    if any(f["severity"] == "criminal" for p in result["persons"] for f in p["findings"] if not p["is_victim"]) \
            and not steps:
        steps.append("Keep a copy of the FIR / challan and get legal advice before the court date.")
    return steps
