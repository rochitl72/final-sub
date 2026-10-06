"""
Clarification planner: ask at most one question per turn, and only about a
fact that changes who is charged or with what. Low-impact unknowns become
"may also apply" lines instead of questions.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .kb import SEVERITY_RANK, get_kb

_TEMPLATES = {
    "actor.age": "How old is {who}?",
    "actor.licence": "Did {who} have a valid driving licence?",
    "event.outcome": "Was anyone hurt?",
    "event.fled": "Did {who} leave the scene without helping or reporting?",
    "owner.permitted": "Did the owner let {who} use the vehicle?",
    "vehicle.segment": "Which vehicle was {who} on?",
    "vehicle.use": "Is {who}'s vehicle private, or a taxi / bus / goods vehicle?",
}


def who(actor: Optional[dict]) -> str:
    if not actor:
        return "the driver"
    if actor.get("relation") == "self":
        return "you"
    label = actor.get("label") or "the driver"
    return label[0].lower() + label[1:] if label.startswith(("Your", "The")) else label


def _subject(fact: str, f: dict) -> Optional[str]:
    if fact.startswith("actor.") or fact == "owner.permitted" or fact.startswith("vehicle."):
        return f.get("doer")
    return None


def pick(result: dict, asked: Optional[List[str]] = None) -> Optional[dict]:
    """The single most outcome-changing question, or None."""
    asked = set(asked or [])
    kb = get_kb()
    options: Dict[tuple, dict] = {}
    for f in result.get("conditional", []):
        for fact in f.get("missing", []):
            if fact not in _TEMPLATES:
                continue
            key = (fact, _subject(fact, f))
            if f"{fact}@{key[1]}" in asked:
                continue
            score = SEVERITY_RANK.get(kb.sev(f["code"]), 0) * 100000 + (f.get("fine_first") or 0)
            o = options.setdefault(key, {"fact": fact, "subject": key[1], "score": 0, "codes": []})
            o["score"] = max(o["score"], score)
            o["codes"].append(f["code"])
    if not options:
        return None
    best = max(options.values(), key=lambda o: o["score"])
    actor = result["actors"].get(best["subject"]) if best["subject"] else None
    q = _TEMPLATES[best["fact"]].format(who=who(actor))
    q = q.replace("is you?", "are you?").replace("Did you have", "Did you have")
    q = q[0].upper() + q[1:]
    chips = (kb.facts.get(best["fact"]) or {}).get("chips") or []
    if best["fact"] == "event.outcome":
        chips = [c for c in chips if c["value"] != "death"] + [c for c in chips if c["value"] == "death"]
    return {"fact": best["fact"], "subject": best["subject"], "question": q,
            "chips": [{"id": f"free:{c['label']}", "label": c["label"], "value": c["value"]} for c in chips],
            "codes": best["codes"]}


_YES = re.compile(r"^\s*(yes|yeah|yep|y|haan|ha|han|sure|correct|right|he did|she did|they did|true)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|nah|n|nahi|nahin|illa|not really|false|he didn'?t|she didn'?t)\b", re.I)


def parse_answer(q: dict, text: str) -> Any:
    """Value for the pending question from a chip label or typed text, or None."""
    t = (text or "").strip()
    for c in q.get("chips", []):
        if t.lower() == c["label"].lower():
            return c["value"]
    fact = q["fact"]
    low = t.lower()
    if fact == "actor.age":
        m = re.search(r"\b(\d{1,2})\b", low)
        if m:
            return int(m.group(1))
        if re.search(r"\b(minor|under ?18|underage|kid|child|teen)\b", low):
            return 17
        if re.search(r"\b(adult|over ?18|above ?18|major|grown)\b", low):
            return 18
        return None
    if fact == "actor.licence":
        if re.search(r"\blearner\b", low):
            return "learner"
        if re.search(r"\b(expired)\b", low):
            return "expired"
        if _NO.search(low) or re.search(r"\b(no licen|without|doesn'?t have|didn'?t have|unlicen)\w*", low):
            return "none"
        if _YES.search(low) or re.search(r"\b(valid|has a|had a|licensed)\b", low):
            return "valid"
        return None
    if fact == "event.outcome":
        if re.search(r"\b(died|dead|death|killed)\b", low):
            return "death"
        if re.search(r"\b(serious|fractur|hospital|icu|grievous|badly)\w*", low):
            return "grievous_injury"
        if _NO.search(low) or re.search(r"\b(no ?one|nobody|not hurt|no injur)\w*", low):
            return "none"
        if _YES.search(low) or re.search(r"\b(hurt|injur|minor)\w*", low):
            return "injury"
        return None
    if fact in ("event.fled", "owner.permitted"):
        if _YES.search(low) or re.search(r"\b(with permission|borrowed|let him|let her|left|ran)\b", low):
            return True
        if _NO.search(low) or re.search(r"\b(without (asking|permission)|stayed|didn'?t know)\b", low):
            return False
        return None
    if fact == "vehicle.segment":
        from graph_engine import get_graph_engine
        return get_graph_engine().detect_vehicle_segment(low)
    if fact == "vehicle.use":
        if re.search(r"\b(taxi|cab|bus|passenger|auto)\b", low):
            return "commercial_passenger"
        if re.search(r"\b(goods|truck|lorry|commercial)\b", low):
            return "commercial_goods"
        if re.search(r"\b(private|own|personal)\b", low):
            return "private"
    return None


def apply_answer(scenario: dict, q: dict, value: Any) -> None:
    fact, subject = q["fact"], q.get("subject")
    scope, _, key = fact.partition(".")
    if scope == "actor":
        for a in scenario.get("actors", []):
            if a["id"] == subject:
                a[key] = value
    elif scope == "vehicle":
        actor = next((a for a in scenario.get("actors", []) if a["id"] == subject), None)
        vid = (actor or {}).get("vehicle")
        for v in scenario.get("vehicles", []):
            if v["id"] == vid:
                v[key] = value
    elif fact == "owner.permitted":
        scenario.setdefault("facts", {})["owner_permitted"] = value
    else:
        scenario.setdefault("facts", {})[key] = value
