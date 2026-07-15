#!/usr/bin/env python3
"""
clarification_engine.py — Smart clarification MCQ builder
==========================================================
Shared helpers for violation / incident / driver-context clarifications,
slot-filling MCQ envelopes, and out-of-scope detection.
Produces vertical checkbox option sets with a Proceed action and an
always-present "Something else" escape hatch where appropriate.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# ── Standard option ids ──────────────────────────────────────────────────────

CHIP_OTHER = {
    "id":    "clarify:other",
    "label": "Something else — I'll describe it",
}
CHIP_NONE = {
    "id":    "clarify:none",
    "label": "None of these apply to me",
}
CHIP_UNSURE = {
    "id":    "clarify:unsure",
    "label": "I'm not sure — help me figure it out",
}

# Topic-router chips shown when the graph has zero relevant violation matches.
TOPIC_CHIPS: List[dict] = [
    {"id": "topic:safety",   "label": "Safety gear (helmet, seatbelt, etc.)"},
    {"id": "topic:speed",    "label": "Speed / signals / lane / wrong side"},
    {"id": "topic:docs",     "label": "Documents (licence, RC, insurance, PUC)"},
    {"id": "topic:parking",  "label": "Parking / stopping / horn / obstruction"},
    {"id": "topic:accident", "label": "Accident / collision / hit something"},
    {"id": "topic:dui",      "label": "Drink-driving / alcohol"},
    {"id": "topic:overload", "label": "Overloading / goods / dimensions"},
    {"id": "topic:officer",  "label": "Officer stopped me / got a challan"},
]

TOPIC_SEARCH_HINTS: dict = {
    "topic:safety":   "helmet seatbelt safety gear protective equipment rider pillion",
    "topic:speed":    "speed overspeed racing red light signal jump wrong side lane",
    "topic:docs":     "licence license rc registration insurance puc pollution certificate",
    "topic:parking":  "parking stop stand horn haphazard obstruction no parking",
    "topic:accident": "accident collision crash hit injury damage",
    "topic:dui":      "drunk alcohol drink driving DUI intoxicated",
    "topic:overload": "overload overweight axle goods projecting dimension",
    "topic:officer":  "officer police challan fine penalty stopped pulled over",
}

_UNSURE_FREEFORM_PROMPT = (
    "No worries — lots of situations don't fit a neat category. Tell me in "
    "your own words: what did the officer say, or what were you doing when "
    "you were stopped? Even rough details help (vehicle, place, time)."
)
# Public alias for dialog_manager imports.
UNSURE_FREEFORM_PROMPT = _UNSURE_FREEFORM_PROMPT

# Minimum resolver score before we show violation chips at all.
VIO_CHIP_MIN_SCORE = 5

# Tokens too generic to count as "user mentioned this violation theme".
_GENERIC_TOKENS = {
    "hit", "driving", "drive", "driver", "vehicle", "car", "road", "while",
    "just", "other", "violation", "fine", "police", "said", "tell", "am",
    "im", "i", "me", "my", "the", "a", "an", "and", "or", "no", "not",
    "cow", "dog", "animal", "died", "dead", "accident", "crash",
}

# Positive traffic-law signal — prevents false out-of-scope on edge cases.
_TRAFFIC_SIGNAL_RE = re.compile(
    r"\b("
    r"drive|driving|driver|road|traffic|challan|chalan|fine|penalty|"
    r"police|cop|rto|parivahan|vehicle|car|bike|scooter|truck|bus|"
    r"helmet|seatbelt|seat\s+belt|signal|red\s+light|speed|overtake|"
    r"park|parking|licen[cs]e|insurance|puc|pollution|rc\b|registration|"
    r"accident|crash|collision|hit|knock|pedestrian|"
    r"mv\s*act|motor\s+vehicle|highway|expressway|toll|checkpost|"
    r"drunk|alcohol|dui|overload|overloaded|wrong\s+side|one\s+way|"
    r"document|digilocker|mparivahan|e\s*challan|pay\s+fine|contest"
    r")\b",
    re.I,
)

_OUT_OF_SCOPE_RE = re.compile(
    r"\b("
    # Weather / sports / entertainment
    r"weather\s+forecast|today'?s?\s+weather|tomorrow'?s?\s+weather|"
    r"cricket\s+score|ipl\s+match|football\s+score|world\s+cup\s+score|"
    r"movie\s+review|bollywood|netflix|hotstar|prime\s+video|"
    r"song\s+lyrics|spotify|recommend\s+a\s+movie|"
    # Food / lifestyle / shopping
    r"recipe\s+for|how\s+to\s+cook|restaurant\s+near|zomato|swiggy|"
    r"amazon\s+order|flipkart|myntra|track\s+my\s+package|"
    # Tech / homework / finance unrelated to traffic
    r"write\s+(me\s+)?(a\s+)?(python|javascript|java|code|essay|"
    r"poem|story|email|resume|cover\s+letter)|"
    r"do\s+my\s+homework|solve\s+this\s+math|assignment\s+help|"
    r"bitcoin|crypto\s+price|stock\s+market|share\s+price|mutual\s+fund|"
    r"salary\s+negotiation|job\s+interview\s+tips|"
    # Personal / medical / relationship
    r"relationship\s+advice|break\s?up|dating\s+app|tinder|"
    r"medical\s+diagnosis|symptoms\s+of|pregnant|anxiety\s+disorder|"
    r"depression\s+help|therapy\s+session|"
    # Misc chatbot bait
    r"tell\s+me\s+a\s+joke|play\s+a\s+game|trivia\s+question|"
    r"who\s+won\s+(the\s+)?(election|debate)|political\s+opinion|"
    r"astrology|horoscope|kundli|tarot|"
    r"translate\s+this\s+(to|into)|grammar\s+check|"
    r"gym\s+workout|diet\s+plan|weight\s+loss|"
    r"property\s+tax|electricity\s+bill|water\s+bill|"
    r"visa\s+application|passport\s+renewal|"
    r"whatsapp\s+hack|instagram\s+followers"
    r")\b",
    re.I,
)

# Very short non-traffic utterances — nudge instead of violation matching.
_VAGUE_UTTERANCE_RE = re.compile(
    r"^(?:help|hmm+|um+|uh+|idk|i\s+don'?t\s+know|not\s+sure|maybe|"
    r"what\??|how\??|why\??|ok\??|so\??)[!.?\s]*$",
    re.I,
)

_VAGUE_NUDGE_REPLY = (
    "I'm here for Indian traffic law — fines, challans, accidents, and "
    "documents. Tell me what happened on the road, or tick the closest "
    "topic below and hit Proceed."
)

_GREETING_ONLY_RE = re.compile(
    r"^(?:hi|hello|hey|hii+|good\s+(?:morning|afternoon|evening)|namaste|"
    r"howdy|sup|what'?s\s+up)[!.?\s]*$",
    re.I,
)

_GREETING_REPLY = (
    "Hey — I'm DriveLegal, your traffic-law buddy for India. Tell me what "
    "happened on the road (or what you're worried about — a challan, an "
    "accident, documents, insurance) and I'll find the exact rule and fine."
)

_OUT_OF_SCOPE_REPLY = (
    "That's a bit outside what I cover — I'm built for Indian traffic law, "
    "challans, accidents, documents, and road safety. If you have a driving "
    "or traffic question, describe it in plain words and I'll help."
)

_AGE_RE = re.compile(
    r"(?:^|\b)(?:i(?:'m|\s+am)?\s+)?(\d{1,2})(?:\s+years?\s+old)?(?:\s|$|\.)",
    re.I,
)
_DENIAL_RE = re.compile(
    r"\b(no|not|none|without|just|only)\b.*\b(violation|offence|offense|challan|"
    r"other\s+than|except|nothing\s+else)\b|\b(only|just)\s+(hit|cow|animal|"
    r"accident)\b",
    re.I,
)
_INCIDENT_RE = re.compile(
    r"\b(hit|knocked|ran\s+over|collid|crash|accident)\b.*\b(cow|animal|dog|"
    r"pedestrian|cyclist|person|wildlife|stray)\b|\b(cow|animal|dog)\b.*\b("
    r"hit|knock|kill|died|dead)\b",
    re.I,
)
_SHORT_SLOT_RE = re.compile(
    r"^(?:yes|no|yep|nope|ok|okay|sure|thanks|thank\s+you|\d{1,2}|"
    r"i(?:'m|\s+am)?\s+\d{1,2}(?:\s+years?\s+old)?)\.?$",
    re.I,
)


def parse_age(text: str) -> Optional[int]:
    """Extract a plausible driver/rider age from free text."""
    if not text:
        return None
    m = _AGE_RE.search(text.strip())
    if not m:
        # Bare number reply to an age question.
        bare = text.strip()
        if bare.isdigit() and 1 <= int(bare) <= 99:
            return int(bare)
        return None
    age = int(m.group(1))
    return age if 1 <= age <= 99 else None


def is_short_slot_answer(text: str) -> bool:
    """True when the message looks like a brief slot fill, not a new story."""
    t = (text or "").strip()
    if not t:
        return False
    if parse_age(t) is not None:
        return True
    return bool(_SHORT_SLOT_RE.match(t))


def is_violation_denial(text: str) -> bool:
    return bool(_DENIAL_RE.search(text or ""))


def looks_like_incident(text: str) -> bool:
    return bool(_INCIDENT_RE.search(text or ""))


def is_traffic_related(text: str) -> bool:
    """True when the message plausibly concerns roads, vehicles, or enforcement."""
    return bool(_TRAFFIC_SIGNAL_RE.search(text or ""))


def scope_response(text: str) -> Optional[dict]:
    """Return a canned reply for greetings or clearly off-topic input.

    Runs before LLM / violation resolution so unrelated queries never produce
    spurious violation MCQs. Returns None when the message should continue
    through the normal pipeline."""
    t = (text or "").strip()
    if not t:
        return None
    if _GREETING_ONLY_RE.match(t):
        return {
            "intent":       "narrate",
            "reply":        _GREETING_REPLY,
            "fine_card":    None,
            "detail_table": None,
            "chips":        None,
            "explanation":  None,
            "scope":        "greeting",
        }
    if _VAGUE_UTTERANCE_RE.match(t) and not is_traffic_related(t):
        mcq = build_zero_match_clarification({})
        mcq["reply"] = _VAGUE_NUDGE_REPLY + "\n\n" + mcq["question"]
        mcq["question"] = mcq["reply"]
        mcq["scope"] = "vague_nudge"
        return mcq
    if _OUT_OF_SCOPE_RE.search(t) and not is_traffic_related(t):
        return {
            "intent":       "narrate",
            "reply":        _OUT_OF_SCOPE_REPLY,
            "fine_card":    None,
            "detail_table": None,
            "chips":        None,
            "explanation":  None,
            "scope":        "out_of_scope",
        }
    return None


def build_slot_clarification(
    slot: str,
    question: str,
    chips: List[dict],
    *,
    allow_text: bool = True,
    selection_mode: str = "single",
    allow_other: bool = False,
) -> dict:
    """Standard vertical MCQ envelope for bootstrap / slot-filling turns."""
    mode = "multi" if selection_mode == "multi" else "single"
    return {
        "intent":         "ask_slot",
        "slot":           slot,
        "question":       question,
        "reply":          question,
        "chips":          chips,
        "multi_select":   mode == "multi",
        "selection_mode": mode,
        "allow_text":     allow_text,
        "allow_other":    allow_other,
    }


def substantive_tokens(text: str) -> set:
    from graph_engine import _norm
    n = _norm(text or "")
    return {
        t for t in re.split(r"[^a-z0-9]+", n)
        if len(t) > 2 and t not in _GENERIC_TOKENS
    }


def candidate_relevant_to_text(
    text: str,
    code: str,
    name: str,
    *,
    min_overlap: int = 1,
) -> bool:
    """Return True if the violation shares meaningful tokens with *text*."""
    user_toks = substantive_tokens(text)
    if not user_toks:
        return False
    from graph_engine import get_graph_engine
    eng = get_graph_engine()
    node = eng.get_violation(code) or {}
    blob = " ".join([
        name or "",
        code or "",
        " ".join(node.get("keywords") or []),
        node.get("grp") or "",
    ]).lower()
    vio_toks = substantive_tokens(blob)
    overlap = user_toks & vio_toks
    return len(overlap) >= min_overlap


def filter_violation_candidates(
    text: str,
    candidates: List[Tuple[str, int, str]],
    *,
    min_score: int = VIO_CHIP_MIN_SCORE,
) -> List[Tuple[str, int, str]]:
    """Drop weak or semantically irrelevant violation suggestions."""
    out: List[Tuple[str, int, str]] = []
    for code, score, name in candidates:
        if score < min_score:
            continue
        if not candidate_relevant_to_text(text, code, name):
            continue
        out.append((code, score, name))
    return out


def _append_escape_chips(
    chips: List[dict],
    *,
    include_unsure: bool = True,
    include_none: bool = True,
    include_other: bool = True,
) -> List[dict]:
    """Append standard MCQ escape hatches without duplicating ids."""
    ids = {c["id"] for c in chips}
    out = list(chips)
    if include_unsure and CHIP_UNSURE["id"] not in ids:
        out.append(CHIP_UNSURE)
        ids.add(CHIP_UNSURE["id"])
    if include_none and CHIP_NONE["id"] not in ids:
        out.append(CHIP_NONE)
        ids.add(CHIP_NONE["id"])
    if include_other and CHIP_OTHER["id"] not in ids:
        out.append(CHIP_OTHER)
    return out


def build_zero_match_clarification(session: dict) -> dict:
    """Topic-router MCQ when no violation in the graph matches the user's story.

    Avoids hallucinated violation chips — guides the user to narrow the topic
    or pick 'I'm not sure' for a guided free-text follow-up."""
    loc = session.get("city_name") or session.get("state_code") or "your area"
    question = (
        f"I couldn't match that to a specific rule near {loc} yet — which "
        "area is closest? Tick any that might apply, then hit Proceed "
        "(or pick 'I'm not sure' and I'll ask a couple of guiding questions):"
    )
    chips = _append_escape_chips(list(TOPIC_CHIPS))
    return {
        "intent":         "ask_slot",
        "slot":           "topic_router",
        "question":       question,
        "reply":          question,
        "chips":          chips,
        "multi_select":   True,
        "selection_mode": "multi",
        "allow_text":     True,
        "allow_other":    True,
    }


