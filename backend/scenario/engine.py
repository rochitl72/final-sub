"""
Scenario Engine entry points used by the chatbot.

maybe_handle(session, text) returns a turn payload when the message is a
multi-person / multi-offence / incident story, an answer to the engine's own
question, or an edit to the current scenario ("actually he was 19") —
otherwise None, and the regular single-offence pipeline answers.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Optional

from . import extract_rules, planner
from .compose import compose
from .reasoner import analyse, signature

log = logging.getLogger("drivelegal.scenario")

_EDIT_RE = re.compile(r"\b(actually|what if|wait|correction|sorry,? (he|she|it|i)|no,? (he|she|it|i))\b|"
                      r"^\s*(he|she|they|my \w+) (was|is|had|has|didn'?t|did not|wasn'?t)\b|"
                      r"\b(no ?one|nobody) (was|got) (hurt|injured)\b|\bthe owner (didn'?t|did not|knew)\b", re.I)
_LONG_STORY = 18


def _ctx(session: dict):
    return session.get("state_code"), session.get("city_code"), session.get("city_name")


_QUESTION_START = re.compile(r"^\s*(can|could|is|are|does|do|should|what|how|when|where|why|which|will|would)\b", re.I)
_NARRATIVE = re.compile(r"\b(was|were|got|had|did|caught|stopped|hit|took|borrowed|drove|rode|fined|ran|jumped)\b", re.I)


def _general_question(text: str) -> bool:
    """'can the police check my phone?' — a question about the law, not a story."""
    return bool(_QUESTION_START.match(text)) and not _NARRATIVE.search(text)


def _complex(result: dict, rules: dict, text: str) -> bool:
    if _general_question(text):
        return False
    people = [p for p in result["persons"] if p["findings"]]
    if not people:
        return False          # nothing definite from the story itself → regular pipeline
    n_find = sum(len(p["findings"]) for p in people) + len(result.get("conditional", []))
    others = [p for p in people if p["actor"].get("relation") != "self"]
    asks = set(rules.get("asks") or [])
    if len(people) >= 2 or n_find >= 2 or others:
        return True
    if "liability" in asks and n_find:
        return True
    if rules["facts"].get("collision") and n_find:
        return True
    return False


def _llm_worth_it(rules: dict, text: str) -> bool:
    words = len(text.split())
    clauses = len(extract_rules._clauses(text))
    return words >= _LONG_STORY and (clauses >= 2 or rules["facts"].get("collision"))


def analyse_text(text: str, *, state_code=None, city_code=None, use_llm: bool = False, llm_call=None) -> dict:
    """Full pipeline without a session (used by evals and tests)."""
    rules = extract_rules.extract(text)
    scn = rules
    if use_llm:
        from . import extract_llm
        llm = extract_llm.extract(text, call=llm_call)
        if llm:
            scn = extract_llm.merge(llm, rules)
    result = analyse(scn, state_code=state_code, city_code=city_code)
    q = planner.pick(result)
    return {"scenario": scn, "result": result, "question": q, "rules": rules}


def _render(session: dict, text: str, scn: dict, *, lead: str = "", intent: str = "scenario",
            previous: Optional[list] = None) -> Optional[dict]:
    state, city, city_name = _ctx(session)
    result = analyse(scn, state_code=state, city_code=city)
    if not result["persons"] and not result.get("conditional"):
        return None
    q = planner.pick(result, asked=session.get("scenario_asked"))
    if previous is not None:
        diff = _diff(previous, signature(result), result)
        if diff:
            lead = (lead + " " if lead else "") + diff
    reply, payload = compose(result, city_name=city_name, question=q, asks=scn.get("asks") or [], lead=lead)
    cards = [f["card"] for p in result["persons"] for f in p["findings"] if f.get("card")]
    seen, uniq = set(), []
    for c in cards:
        if c["violation_code"] not in seen:
            seen.add(c["violation_code"])
            uniq.append(c)
    session["scenario"] = result["scenario"]
    session["scenario_sig"] = signature(result)
    session["scenario_q"] = q
    session["stage"] = "answered"
    session["last_user_story"] = scn.get("text") or text
    if uniq:
        session["violation_code"] = uniq[0]["violation_code"]
        session["last_fine_card"] = uniq[0]
        session["card_history"] = (session.get("card_history") or []) + uniq[1:]
    session.setdefault("messages", []).append({"role": "user", "content": text})
    session["messages"].append({"role": "assistant", "content": reply})
    payload["source"] = scn.get("source")
    chips = [{"id": c["id"], "label": c["label"]} for c in (q or {}).get("chips", [])] or None
    return {"intent": intent, "reply": reply, "fine_card": uniq[0] if uniq else None,
            "fine_cards": uniq, "chips": chips,
            # One tap answers the question (radio, not checkboxes).
            "selection_mode": "single" if chips else None,
            # An update rewrites the previous answer in place instead of adding
            # a second, near-identical one under it.
            "replace_last": intent == "scenario_update",
            "allow_text": True, "scenario": payload, "detail_table": None, "explanation": None}


def _diff(before: list, after: list, result: dict) -> str:
    b = {(a, c) for a, c, _, _ in before}
    a_ = {(a, c) for a, c, _, _ in after}
    added, removed = a_ - b, b - a_
    if not added and not removed:
        return "Updated — the charges stay the same."
    actors = result["actors"]
    from .kb import get_kb
    kb = get_kb()

    def fmt(pairs):
        return "; ".join(f"{kb.name(c)} ({planner.who(actors.get(aid)) if aid in actors else 'removed person'})"
                         for aid, c in sorted(pairs))
    bits = []
    if added:
        bits.append(f"**Now applies:** {fmt(added)}.")
    if removed:
        bits.append(f"**No longer applies:** {fmt(removed)}.")
    return "Updated. " + " ".join(bits)


def _apply_edit(scn: dict, text: str) -> bool:
    """Fold a correction ("actually he was 19", "no one was hurt") into the scenario."""
    delta = extract_rules.extract(text)
    changed = False
    actors = scn.get("actors", [])
    others = [a for a in actors if a.get("relation") != "self"]
    me = next((a for a in actors if a.get("relation") == "self"), None)
    low = text.lower()
    target = None
    m = re.search(r"\bmy (\w+)\b", low)
    if m:
        target = next((a for a in others if a.get("relation") == m.group(1)), None)
    if target is None and re.search(r"\b(he|she|him|her|they)\b", low):
        # most recently described driver-like person who isn't the user
        target = next((a for a in reversed(others) if set(a.get("roles") or []) & {"driver", "rider"}
                       and not a.get("is_victim")), None) \
            or next((a for a in reversed(others) if not a.get("is_victim")), None) \
            or (others[-1] if others else None)
    if target is None and re.search(r"\b(i|me|my)\b", low):
        target = me
    age = re.search(r"\b(\d{1,2})\b(?:\s*(?:years?|yrs?)(?:\s*old)?)?", low)
    if target and age and re.search(r"\b(was|is|aged|age|years?|old)\b", low):
        target["age"] = int(age.group(1))
        changed = True
    if target:
        for da in delta["actors"]:
            if da.get("licence"):
                target["licence"] = da["licence"]
                changed = True
                break
        if re.search(r"\b(had|has|with) (a |his |her |my )?(valid )?(driving )?licen[cs]e\b", low) and not re.search(r"\b(no|not|n't)\b", low):
            target["licence"] = "valid"
            changed = True
    facts = scn.setdefault("facts", {})
    for k in ("outcome", "fled", "owner_permitted", "speed_over_pct", "zone", "time"):
        v = delta["facts"].get(k)
        if v is not None and v != facts.get(k) and not (k == "outcome" and v == "none" and not re.search(r"\b(hurt|injur)", low)):
            facts[k] = v
            changed = True
    if re.search(r"\b(no ?one|nobody) (was|got) (hurt|injured)\b", low):
        facts["outcome"] = "damage" if facts.get("collision") else "none"
        changed = True
    if re.search(r"\bthe owner (didn'?t|did not) know\b|\bwithout (asking|permission)\b", low):
        facts["owner_permitted"] = False
        changed = True
    return changed


def maybe_handle(session: dict, text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    use_llm = not session.get("_force_rules")

    # 1. Answer to our own question.
    q = session.get("scenario_q")
    if q and session.get("scenario"):
        value = planner.parse_answer(q, text)
        if value is not None:
            scn = copy.deepcopy(session["scenario"])
            planner.apply_answer(scn, q, value)
            session.setdefault("scenario_asked", []).append(f"{q['fact']}@{q.get('subject')}")
            label = next((c["label"] for c in q.get("chips", []) if c.get("value") == value), None) or text
            return _render(session, text, scn, intent="scenario_update", previous=session.get("scenario_sig"),
                           lead=f"**{q.get('question') or 'Answer'}** → {label}.")

    # 2. A correction to the current scenario.
    if session.get("scenario") and _EDIT_RE.search(text):
        scn = copy.deepcopy(session["scenario"])
        if _apply_edit(scn, text):
            return _render(session, text, scn, intent="scenario_update", previous=session.get("scenario_sig"))

    # 3. A new story.
    rules = extract_rules.extract(text)
    state, city, _ = _ctx(session)
    try:
        rules_result = analyse(rules, state_code=state, city_code=city)
    except Exception:
        log.exception("scenario reasoner failed on rules extraction")
        return None
    complex_ = _complex(rules_result, rules, text)
    scn = rules
    if use_llm and (complex_ or _llm_worth_it(rules, text)):
        from . import extract_llm
        llm = extract_llm.extract(text)
        if llm:
            scn = extract_llm.merge(llm, rules)
            try:
                complex_ = complex_ or _complex(analyse(scn, state_code=state, city_code=city), scn, text)
            except Exception:
                log.exception("scenario reasoner failed on LLM extraction")
                scn = rules
    if not complex_:
        return None
    session["scenario_asked"] = []
    try:
        return _render(session, text, scn)
    except Exception:
        log.exception("scenario render failed")
        return None


def driver_context_turn(session: dict, *, repeat: bool, minor: bool, no_licence: bool) -> Optional[dict]:
    """The legacy single-offence flow asks a checkbox question (repeat / no
    licence / under 18). When any box is ticked, re-run the whole story through
    the Scenario Engine so the answer becomes a proper person-by-person
    breakdown (e.g. under 18 → the guardian is charged under s.199A) instead of
    a hedged one-liner bolted onto the old card."""
    story = (session.get("last_user_story") or "").strip()
    if not story or not (repeat or minor or no_licence):
        return None
    extra = []
    if minor:
        extra.append("I am 16 years old.")
    if no_licence and not minor:
        extra.append("I don't have a valid driving licence.")
    text = story.rstrip(". ") + ". " + " ".join(extra) if extra else story
    rules = extract_rules.extract(text)
    if repeat:
        rules.setdefault("facts", {})["repeat"] = True
    session["scenario_asked"] = []
    lead_bits = []
    if minor:
        lead_bits.append("under 18")
    if no_licence and not minor:
        lead_bits.append("no valid licence")
    if repeat:
        lead_bits.append("repeat offence")
    lead = "Updated with what you ticked (" + ", ".join(lead_bits) + ")."
    try:
        out = _render(session, text, rules, lead=lead, intent="scenario_update")
    except Exception:
        log.exception("driver-context scenario render failed")
        return None
    if out is not None:
        session["last_user_story"] = story          # keep the user's own words
    return out


def chip_answer_turn(session: dict, chip_ids: list) -> Optional[dict]:
    """Checkbox/radio submission for the Scenario Engine's own question.
    Returns None when the ids don't belong to the pending scenario question."""
    q = session.get("scenario_q")
    if not q or not session.get("scenario"):
        return None
    labels = [c["label"] for c in q.get("chips", []) if c["id"] in set(chip_ids or [])]
    if not labels:
        return None
    # If several were ticked, the most serious option wins (chips are ordered
    # least → most serious).
    return maybe_handle(session, labels[-1])