def enrich_text_with_topic_hints(base: str, topic_ids: List[str]) -> str:
    """Combine the user's story with topic-router selections for re-resolution."""
    hints = " ".join(
        TOPIC_SEARCH_HINTS[t]
        for t in topic_ids
        if t in TOPIC_SEARCH_HINTS
    )
    parts = [p for p in (base or "", hints) if p.strip()]
    return " ".join(parts).strip()


def build_violation_clarification(
    question: str,
    candidates: List[Tuple[str, int, str]],
    *,
    multi: bool = True,
) -> dict:
    """Build a turn-shaped clarification with vertical checkbox MCQ + escape hatches."""
    chips = [{"id": code, "label": name} for code, _s, name in candidates[:4]]
    chips = _append_escape_chips(chips)
    mode = "multi" if multi else "single"
    return {
        "intent":         "ask_slot",
        "slot":           "violation_code",
        "question":       question,
        "reply":          question,
        "chips":          chips,
        "multi_select":   multi,
        "selection_mode": mode,
        "allow_text":     True,
        "allow_other":    True,
    }


def build_incident_clarification(session: dict) -> dict:
    """Checkbox MCQ after an accident / animal-collision story."""
    loc = session.get("city_name") or session.get("state_code") or "your area"
    question = (
        f"I'm really sorry that happened near {loc}. A few quick things so I "
        "can guide you properly — tick everything that applies (or pick "
        "'Something else' to explain in your own words):"
    )
    chips = [
        {"id": "inc_injury",   "label": "Someone was injured"},
        {"id": "inc_police",   "label": "I haven't reported it to the police yet"},
        {"id": "inc_animal",   "label": "It involved an animal (cow/dog/stray/wildlife)"},
        {"id": "inc_vehicle",  "label": "My vehicle was damaged"},
        {"id": "inc_insurance","label": "I need to know about insurance / claims"},
    ]
    chips = _append_escape_chips(chips)
    return {
        "intent":         "ask_slot",
        "slot":           "incident_context",
        "question":       question,
        "reply":          question,
        "chips":          chips,
        "multi_select":   True,
        "selection_mode": "multi",
        "allow_text":     True,
        "allow_other":    True,
    }


def build_driver_context_clarification() -> dict:
    """Checkbox MCQ after a fine is shown — licence / repeat / minor."""
    question = (
        "A couple of quick things so I can fine-tune this for you — tick "
        "anything that applies (or submit if none do):"
    )
    chips = [
        {"id": "ctx_no_licence", "label": "I don't have a valid driving licence"},
        {"id": "ctx_repeat",     "label": "This has happened before (repeat offence)"},
        {"id": "ctx_minor",      "label": "The rider/driver is under 18"},
    ]
    chips = _append_escape_chips(chips, include_none=False)
    return {
        "question":       question,
        "chips":          chips,
        "multi_select":   True,
        "selection_mode": "multi",
        "allow_text":     True,
        "allow_other":    True,
    }
