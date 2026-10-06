#!/usr/bin/env python3
"""
dynamic_chatbot.py — Conversational mode (narrate phase)
========================================================
Personality: warm Indian traffic constable / road-safety officer.
Empathetic, encouraging, never preachy. Grounded — every fact comes from
the compiled graph. Never invents fine amounts.

Public surface
--------------
    handle(session, user_text)              -> turn-shaped dict   (legacy)
    extract_and_reply(session, user_text)   -> turn-shaped dict   (v3.2+)

`extract_and_reply` is the new LLM-driven slot-extraction loop used by
Chatbot mode once the user has finished the location bootstrap. The LLM
owns extraction, asks ONE conversational follow-up at a time, and
emits a strict trailing protocol so the backend can parse slot picks
and optional inline-chip suggestions out of free text.

The returned dict for `extract_and_reply` has shape:

    {
      "intent":    "narrate",
      "reply":     "<bot text — protocol lines stripped>",
      "fine_card": {...} | None,
      "chips":     [{"id": "free:<opt>", "label": "<opt>"}, ...] | None,
    }
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

from graph_engine import (
    SEGMENT_FINE_CLASS,
    SEGMENT_LABEL,
    get_graph_engine,
)
from violation_resolver import get_violation_resolver
from clarification_engine import (
    CHIP_OTHER,
    CHIP_NONE,
    CHIP_UNSURE,
    build_driver_context_clarification,
    build_incident_clarification,
    build_slot_clarification,
    build_violation_clarification,
    build_zero_match_clarification,
    candidate_relevant_to_text,
    enrich_text_with_topic_hints,
    filter_violation_candidates,
    is_short_slot_answer,
    is_traffic_related,
    is_violation_denial,
    looks_like_incident,
    parse_age,
    scope_response,
    substantive_tokens,
)

log = logging.getLogger(__name__)

# ── Vehicle-violation exclusion map ──────────────────────────────────────────

_VEH_VIO_EXCLUSIVE: dict = {
    "two_wheeler_only": {
        "SAFETY_NO_HELMET_RIDER", "SAFETY_NO_HELMET_PILLION",
        "SAFETY_MORE_THAN_2_ON_2W", "SAFETY_NO_CHILD_2W",
    },
    "four_wheeler_only": {
        "SAFETY_NO_SEATBELT_DRIVER", "SAFETY_NO_SEATBELT_PASSENGER",
        "SAFETY_NO_AIRBAG", "SAFETY_NO_CHILD_RESTRAINT",
    },
    "heavy_vehicle_only": {
        "HWY_TRUCK_LEFT_LANE", "OVERLOAD_AXLE",
        "OVERLOAD_GOODS_WEIGHT", "OVERLOAD_GOODS_CARRYING_PASSENGERS",
    },
}

# Maps a vehicle_segment to its exclusive-violation category (if any).
_SEGMENT_TO_EXCL_CATEGORY: dict = {
    "two_wheeler":       "two_wheeler_only",
    "four_wheeler":      "four_wheeler_only",
    "four_wheeler_plus": "four_wheeler_only",
    "heavy_vehicle":     "heavy_vehicle_only",
}

# Plain-text keyword fallbacks for history scanning when the graph node is
# absent or has no keywords field.  Keys are violation codes.
_VIO_KEYWORDS_FALLBACK: dict = {
    "SAFETY_NO_HELMET_RIDER":            ["helmet", "no helmet", "without helmet"],
    "SAFETY_NO_HELMET_PILLION":          ["pillion helmet", "passenger helmet"],
    "SAFETY_MORE_THAN_2_ON_2W":          ["triple riding", "three on bike", "three on two"],
    "SAFETY_NO_CHILD_2W":                ["child on bike", "kid on bike"],
    "SAFETY_NO_SEATBELT_DRIVER":         ["seatbelt", "seat belt", "no seatbelt", "without seatbelt"],
    "SAFETY_NO_SEATBELT_PASSENGER":      ["passenger seatbelt", "passenger seat belt"],
    "SAFETY_NO_AIRBAG":                  ["airbag", "no airbag"],
    "SAFETY_NO_CHILD_RESTRAINT":         ["child restraint", "child seat"],
    "HWY_TRUCK_LEFT_LANE":               ["truck left lane", "left lane truck"],
    "OVERLOAD_AXLE":                     ["overload axle", "axle load", "lorry overload",
                                          "truck overload", "overloaded lorry", "overloaded truck"],
    "OVERLOAD_GOODS_WEIGHT":             ["overloaded goods", "goods overload"],
    "OVERLOAD_GOODS_CARRYING_PASSENGERS": ["goods carrying passengers"],
    # DOC_NO_DL: catch common phrasings, Indian-English spellings, and typos
    "DOC_NO_DL": [
        "without driving licence", "without driving license", "without licence",
        "without license", "no driving licence", "no driving license",
        "no licence", "no license", "without dl", "no dl",
        "dont have licence", "don't have licence", "dont have license",
        "dont have driving license", "don't have driving license",
        "dont have driving licence", "don't have driving licence",
        "driving without licence", "driving without license",
        "i have no licence", "i have no license",
        # common Indian-English spellings and typos
        "without driving lisence", "without driving liscense",
        "without driving listenece", "without liscence", "without licenece",
        "licenece", "liscence", "liscense", "lisence", "listenece",
    ],
}

# ── Intent patterns ──────────────────────────────────────────────────────────

# Each entry: (intent, list of compiled regex). First match wins.
_INTENT_PATTERNS: list = [
    ("post_incident", [
        re.compile(r"\b(accident|crash|collision|collid|hit (and|&) run|hit-and-run)\b", re.I),
        re.compile(r"\b(i (just )?hit|i (just )?knocked|i (just )?ran (over|into))\b", re.I),
        re.compile(r"\b(pedestrian|cyclist|bystander|another (car|vehicle|bike))\s+(was )?hit\b", re.I),
        re.compile(r"\b(injured|injury|bleeding|unconscious)\b", re.I),
        re.compile(r"\bwhat (do|should) i do (now|next|after)\b", re.I),
    ]),
    ("documents", [
        re.compile(r"\b(rc|registration certificate)\b", re.I),
        re.compile(r"\b(puc|pollution|emission certificate)\b", re.I),
        re.compile(r"\binsurance\b", re.I),
        re.compile(r"\b(renew|renewal of)\s+(dl|driving licen[cs]e|licence|license)\b", re.I),
        re.compile(r"\b(documents? (do|i) need|paperwork|do i need)\b", re.I),
        re.compile(r"\bfitness certificate\b", re.I),
    ]),
    ("license_guidance", [
        re.compile(r"\b(don'?t have|without|no)\s+(a )?(dl|driving\s+licen[cs]e?|licen[cs]e?|lice[ns]{1,2}e[nce]*)\b", re.I),
        re.compile(r"\b(how (do|to|can) i get|how to apply for)\s+(a )?(dl|driving licen[cs]e|licence|license|learner'?s? licen[cs]e|ll)\b", re.I),
        re.compile(r"\b(learner'?s? licen[cs]e|learners licence|ll)\b", re.I),
        re.compile(r"\bminimum age\b", re.I),
        re.compile(r"\bparivahan\b", re.I),
        re.compile(r"\bget (my )?(licen[cs]e|dl)\b", re.I),
        # Catch "driving X without driving licence" phrasings and Indian typos
        re.compile(r"\bwithout\s+(a\s+)?driving\s+\w+\b", re.I),
        re.compile(r"\b(liscen[sc]e|lisence|listenece|licenece|liscense)\b", re.I),
    ]),
    ("fine_query", [
        re.compile(r"\b(how much|what'?s|whats|what is)\s+(the )?fine\b", re.I),
        re.compile(r"\bfine for\b", re.I),
        re.compile(r"\b(penalty|challan)\b", re.I),
        re.compile(r"\b(how much (would|is) i (be )?fined|costs?)\b", re.I),
        re.compile(r"\b(no helmet|seat ?belt|over ?speed|drink (and )?drive|drunk driving|signal jump|red light)\b", re.I),
    ]),
]


def classify_intent(text: str) -> str:
    """Map free text → one of the five intents. Defaults to ``general_law``."""
    t = (text or "").strip()
    if not t:
        return "general_law"
    for intent, patterns in _INTENT_PATTERNS:
        for p in patterns:
            if p.search(t):
                return intent
    return "general_law"


# ── Hard-coded fallbacks (always available even if graph is incomplete) ─────

ACCIDENT_PROCEDURE: list = [
    "Stop your vehicle and switch on hazard lights — do not drive away.",
    "Check on anyone injured; if it's serious, call 112 (or 108 for ambulance) immediately.",
    "Exchange names, phone numbers, RC and insurance details with the other party. Take photos of vehicles, the scene, and any injuries.",
    "Inform your insurer and report at the nearest police station within 24 hours under MV Act §134 — even for minor accidents, a written report protects you later.",
]

LICENSE_STEPS: list = [
    "Apply for a Learner's Licence on the Parivahan Sewa portal (parivahan.gov.in) or at your local RTO. Minimum age: 16 for gear-less two-wheelers under 50cc, 18 for cars and other two-wheelers, 20 for transport vehicles.",
    "Take the online theory test — it's a short multiple-choice quiz on road signs and basic traffic rules.",
    "After the LL is issued, wait at least 30 days before applying for the permanent Driving Licence.",
    "Book a driving test slot at your RTO. Pass it, and the permanent DL is issued (usually mailed within 2–3 weeks).",
]

DOCUMENT_FACTS: dict = {
    "rc": (
        "RC (Registration Certificate, MV Act §39) — the vehicle's identity document. "
        "You must carry it (or a digital copy via mParivahan/DigiLocker) whenever you drive. "
        "Driving without RC: ₹5,000 fine + possible seizure."
    ),
    "dl": (
        "Driving Licence (MV Act §3, §9). Carry the original or a DigiLocker copy. "
        "Driving without a valid DL: ₹5,000 fine for first offence, up to ₹10,000 on repeat, "
        "plus potential vehicle seizure. Renewal is online via Parivahan; do it before expiry "
        "to avoid the late fee."
    ),
    "insurance": (
        "Third-party insurance is **mandatory** under MV Act §146. Comprehensive cover is optional "
        "but recommended. Driving without insurance: ₹2,000 fine + 3 months jail possible. "
        "Renewal is online with most insurers — keep a soft copy in DigiLocker."
    ),
    "puc": (
        "Pollution Under Control (PUC) certificate, CMVR Rule 115. Petrol cars: 1-year validity; "
        "diesel: 6 months; two-wheelers / older vehicles: 3 months. Driving without a valid PUC: "
        "₹10,000 fine. Test at any authorized PUC centre — takes 5 minutes, costs ₹50–₹100."
    ),
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _bot_reply(text: str, **extra) -> dict:
    return {"intent": "info", "reply": text, **extra}


def _ask_slot(slot: str, question: str, chips: List[dict],
              allow_text: bool = True, *, selection_mode: str = "single") -> dict:
    return build_slot_clarification(
        slot, question, chips,
        allow_text=allow_text,
        selection_mode=selection_mode,
    )


def _format_steps(steps: List[str]) -> str:
    return "\n".join(f"{i+1}. {step}" for i, step in enumerate(steps))


# ── Coherence helpers ─────────────────────────────────────────────────────────


def _get_vio_excl_category(violation_code: str) -> Optional[str]:
    """Return the exclusion category for a violation code, or None.

    Uses `vehicle_applicability` from the graph node as the authoritative
    source, falling back to `_VEH_VIO_EXCLUSIVE` when the field is absent
    or is the catch-all ``["ALL"]``.
    """
    if not violation_code:
        return None
    eng = get_graph_engine()
    node = eng.get_violation(violation_code)
    if node:
        va = node.get("vehicle_applicability") or ["ALL"]
        if va != ["ALL"]:
            has_2w  = "2W"  in va
            has_lmv = any(x in va for x in ("LMV", "HPV"))
            has_hgv = "HGV" in va
            if has_2w  and not has_lmv and not has_hgv:
                return "two_wheeler_only"
            if has_lmv and not has_2w  and not has_hgv:
                return "four_wheeler_only"
            if has_hgv and not has_2w  and not has_lmv:
                return "heavy_vehicle_only"
    # Fallback: hardcoded map
    for cat, vio_set in _VEH_VIO_EXCLUSIVE.items():
        if violation_code in vio_set:
            return cat
    return None


def _scan_text_for_vio_keywords(text: str, vio_set: set) -> Optional[str]:
    """Scan *text* for any keyword that maps to a violation in *vio_set*.

    Checks graph `keywords` first, then `_VIO_KEYWORDS_FALLBACK`.
    Returns the first matched violation_code or None.
    """
    if not text:
        return None
    text_l = text.lower()
    eng = get_graph_engine()
    for vcode in vio_set:
        node = eng.get_violation(vcode)
        if node:
            for kw in (node.get("keywords") or []):
                if kw.lower() in text_l:
                    return vcode
        for kw in _VIO_KEYWORDS_FALLBACK.get(vcode, []):
            if kw in text_l:
                return vcode
    return None


def _scan_history_for_vio_keywords(session: dict, vio_set: set) -> Optional[str]:
    """Scan the last 8 user messages in *session* for violation keywords.

    Returns the first matched violation_code in *vio_set* or None.
    Used by `_check_coherence` to detect ambiguous-vehicle situations.
    """
    messages = session.get("messages") or []
    user_msgs = [m for m in messages if m.get("role") == "user"][-8:]
    combined = " ".join(m.get("content", "") for m in user_msgs)
    return _scan_text_for_vio_keywords(combined, vio_set)


def _check_coherence(session: dict) -> dict:
    """Detect vehicle-violation contradictions in the current session.

    Two cases are checked:

    b) *vehicle_vs_violation* — ``vehicle_segment`` is set AND
       ``violation_code`` belongs to the wrong exclusive category
       (e.g. two_wheeler session + seatbelt violation).

    c) *ambiguous_vehicle* — ``vehicle_segment`` is not set AND the
       conversation history mentions BOTH a two-wheeler-only violation
       AND a four-wheeler-only violation.

    Returns {} when coherent, or:
        {"contradiction": True, "conflict": "<case>", "detail": "<hint>"}
    """
    vehicle_seg    = session.get("vehicle_segment")
    violation_code = session.get("violation_code")

    # Case b ─────────────────────────────────────────────────────────────────
    if vehicle_seg and violation_code:
        vio_cat = _get_vio_excl_category(violation_code)
        seg_cat = _SEGMENT_TO_EXCL_CATEGORY.get(vehicle_seg)
        if vio_cat and seg_cat and vio_cat != seg_cat:
            return {
                "contradiction": True,
                "conflict":      "vehicle_vs_violation",
                "detail":        "helmet vs seatbelt",
            }

    # Case c ─────────────────────────────────────────────────────────────────
    if not vehicle_seg:
        two_w  = _scan_history_for_vio_keywords(
            session, _VEH_VIO_EXCLUSIVE["two_wheeler_only"]
        )
        four_w = _scan_history_for_vio_keywords(
            session, _VEH_VIO_EXCLUSIVE["four_wheeler_only"]
        )
        if two_w and four_w:
            return {
                "contradiction": True,
                "conflict":      "ambiguous_vehicle",
                "detail":        "both helmet and seatbelt mentioned",
            }

    return {}


# ── Branch handlers ──────────────────────────────────────────────────────────


def _handle_post_incident(session: dict, text: str) -> dict:
    """Procedure for accidents / hit-and-run / injuries.

    We try to pull a matching violation's `what_to_do_next` from the graph
    first; if anything is missing we fall back to the hard-coded 4-step
    procedure (which the plan requires)."""
    eng = get_graph_engine()
    violator = get_violation_resolver()
    ranked = violator.resolve(text)
    graph_steps: List[str] = []
    matched_vio = None
    for vcode, _score in ranked[:6]:
        node = eng.get_violation(vcode) or {}
        if "ACC" in (node.get("code") or "") or "accident" in (node.get("grp") or ""):
            matched_vio = node
            wd = node.get("what_to_do_next")
            if wd:
                graph_steps.extend(_split_steps(wd))
            break

    # Merge graph hints into the canonical procedure (without duplicating).
    procedure = list(ACCIDENT_PROCEDURE)
    sec_refs = ["MV Act §134"]
    if matched_vio and matched_vio.get("mv_section"):
        sec_refs.append(f"MV Act §{matched_vio['mv_section']}")
    sec_refs.append("BNS §106 (rash act causing death)")

    intro = (
        "Take a breath — I'm here to help. Here's what you should do right now:"
    )
    body = _format_steps(procedure)
    citations = " / ".join(sorted(set(sec_refs)))
    if graph_steps:
        body += "\n\n**Specific to your situation:**\n" + _format_steps(graph_steps[:3])
    body += f"\n\n*Reference: {citations}.*"
    from clarification_engine import is_victim_report, victim_fled
    if is_victim_report(text) and victim_fled(text):
        body += ("\n\n**If the other driver fled (hit-and-run):** note the vehicle number, colour "
                 "and direction, look for CCTV/dashcam footage and witnesses, and file an FIR — "
                 "victims can claim compensation under the Hit and Run Motor Accidents Scheme "
                 "(₹2 lakh for death, ₹50,000 for grievous injury).")
    return _bot_reply(f"{intro}\n\n{body}")


def _split_steps(text: str) -> List[str]:
    """Heuristic: split `what_to_do_next` paragraphs into numbered steps."""
    if not text:
        return []
    parts = re.split(r"(?<=[.;])\s+|\s*[\n;]\s*", text.strip())
    out = [p.strip().rstrip(".") for p in parts if p and len(p.strip()) > 4]
    return out[:5]


def _handle_documents(session: dict, text: str) -> dict:
    t = text.lower()
    chunks: List[str] = []
    seen: set = set()
    keys_to_show: List[str] = []
    if re.search(r"\b(rc|registration)\b", t):           keys_to_show.append("rc")
    if re.search(r"\b(dl|licen[cs]e|driving licen[cs]e)\b", t): keys_to_show.append("dl")
    if "insurance" in t:                                 keys_to_show.append("insurance")
    if re.search(r"\b(puc|pollution|emission)\b", t):    keys_to_show.append("puc")
    if not keys_to_show:
        keys_to_show = ["dl", "rc", "insurance", "puc"]

    for k in keys_to_show:
        if k not in seen and k in DOCUMENT_FACTS:
            chunks.append(f"**{k.upper()}** — {DOCUMENT_FACTS[k]}")
            seen.add(k)

    body = (
        "Here's what you should know about the paperwork you mentioned:\n\n"
        + "\n\n".join(chunks)
        + "\n\nPro tip — keep digital copies in DigiLocker; they're legally equivalent to originals."
    )
    return _bot_reply(body)


def _handle_license_guidance(session: dict, text: str) -> dict:
    eng = get_graph_engine()
    state_code = session.get("state_code")
    age_line = ""
    if state_code:
        st = eng.get_state(state_code)
        if st:
            age_line = (
                f"In **{st.get('name', state_code)}**, the standard minimum ages are "
                "16 (50cc & under, no gear), 18 (other two-wheelers / LMV), "
                "20 (transport vehicles)."
            )
    intro = (
        "No worries — getting a driving licence in India is a straightforward "
        "process. It's not punitive; it's the legal qualification everyone goes "
        "through. Here's the path:"
    )
    body = _format_steps(LICENSE_STEPS)
    portal = "**Portal:** https://parivahan.gov.in/parivahansewa"
    tail = (
        "While you're driving with the Learner's Licence, you must display the "
        "'L' plate and have a fully-licensed driver next to you. Once you have "
        "the permanent DL, you're set."
    )
    parts = [intro, body]
    if age_line:
        parts.append(age_line)
    parts.extend([portal, tail])
    return _bot_reply("\n\n".join(parts))


def _handle_fine_query(
    session: dict,
    text: str,
    freeform_fn: Optional[Callable[[dict, str], dict]],
) -> Optional[dict]:
    """Try to deterministically resolve a violation. If we lack a hard slot
    (state / vehicle / violation), ask for it conversationally."""
    eng = get_graph_engine()
    violator = get_violation_resolver()

    # If the violation is missing, try to resolve it from the message text
    # (or fall back to the existing slot question, but warmer phrasing).
    if not session.get("violation_code"):
        ranked = violator.resolve(
            text,
            road_bucket        = session.get("road_bucket"),
            vehicle_fine_class = session.get("vehicle_fine_class"),
        )
        if ranked and violator.is_deterministic(ranked):
            vcode = ranked[0][0]
            session["violation_code"] = vcode
        elif ranked:
            chips = [
                {"id": vc, "label": (eng.get_violation(vc) or {}).get("name", vc)}
                for vc, _ in ranked[:4]
            ]
            session["pending_slot"] = "violation_code"
            return _ask_slot(
                slot       = "violation_code",
                question   = "Got it — sounds like you want to know the fine. "
                             "I'm narrowing it down. Tap the one that fits, or rephrase:",
                chips      = chips,
            )
        else:
            return None  # let LLM general_law handle it

    # State is a hard slot — ask conversationally.
    if not session.get("state_code"):
        from catalogs import get_catalogs
        chips = [{"id": st["code"], "label": st["name"]} for st in get_catalogs().states()]
        session["pending_slot"] = "state_code"
        return _ask_slot(
            slot     = "state_code",
            question = "To get the exact fine I'll need to know your state — "
                       "fines vary by region. Tap one below or just type it:",
            chips    = chips,
        )

    # Vehicle is a hard slot.
    if not session.get("vehicle_segment"):
        from catalogs import get_catalogs
        chips = [{"id": v["id"], "label": v["label"]}
                 for v in get_catalogs().vehicle_segments()]
        session["pending_slot"] = "vehicle_segment"
        location = session.get("city_name") or session.get("state_code") or "there"
        return _ask_slot(
            slot     = "vehicle_segment",
            question = (f"Got it — for {location}. To work out the right "
                        "fine I just need to know what you were riding or driving. "
                        "Tap a chip below or just say it:"),
            chips    = chips,
        )

    # All slots present → produce the fine card.
    card = eng.quick_fine(
        violation_code     = session["violation_code"],
        state_code         = session.get("state_code"),
        city_code          = session.get("city_code"),
        vehicle_fine_class = session.get("vehicle_fine_class"),
    )
    session["last_fine_card"] = card
    session["stage"] = "answered"
    session["pending_slot"] = None
    if not card:
        return _bot_reply(
            "I couldn't find a specific fine for that combination. "
            "Could you describe the violation a bit differently?"
        )
    first  = f"₹{card['fine_first']:,}"  if card.get("fine_first")  else "varies"
    name   = card.get("violation_name", "this violation")
    sec    = f" (MV Act §{card['mv_section']})" if card.get("mv_section") else ""
    src    = card.get("fine_source", "central")
    reply  = (
        f"For **{name}**{sec}, the first-offence fine here is **{first}**. "
        f"Source: {src} schedule. Drive safe — most of these are avoidable "
        "with small habits."
    )
    return {
        "intent":        "answer",
        "reply":         reply,
        "fine_card":     card,
    }


def _handle_general_law(
    session: dict,
    text: str,
    freeform_fn: Optional[Callable[[dict, str], dict]],
) -> dict:
    """Fallback — hand off to the existing Ollama route, but with a constable
    persona system prompt.

    The LLM may not be reachable; we degrade gracefully to a friendly nudge."""
    if not freeform_fn:
        return _bot_reply(
            "I'd love to help, but my conversational engine isn't reachable "
            "right now. You can still ask me about a specific fine using the "
            "chips above, or come back in a minute."
        )
    out = freeform_fn(session, text)
    reply = (out or {}).get("reply") or ""
    fine_card = (out or {}).get("fine_card")
    response: dict = {"intent": "answer", "reply": reply, "fine_card": fine_card}
    return response


# ── Public entry point ──────────────────────────────────────────────────────


def handle(
    session: dict,
    user_text: str,
    *,
    freeform_fn: Optional[Callable[[dict, str], dict]] = None,
) -> dict:
    """Single entry-point for `dialog_manager.dynamic_fn`."""
    text  = (user_text or "").strip()

    # Guardrail first — refuse evasion / bribery / forgery requests safely.
    guard = guardrail_response(text)
    if guard is not None:
        return guard

    intent = classify_intent(text)
    log.debug("dynamic_chatbot intent=%s text=%r", intent, text[:80])

    if intent == "post_incident":
        return _handle_post_incident(session, text)

    if intent == "documents":
        return _handle_documents(session, text)

    if intent == "license_guidance":
        return _handle_license_guidance(session, text)

    if intent == "fine_query":
        out = _handle_fine_query(session, text, freeform_fn)
        if out is not None:
            return out
        # No deterministic resolution → fall through to general_law.

    return _handle_general_law(session, text, freeform_fn)


# ── LLM persona helpers (consumed by llm_chatbot.handle_freeform) ───────────


CONSTABLE_PERSONA_PROMPT = (
    "You are DriveLegal — a friendly Indian traffic constable / road-safety "
    "officer. Speak warmly and respectfully (use Indian English). Be "
    "encouraging, never preachy. Keep replies to 2-4 sentences. Always cite "
    "the MV Act / BNS / CMVR §section when giving a legal point. Use ₹ for "
    "amounts. NEVER invent fine amounts — only quote values present in the "
    "RELEVANT VIOLATIONS list."
)

DYNAMIC_GEN_OPTIONS = {
    "temperature":    0.4,
    "num_predict":    350,
    "top_p":          0.9,
    "repeat_penalty": 1.1,
}


def is_dynamic_mode_request(env: Optional[dict]) -> bool:
    """Cheap predicate so llm_chatbot can pick the right options."""
    return bool(env and env.get("mode") == "dynamic")


# ─────────────────────────────────────────────────────────────────────────────
# v3.2 — narrate phase: LLM-driven slot extraction with strict trailing protocol
# ─────────────────────────────────────────────────────────────────────────────

# Allowed slot values — anything else from the model gets clamped to "?".
_ALLOWED_ROAD_BUCKETS = {"highway", "main_road", "street"}
_ALLOWED_SEGMENTS = {
    "two_wheeler",
    "four_wheeler",
    "three_wheeler",
    "four_wheeler_plus",
    "heavy_vehicle",
}

# `<<SLOTS ... NEEDS=...>>`  — outer envelope.
# Each slot is extracted individually so field ORDER doesn't matter and
# new fields remain backward-compatible with old-format canned replies.
_SLOTS_BLOCK_RE = re.compile(r"<<\s*SLOTS\s+(.+?)\s*>>", re.IGNORECASE | re.DOTALL)

# Per-field extractors — applied against the content inside <<SLOTS …>>.
_FIELD_RE: dict = {
    "road_bucket":     re.compile(r"road_bucket\s*=\s*([^\s>]+)",     re.I),
    "vehicle_segment": re.compile(r"vehicle_segment\s*=\s*([^\s>]+)", re.I),
    "violation_code":  re.compile(r"violation_code\s*=\s*([^\s>]+)",  re.I),
    "driver_age":      re.compile(r"driver_age\s*=\s*([^\s>]+)",      re.I),
    "has_licence":     re.compile(r"has_licence\s*=\s*([^\s>]+)",     re.I),
    "licence_type":    re.compile(r"licence_type\s*=\s*([^\s>]+)",    re.I),
    "repeat_offender": re.compile(r"repeat_offender\s*=\s*([^\s>]+)", re.I),
    "needs":           re.compile(r"NEEDS\s*=\s*([^\s>]+)",           re.I),
}

# `<<CHIPS option1 | option2 | option3>>`
_CHIPS_RE = re.compile(r"<<\s*CHIPS\s+(.+?)\s*>>", re.IGNORECASE | re.DOTALL)

# Whole protocol-emitting lines we want stripped from the user-facing reply
# (handles cases where the model adds extra markdown around the tag).
# Also matches lines that START a <<SLOTS block but have no closing >> on the
# same line (LLM line-wrapped the block), so we don't leak the raw tag.
_PROTOCOL_LINE_RE = re.compile(
    r"^[\s>*_`-]*<<\s*(SLOTS|CHIPS)\b.*?(?:>>[\s>*_`-]*)?$",
    re.IGNORECASE | re.MULTILINE,
)

PROTOCOL_INSTRUCTIONS = (
    "PROTOCOL — you MUST follow this exactly.\n"
    "At the END of EVERY reply, on its own line, emit exactly:\n"
    "<<SLOTS road_bucket={highway|main_road|street|?} "
    "vehicle_segment={two_wheeler|four_wheeler|three_wheeler|"
    "four_wheeler_plus|heavy_vehicle|?} "
    "violation_code={CODE or ?} "
    "driver_age={integer|?} "
    "has_licence={true|false|?} "
    "licence_type={MCWG|LMV|none|?} "
    "repeat_offender={true|false|?} "
    "NEEDS={slot_name|none}>>\n"
    "Use ? when unknown. Populate driver_age as an integer if the user "
    "mentioned their age; has_licence as true/false if they said whether "
    "they have a valid DL; licence_type as the DL class if mentioned "
    "(e.g. MCWG, LMV, TRANS); repeat_offender as true if they mentioned "
    "a previous offence or false if they explicitly said it's first time. "
    "Use NEEDS to mark which single slot you still need; "
    "ask ONE conversational question for that slot if NEEDS != none. "
    "If NEEDS=none, you have everything to answer — quote the exact fine, "
    "MV section, and tips from the data above.\n"
    "If the user is asking about something OTHER than a fine (license, "
    "post-incident, documents), set all unknown slots to ? and NEEDS=none, "
    "then answer using the dataset.\n"
    "Optionally, when truly ambiguous, emit one additional line:\n"
    "<<CHIPS option1 | option2 | option3>>\n"
    "(max 3 short options). The UI will render these as inline chips. "
    "Use sparingly."
)

NARRATE_PERSONA_PROMPT = (
    "You are DriveLegal — a warm, sharp, genuinely helpful Indian traffic "
    "constable and road-safety buddy. You have a calm, reassuring personality: "
    "you never lecture, you never scold, and you treat the user like a friend "
    "who needs clear guidance. Speak in natural, friendly Indian English. "
    "Replies are concise (3–5 sentences) but specific and human — acknowledge "
    "what the person said before answering. "
    "ALWAYS try to conclude with something useful: either the exact fine, the "
    "next step, or one focused clarifying question (never trail off without a "
    "clear next move). When you have enough to answer, quote the exact fine, "
    "the MV Act/CMVR section, and one practical tip. Always use ₹ for amounts. "
    "NEVER invent fine amounts — only quote values present in the RELEVANT "
    "VIOLATIONS list below. If a request is about evading a fine, bribery, or "
    "fake documents, refuse warmly and redirect to the lawful option."
)

NARRATE_GEN_OPTIONS = {
    "temperature":    0.4,
    "num_predict":    280,
    "top_p":          0.9,
    "repeat_penalty": 1.1,
}

# Deterministic violation resolution thresholds (see resolve_violation_from_text).
_VIO_MATCH_MIN_SCORE = 3
_VIO_MATCH_DOMINANCE = 2  # top score must be >= 2× runner-up

_CANNED_AMBIGUOUS_VEHICLE = (
    "I need to clarify something — you mentioned both a helmet issue "
    "(usually for bikes/scooters) and a seatbelt issue (usually for cars). "
    "Which vehicle were you on?"
)

# User-message keywords that signal a specific violation family (for sanity checks).
_USER_VIO_SIGNALS: List[Tuple[str, ...]] = [
    (("helmet", "no helmet", "without helmet"), ("helmet", "2w", "rider")),
    (("seatbelt", "seat belt", "no seatbelt", "without seatbelt"),
     ("seatbelt", "seat belt", "driver", "passenger")),
    (("overload", "overloaded", "goods projecting", "goods beyond"),
     ("goods", "overload", "weight", "axle", "dimension")),
]


# ─────────────────────────────────────────────────────────────────────────────
# Guardrail layer — fast, pre-LLM input safety filter
# ─────────────────────────────────────────────────────────────────────────────

# Requests we must refuse: evading enforcement, bribery, document forgery.
_GUARDRAIL_UNSAFE_RE = re.compile(
    r"\b("
    r"bribe|bribing|ghoos|ghus|rishwat|"
    r"fake\s+(licen[cs]e|rc|puc|number\s*plate|plate|insurance|challan)|"
    r"forged?\s+(licen[cs]e|rc|puc|document|plate)|"
    r"duplicate\s+(number\s*plate|plate)|"
    r"(avoid|escape|evade|dodge|get\s+out\s+of|wriggle\s+out\s+of|skip)\s+"
    r"(?:(?:paying|pay|the|this|that|my|a)\s+){0,3}"
    r"(fine|challan|chalan|penalty|cop|police|checkpost|check\s*post|rto)|"
    r"how\s+(to|do\s+i)\s+(not\s+)?(get\s+)?(caught|fined|challan)"
    r")\b",
    re.IGNORECASE,
)

_GUARDRAIL_UNSAFE_REPLY = (
    "I hear you — fines are frustrating. But I can't help with avoiding, "
    "evading, or 'managing' a challan, or with fake documents. That's a "
    "separate offence and can land you in much bigger trouble. What I *can* "
    "do: explain exactly what the rule is, what the correct fine should be, "
    "how to pay or contest it the legal way, and how to avoid it next time. "
    "Want me to walk you through any of those?"
)


def guardrail_response(text: str) -> Optional[dict]:
    """Return a safe canned narrate reply if *text* asks for something we must
    refuse (evasion / bribery / forgery). Returns None when the text is fine.

    Runs before any LLM call — it's a cheap regex gate, so it adds no latency
    to legitimate queries and guarantees a safe answer for unsafe ones."""
    if not text:
        return None
    from nlu import is_unsafe
    if is_unsafe(text):
        return {
            "intent":       "narrate",
            "reply":        __import__("nlu").UNSAFE_REPLY,
            "fine_card":    None,
            "detail_table": None,
            "chips":        None,
            "explanation":  None,
            "guardrail":    "unsafe_request",
        }
    return None


def _text_for_violation_resolution(session: dict, current: str) -> str:
    """Choose the text window used for keyword violation matching.

    Short slot answers ("19", "Am 19") must NOT be merged with earlier
    user stories — that was producing absurd chips (cow accident → mobile
    phone violation). Only fall back to recent history when the current
    turn itself carries substantive violation signal."""
    current = (current or "").strip()
    if is_short_slot_answer(current):
        return current
    if substantive_tokens(current):
        return current
    # Last resort: one prior user line for context like "also forgot helmet".
    msgs = [m.get("content", "") for m in (session.get("messages") or [])
            if m.get("role") == "user"]
    if msgs and msgs[-1].strip() != current:
        prev = msgs[-1].strip()
        if substantive_tokens(prev):
            return f"{prev} {current}".strip()
    return current


def _route_conversation(session: dict, text: str) -> Optional[dict]:
    """Intent-first routing before violation resolution / LLM.

    Returns a complete turn dict when this message should NOT enter the
    generic extract loop, else None."""
    intent = classify_intent(text)

    # A named offence asked about as a fine ("fine for not reporting an
    # accident?") or described as being caught ("driving without a licence",
    # "insurance expired and police stopped me") is a FINE question — not the
    # accident procedure or a document explainer.
    import nlu
    from clarification_engine import is_victim_report
    offence = None if is_victim_report(text) else resolve_violation_from_text(
        text, session.get("road_bucket"), session.get("vehicle_segment")).get("match")
    if offence and (nlu.is_fine_query(text)
                    or (intent in ("documents", "license_guidance")
                        and nlu.has_enforcement_context(text))):
        return None

    # ── Accident / animal collision ───────────────────────────────────────────
    if intent == "post_incident" or looks_like_incident(text):
        session["conversation_mode"] = "incident"
        session["violation_code"] = None
        session["last_fine_card"] = None
        if text:
            session.setdefault("messages", []).append(
                {"role": "user", "content": text}
            )
        out = _handle_post_incident(session, text)
        # Follow with an incident MCQ so the user can clarify in one shot.
        mcq = build_incident_clarification(session)
        combined_reply = (
            (out.get("reply") or "").rstrip()
            + "\n\n"
            + mcq["question"]
        )
        session.setdefault("messages", []).append(
            {"role": "assistant", "content": combined_reply}
        )
        session["pending_slot"] = "incident_context"
        return {
            **mcq,
            "reply":         combined_reply,
            "intent":        "ask_slot",
            "fine_card":     None,
            "detail_table":  None,
            "explanation":   None,
        }

    # ── Ongoing incident thread ─────────────────────────────────────────────
    # A clearly named offence leaves the incident thread.
    if session.get("conversation_mode") == "incident" and offence:
        session["conversation_mode"] = None
        session["pending_slot"] = None
        return None
    if session.get("conversation_mode") == "incident":
        if is_violation_denial(text):
            session["violation_code"] = None
            session["last_fine_card"] = None
            if text:
                session.setdefault("messages", []).append(
                    {"role": "user", "content": text}
                )
            reply = (
                "Got it — sounds like this is mainly about the accident itself, "
                "not a separate traffic-violation fine. Let me help with the "
                "right next steps. Tick anything below that applies, or pick "
                "'Something else' to explain."
            )
            mcq = build_incident_clarification(session)
            session.setdefault("messages", []).append(
                {"role": "assistant", "content": reply}
            )
            session["pending_slot"] = "incident_context"
            return {**mcq, "reply": reply, "intent": "ask_slot",
                    "fine_card": None}

        age = parse_age(text)
        if age is not None:
            session["driver_age"] = age
            if text:
                session.setdefault("messages", []).append(
                    {"role": "user", "content": text}
                )
            reply = (
                f"Thanks — noted you're {age}. For accident situations the "
                "priority is safety and reporting. Tick anything below that "
                "applies so I can guide you properly."
            )
            mcq = build_incident_clarification(session)
            session.setdefault("messages", []).append(
                {"role": "assistant", "content": reply}
            )
            session["pending_slot"] = "incident_context"
            return {**mcq, "reply": reply, "intent": "ask_slot",
                    "fine_card": None}

    # ── Slot-only age reply (not in incident mode) ──────────────────────────
    # Only a short age statement ("I'm 17", "he is 16") — an age inside a
    # story ("my 16 year old son was driving") must still reach violation
    # matching (→ underage driving).
    age = parse_age(text)
    if age is not None and (offence or len(text.split()) > 6):
        if session.get("driver_age") is None:
            session["driver_age"] = age
        age = None
    if age is not None and session.get("driver_age") is None:
        session["driver_age"] = age
        if text:
            session.setdefault("messages", []).append(
                {"role": "user", "content": text}
            )
        card = session.get("last_fine_card")
        if age < 18:
            reply = (f"Noted — the rider/driver is {age}, a minor. Under **MV Act §199A** the "
                     "guardian or vehicle owner is held responsible: a ₹25,000 fine and up to "
                     "3 years' imprisonment, the vehicle's registration can be cancelled for a "
                     "year, and the minor can't get a licence until 25."
                     + (f" That's on top of the **{card.get('violation_name')}** fine." if card else ""))
        else:
            reply = (f"Got it — {age}. " + (
                "That doesn't change this fine — adult rules apply." if card else
                "Tell me a bit more about what happened and I'll find the exact rule and fine."))
        session.setdefault("messages", []).append(
            {"role": "assistant", "content": reply}
        )
        return {
            "intent": "narrate", "reply": reply,
            "fine_card": None, "chips": None,
            "allow_text": True,
        }

    # ── Other intent shortcuts ───────────────────────────────────────────────
    if intent == "documents":
        if text:
            session.setdefault("messages", []).append(
                {"role": "user", "content": text}
            )
        return _handle_documents(session, text)
    if intent == "license_guidance":
        if text:
            session.setdefault("messages", []).append(
                {"role": "user", "content": text}
            )
        return _handle_license_guidance(session, text)

    return None


def resolve_violation_from_text(
    text: str,
    road_bucket: Optional[str],
    vehicle_segment: Optional[str],
    engine=None,
) -> dict:
    """Score violations from user text via keyword index + road/vehicle filters.

    Returns:
        {"match": {code, score, name} | None,
         "candidates": [(code, score, name), ...]}  # top 3
    """
    eng = engine or get_graph_engine()
    violator = get_violation_resolver()
    vehicle_fine_class = (
        SEGMENT_FINE_CLASS.get(vehicle_segment) if vehicle_segment else None
    )
    ranked = violator.resolve(text, road_bucket, vehicle_fine_class)
    if not ranked:
        return {"match": None, "candidates": []}

    candidates: List[Tuple[str, int, str]] = []
    for code, score in ranked[:3]:
        node = eng.get_violation(code) or {}
        candidates.append((code, score, node.get("name", code)))

    top_code, top_score, top_name = candidates[0]
    is_det = violator.is_deterministic(ranked)
    match = (
        {"code": top_code, "score": top_score, "name": top_name}
        if is_det and top_score >= _VIO_MATCH_MIN_SCORE
        else None
    )
    return {"match": match, "candidates": candidates}


def _recent_user_text(session: dict, current: str, n: int = 2) -> str:
    """Concatenate the last *n* user messages plus the current turn."""
    msgs = [m.get("content", "") for m in (session.get("messages") or [])
            if m.get("role") == "user"][-n:]
    parts = [p for p in msgs if p]
    if current:
        parts.append(current)
    return " ".join(parts)


def _allowed_violation_codes(
    road_bucket: Optional[str],
    vehicle_fine_class: Optional[str],
    state_code: Optional[str],
    engine=None,
    limit: int = 12,
) -> set:
    """Violation codes the LLM may emit for this context."""
    eng = engine or get_graph_engine()
    bucket = road_bucket or "main_road"
    veh = [vehicle_fine_class] if vehicle_fine_class else None
    nodes = eng.get_violation_context(bucket, veh, state_code, limit=limit)
    return {n["code"] for n in nodes if n.get("code")}


def _clear_stale_violation_if_needed(session: dict, text: str) -> None:
    """Drop violation_code / fine cache when new keywords contradict the slot."""
    cur = session.get("violation_code")
    if not cur or not text:
        return
    eng = get_graph_engine()
    resolved = resolve_violation_from_text(
        text,
        session.get("road_bucket"),
        session.get("vehicle_segment"),
        eng,
    )
    match = resolved.get("match")
    if match and match["code"] != cur:
        session["violation_code"] = None
        session["last_fine_card"] = None
        if session.get("stage") == "answered":
            session["stage"] = "collecting"
        return
    # Keyword scan: user mentions helmet/seatbelt but slot is unrelated.
    text_l = text.lower()
    cur_node = eng.get_violation(cur) or {}
    cur_name = (cur_node.get("name") or cur).lower()
    for user_kws, vio_hints in _USER_VIO_SIGNALS:
        if any(k in text_l for k in user_kws):
            if not any(h in cur_name or h in cur.lower() for h in vio_hints):
                session["violation_code"] = None
                session["last_fine_card"] = None
                if session.get("stage") == "answered":
                    session["stage"] = "collecting"
                return


def _sanity_check_fine(fine_card: Optional[dict], violation_code: str,
                       user_text: str) -> bool:
    """Return True if fine_card is consistent with user_text keywords."""
    if not fine_card or not user_text:
        return True
    text_l = user_text.lower()
    vname = (fine_card.get("violation_name") or "").lower()
    vcode = (violation_code or "").lower()

    if any(k in text_l for k in ("helmet", "no helmet", "without helmet")):
        if any(x in vname or x in vcode for x in ("goods", "overload", "projecting")):
            return False
    if any(k in text_l for k in ("seatbelt", "seat belt", "no seatbelt")):
        if any(x in vname or x in vcode for x in ("goods", "overload", "helmet")):
            return False
    return True


def _build_explanation(
    session: dict,
    violation_code: str,
    fine_card: Optional[dict],
    *,
    match_method: str,
    match_confidence: str,
    deterministic_name: Optional[str] = None,
) -> dict:
    """Template-based explainability payload (no LLM on high-confidence paths)."""
    eng = get_graph_engine()
    vio = eng.get_violation(violation_code) or {}
    name = deterministic_name or vio.get("name", violation_code)
    state_code = session.get("state_code")
    st = eng.get_state(state_code) if state_code else None
    st_name = (st or {}).get("name", state_code or "your state")
    sec = vio.get("mv_section")
    sec_txt = f"§{sec}" if sec else "the applicable section"
    grp = (vio.get("grp") or "").replace("_", " ")

    hints: List[str] = []
    combined = _recent_user_text(session, "")
    cl = combined.lower()
    if "helmet" in cl:
        hints.append("not wearing a helmet")
    if "seatbelt" in cl or "seat belt" in cl:
        hints.append("not wearing a seatbelt")
    if "head injury" in cl or "injury" in cl:
        hints.append("an injury")
    user_hint = hints[0] if hints else "what you described"

    reasoning = (
        f"You mentioned {user_hint}. Under MV Act {sec_txt}, this applies to "
        f"{name.lower()} in {st_name}."
    )
    if grp:
        reasoning += f" ({grp})"

    fine_source = (fine_card or {}).get("fine_source", "central")
    data_sources = [f"graph:violation:{violation_code}"]
    if state_code:
        data_sources.append(f"graph:fine:{state_code}")
        if session.get("city_code"):
            data_sources.append(
                f"graph:fine:{state_code}:{session['city_code']}"
            )

    return {
        "violation_matched": violation_code,
        "match_method":      match_method,
        "match_confidence":  match_confidence,
        "fine_source":       fine_source,
        "reasoning":         reasoning,
        "data_sources":      data_sources,
    }


def _template_narrate_reply(session: dict, fine_card: dict) -> str:
    """Short constable reply when all slots are filled without LLM."""
    first = f"₹{fine_card['fine_first']:,}" if fine_card.get("fine_first") else "varies"
    name  = fine_card.get("violation_name", "this violation")
    sec   = f" (MV Act §{fine_card['mv_section']})" if fine_card.get("mv_section") else ""
    loc   = session.get("city_name") or session.get("state_code") or "your area"
    return (
        f"Based on what you told me, **{name}**{sec} in {loc} carries a "
        f"first-offence fine of **{first}**. Drive safe — small habits prevent "
        "most of these."
    )


def _validate_llm_violation_code(
    llm_code: str,
    session: dict,
    deterministic: Optional[dict],
    allowed: set,
) -> Tuple[Optional[str], Optional[str]]:
    """Validate / override LLM violation_code. Returns (code, override_note)."""
    eng = get_graph_engine()
    if not llm_code or llm_code == "?":
        if deterministic:
            return deterministic["code"], None
        return None, None

    det = deterministic
    if det and llm_code != det["code"]:
        if eng.get_violation(det["code"]):
            note = (
                f"I've matched this to {det['name']} based on what you described."
            )
            return det["code"], note
        return None, None

    if llm_code in allowed and eng.get_violation(llm_code):
        return llm_code, None

    if det and eng.get_violation(det["code"]):
        note = (
            f"I've matched this to {det['name']} based on what you described."
        )
        return det["code"], note

    return None, None


def _build_narrate_system_prompt(
    session: dict,
    *,
    coherence_override: str = "",
    known_violation: Optional[str] = None,
) -> str:
    """Compose the narrate-phase system prompt: persona + context line +
    LRU-cached subgraph + coherence rules + strict trailing protocol.

    *coherence_override* is an optional extra instruction injected at the
    end of the COHERENCE RULES block when a specific ambiguity has been
    detected pre-LLM (e.g. both helmet and seatbelt mentioned).
    """
    eng = get_graph_engine()

    ctx_parts: List[str] = []
    if session.get("city_name"):
        ctx_parts.append(
            f"City: {session['city_name']} ({session.get('city_code') or '—'})"
        )
    if session.get("state_code"):
        st = eng.get_state(session["state_code"])
        st_name = st["name"] if st else session["state_code"]
        ctx_parts.append(f"State: {st_name} ({session['state_code']})")
    if session.get("road_bucket"):
        ctx_parts.append(f"Road bucket: {session['road_bucket']}")
    if session.get("vehicle_type"):
        ctx_parts.append(f"Vehicle: {session['vehicle_type']}")
    ctx = "\n".join(ctx_parts) if ctx_parts else "No additional context yet."

    # The subgraph is keyed on (bucket-or-all, state, city, vehicle_class).
    # When bucket isn't known yet we still call with "main_road" so the LLM
    # has *some* violation set to ground answers — it's the cheapest middle.
    bucket_for_subgraph = session.get("road_bucket") or "main_road"
    veh_codes = (
        [session["vehicle_fine_class"]]
        if session.get("vehicle_fine_class") else None
    )
    subgraph = eng.subgraph_for_llm(
        bucket_for_subgraph,
        session.get("state_code"),
        session.get("city_code"),
        veh_codes,
        limit=12,
    )

    extra_instructions: list = []
    if known_violation:
        vio = eng.get_violation(known_violation) or {}
        vname = vio.get("name", known_violation)
        extra_instructions.append(
            f"The violation is CONFIRMED as {known_violation} ({vname}) — "
            "do NOT change violation_code in your SLOTS block; keep "
            f"violation_code={known_violation}."
        )
    else:
        extra_instructions.append(
            "Do NOT set violation_code unless it appears in the RELEVANT "
            "VIOLATIONS list above. Use ? if unsure."
        )

    # Inject driver-age inquiry for next turn if flagged.
    if session.get("_ask_age_next_turn") and session.get("driver_age") is None:
        extra_instructions.append(
            "Also ask: Is the rider/driver an adult (18+)? "
            "Age affects whether guardian liability rules apply."
        )
        session["_ask_age_next_turn"] = False   # consume flag

    # Inject licence guidance note if user said they have no DL.
    if session.get("_inject_licence_guidance"):
        extra_instructions.append(
            "The user does not have a valid driving licence. "
            "Briefly mention the steps to get one (Parivahan portal, Learner's Licence first) "
            "in addition to answering their question."
        )
        session["_inject_licence_guidance"] = False  # consume flag

    extra_block = (
        "\n\nADDITIONAL INSTRUCTIONS:\n" + "\n".join(f"- {i}" for i in extra_instructions)
        if extra_instructions else ""
    )

    coherence_rules = (
        "COHERENCE RULES (always enforce):\n"
        "- Helmets and seatbelts are MUTUALLY EXCLUSIVE violations. "
        "Helmets → two-wheelers only. Seatbelts → four-wheelers only. "
        "If the user mentions BOTH, you MUST stop and ask: 'Were you on a "
        "two-wheeler (bike/scooter) or a four-wheeler (car/SUV)? I can only answer for one.'\n"
        "- Never accept a violation that is impossible for the confirmed vehicle type. "
        "If someone on a bike mentions seatbelt, ask them to confirm — "
        "they may have been confused.\n"
        "- If the user's statements are contradictory (e.g., different vehicle types implied), "
        "ALWAYS clarify before giving a fine answer.\n"
        "- Confirm vehicle type conversationally before diving into fines if any ambiguity exists."
    )
    if coherence_override:
        coherence_rules += f"\n\n{coherence_override}"

    # F1: when structured-output mode is on, ask for a strict JSON object
    # instead of the <<SLOTS>> text protocol. The parser accepts both.
    output_instructions = PROTOCOL_INSTRUCTIONS
    if _json_mode_enabled():
        output_instructions = (
            "OUTPUT FORMAT — respond with ONLY a single JSON object, no prose "
            "outside it, no markdown fences:\n"
            '{"reply": "<your 2-4 sentence answer>", '
            '"violation_code": "<CODE from the list, or null>", '
            '"road_bucket": "<highway|main_road|street|null>", '
            '"vehicle_segment": "<two_wheeler|three_wheeler|four_wheeler|four_wheeler_plus|special|null>", '
            '"needs": "<state_code|vehicle_segment|violation_code|none>", '
            '"chips": ["opt1","opt2"]}\n'
            "Use null when unknown. Only use violation_code values from the "
            "RELEVANT VIOLATIONS list — never invent one."
        )

    return (
        f"{NARRATE_PERSONA_PROMPT}\n\n"
        f"SESSION CONTEXT:\n{ctx}\n\n"
        f"{subgraph}\n\n"
        f"{coherence_rules}\n\n"
        f"{output_instructions}"
        f"{extra_block}"
    )


def _call_narrate_llm(
    system_prompt: str,
    history: List[dict],
    user_text: str,
    *,
    _session_ref: Optional[dict] = None,
    _deterministic_match: Optional[dict] = None,
) -> str:
    """Hybrid narrate-phase handler.

    1. Try Groq API (fast, ~500ms).
    2. On ANY network / connectivity failure → fall back to offline_engine
       instantly.  Context is preserved because _session_ref is passed.

    Returns the raw assistant string with trailing <<SLOTS ...>> protocol.
    Tests can monkey-patch this function to skip the network call.
    """
    from llm_chatbot import GroqOfflineError, call_groq_narrate

    # ── Online path: Groq ────────────────────────────────────────────────────
    try:
        raw = call_groq_narrate(
            system_prompt,
            history,
            user_text,
            temperature = NARRATE_GEN_OPTIONS.get("temperature", 0.4),
            max_tokens  = NARRATE_GEN_OPTIONS.get("num_predict", 280),
            reasoning   = True,     # only unmatched / open questions reach here
        )
        log.debug("narrate: Groq OK (%d chars)", len(raw))
        return raw

    except GroqOfflineError as exc:
        log.info("narrate: Groq offline (%s) — using offline_engine", exc)

    except Exception as exc:
        log.warning("narrate: unexpected Groq error (%s) — using offline_engine", exc)

    # ── Offline fallback: rule-based engine ──────────────────────────────────
    try:
        from offline_engine import narrate_protocol
        session = _session_ref or {}
        return narrate_protocol(session, user_text, _deterministic_match)
    except Exception as exc:
        log.exception("narrate: offline_engine also failed")
        return (
            "I'm having trouble right now — both online and offline engines hit "
            "an unexpected error. Please describe your question again and I'll try "
            "to help with the information I have locally.\n"
            "<<SLOTS road_bucket=? vehicle_segment=? violation_code=? "
            "driver_age=? has_licence=? licence_type=? repeat_offender=? NEEDS=none>>"
        )


def _clean_slot_value(raw: str) -> str:
    """Strip braces/quotes/punctuation the LLM may wrap around a slot value."""
    if not raw:
        return "?"
    s = raw.strip().strip("{}[]()<>\"'`,;.")
    return s or "?"


def _json_mode_enabled() -> bool:
    """Structured-output mode. Default OFF (keeps the proven <<SLOTS>> path).
    Flip DRIVELEGAL_LLM_JSON=1 once your provider's JSON response_format is
    verified to make the LLM emit a strict object instead of a text protocol."""
    return os.getenv("DRIVELEGAL_LLM_JSON", "").strip().lower() in ("1", "true", "yes")


# Canonical field set shared by both the JSON and the <<SLOTS>> parsers.
_SLOT_DEFAULTS = {
    "road_bucket": "?", "vehicle_segment": "?", "violation_code": "?",
    "driver_age": "?", "has_licence": "?", "licence_type": "?",
    "repeat_offender": "?", "needs": "none",
}


def _try_parse_json_reply(raw_reply: str) -> Optional[dict]:
    """F1 fix — structured output path.

    If the model returned a JSON object (optionally fenced in ```json), parse it
    into the SAME {slots, chips, clean_reply} shape the <<SLOTS>> regex produces,
    so nothing downstream has to change. Returns None (→ fall back to the regex
    parser) on anything that isn't a clean object with a `reply` field. This
    makes malformed / truncated protocol lines a non-issue when JSON mode is on.
    """
    if not raw_reply or "{" not in raw_reply:
        return None
    text = raw_reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or "reply" not in obj:
        return None

    slots = dict(_SLOT_DEFAULTS)
    src = obj.get("slots") if isinstance(obj.get("slots"), dict) else obj
    for field in _SLOT_DEFAULTS:
        if field in src and src[field] not in (None, ""):
            val = str(src[field]).strip()
            slots[field] = val if field == "violation_code" else val.lower()

    chips: Optional[List[str]] = None
    raw_chips = obj.get("chips")
    if isinstance(raw_chips, list):
        opts = [str(o).strip() for o in raw_chips if str(o).strip()]
        if opts:
            chips = opts[:3]

    return {
        "slots": slots,
        "chips": chips,
        "clean_reply": str(obj.get("reply") or "").strip(),
    }


def _parse_protocol(raw_reply: str) -> dict:
    """Extract the trailing `<<SLOTS …>>` and optional `<<CHIPS …>>` lines.

    Returns: {"slots": {road_bucket, vehicle_segment, violation_code,
                        driver_age, has_licence, licence_type,
                        repeat_offender, needs},
              "chips": [str, ...] | None,
              "clean_reply": <reply with protocol lines stripped>}

    Field order inside <<SLOTS …>> does NOT matter — each field is extracted
    independently so old 4-slot canned replies remain backward-compatible.
    """
    # Structured-output first: if the model returned a JSON object, use it.
    # Falls through to the robust <<SLOTS>> regex parser on anything else, so
    # this is safe whether or not JSON mode is enabled.
    js = _try_parse_json_reply(raw_reply)
    if js is not None:
        return js

    slots: dict = {
        "road_bucket":     "?",
        "vehicle_segment": "?",
        "violation_code":  "?",
        "driver_age":      "?",
        "has_licence":     "?",
        "licence_type":    "?",
        "repeat_offender": "?",
        "needs":           "none",
    }

    bm = _SLOTS_BLOCK_RE.search(raw_reply or "")
    if bm:
        block = bm.group(1)
        for field, pat in _FIELD_RE.items():
            fm = pat.search(block)
            if fm:
                raw_val = _clean_slot_value(fm.group(1))
                # Lower-case everything except violation_code (which is ALL_CAPS)
                if field == "violation_code":
                    slots[field] = raw_val
                else:
                    slots[field] = raw_val.lower()

    cm = _CHIPS_RE.search(raw_reply or "")
    chips: Optional[List[str]] = None
    if cm:
        opts = [o.strip(" -•") for o in cm.group(1).split("|")]
        opts = [o for o in opts if o]
        if opts:
            chips = opts[:3]

    # Strip whole-line protocol artefacts, then collapse any trailing
    # whitespace so the bubble doesn't get a dangling newline.
    # The protocol block terminates the message; models sometimes keep
    # talking after it (a second, duplicate answer). Keep only what precedes it.
    raw_reply = raw_reply or ""
    first_block = re.search(r"<<\s*(SLOTS|CHIPS)\b", raw_reply, re.I)
    if first_block and raw_reply[:first_block.start()].strip():
        tail = raw_reply[first_block.start():]
        # keep protocol lines themselves (parsed above / below), drop stray prose
        tail_blocks = "\n".join(re.findall(r"<<.*?>>", tail, re.S))
        raw_reply = raw_reply[:first_block.start()] + "\n" + tail_blocks
    cleaned = _PROTOCOL_LINE_RE.sub("", raw_reply)
    # Also handle inline (non-line-anchored) protocol tags as a fallback.
    cleaned = _SLOTS_BLOCK_RE.sub("", cleaned)
    cleaned = _CHIPS_RE.sub("", cleaned)
    # Catch-all: strip any <<SLOTS or <<CHIPS block the LLM may have emitted
    # across multiple lines or without a closing >>  (e.g. truncated output).
    # This handles cases like "...driver_age={?} has\n..." where >> was missing.
    cleaned = re.sub(
        r"<<\s*(?:SLOTS|CHIPS)\b.*?(?:>>|$)",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return {"slots": slots, "chips": chips, "clean_reply": cleaned}


def _apply_extracted_slots(session: dict, slots: dict, evidence: Optional[str] = None) -> None:
    """Update session slots from the parsed protocol. Never overwrites a
    value that was already set explicitly (e.g. via location bootstrap).

    *evidence*: the user's recent text. When given (LLM output), a vehicle is
    only accepted if the user actually mentioned it or the offence implies it
    — the model used to assume "car" for "I jumped a red light"."""
    eng = get_graph_engine()
    if evidence is not None:
        import nlu
        vs_llm = slots.get("vehicle_segment")
        if vs_llm not in (None, "?") and vs_llm != eng.detect_vehicle_segment(evidence) \
                and vs_llm != nlu.vehicle_from_violation(slots.get("violation_code")):
            slots = {**slots, "vehicle_segment": "?"}

    rb = slots.get("road_bucket")
    if rb in _ALLOWED_ROAD_BUCKETS and not session.get("road_bucket"):
        session["road_bucket"] = rb

    vs = slots.get("vehicle_segment")
    if vs in _ALLOWED_SEGMENTS and not session.get("vehicle_segment"):
        session["vehicle_segment"]    = vs
        session["vehicle_fine_class"] = SEGMENT_FINE_CLASS.get(vs)
        session["vehicle_type"]       = SEGMENT_LABEL.get(vs, vs)

    vc = slots.get("violation_code")
    if vc and vc != "?" and not session.get("violation_code"):
        # Only accept codes the engine actually knows about.
        if eng.get_violation(vc):
            session["violation_code"] = vc

    # ── New driver-context slots ──────────────────────────────────────────────
    da = slots.get("driver_age")
    if da and da != "?" and session.get("driver_age") is None:
        try:
            session["driver_age"] = int(da)
        except (ValueError, TypeError):
            pass

    hl = slots.get("has_licence")
    if hl and hl != "?" and session.get("has_licence") is None:
        if hl in ("true", "yes", "1"):
            session["has_licence"] = True
        elif hl in ("false", "no", "0"):
            session["has_licence"] = False

    lt = slots.get("licence_type")
    if lt and lt not in ("?", "none") and session.get("licence_type") is None:
        session["licence_type"] = lt

    ro = slots.get("repeat_offender")
    if ro and ro != "?" and session.get("repeat_offender") is None:
        if ro in ("true", "yes", "1"):
            session["repeat_offender"] = True
        elif ro in ("false", "no", "0"):
            session["repeat_offender"] = False


def _build_detail_table(fine_card: dict) -> List[dict]:
    """Assemble the `detail_table` list for a narrate fine_card response.

    Each row is {"label": str, "value": str}.  Rows with no value are omitted.
    """
    rows: List[dict] = []

    def _row(label: str, value) -> None:
        if value is not None and str(value).strip():
            rows.append({"label": label, "value": str(value).strip()})

    _row("Violation",        fine_card.get("violation_name"))
    if fine_card.get("mv_section"):
        _row("MV Act Section", f"§{fine_card['mv_section']}")
    if fine_card.get("fine_first"):
        _row("Fine (1st offence)", f"₹{int(fine_card['fine_first']):,}")
    if fine_card.get("fine_repeat"):
        _row("Fine (repeat)",      f"₹{int(fine_card['fine_repeat']):,}")
    _row("Compoundable", "Yes" if fine_card.get("compoundable") else "No — court required")
    _row("Imprisonment",     fine_card.get("imprisonment") or "None")
    _row("DL consequence",   fine_card.get("licence_consequence"))
    _row("What to do",       fine_card.get("what_to_do_next"))
    _row("Tip",              fine_card.get("tips_to_avoid"))
    return rows


_SPEED_LIMIT_Q_RE = re.compile(
    r"\b(speed\s+limits?|max(imum)?\s+speed|how\s+fast\s+can\s+i)\b", re.I)

_STORY_START_RE = re.compile(
    r"\b(yesterday|last\s+night|tonight|today|this\s+(morning|evening)|so\s+i|"
    r"i\s+was\s+(coming|going|heading|returning|on\s+my\s+way)|on\s+my\s+way)\b", re.I)


def _reply_turn(session: dict, text: str, reply: str, **extra) -> dict:
    """Record a deterministic turn in history and return a narrate envelope."""
    if text:
        session.setdefault("messages", []).append({"role": "user", "content": text})
    session.setdefault("messages", []).append({"role": "assistant", "content": reply})
    out = {"intent": "narrate", "reply": reply, "fine_card": None,
           "detail_table": None, "chips": None, "explanation": None}
    out.update(extra)
    return out


def _set_vehicle(session: dict, seg: Optional[str]) -> None:
    if seg in SEGMENT_FINE_CLASS:
        session["vehicle_segment"]    = seg
        session["vehicle_fine_class"] = SEGMENT_FINE_CLASS.get(seg)
        session["vehicle_type"]       = SEGMENT_LABEL.get(seg, seg)


def grounded_answer(session: dict, vcode: str, *, lead: str = "") -> Tuple[str, Optional[dict]]:
    """Graph-grounded fine reply + card for *vcode* in the session's context.
    Every number, section and consequence comes from the knowledge graph."""
    from offline_engine import _build_fine_response
    eng = get_graph_engine()
    card = eng.quick_fine(
        violation_code     = vcode,
        state_code         = session.get("state_code"),
        city_code          = session.get("city_code"),
        vehicle_fine_class = session.get("vehicle_fine_class"),
    )
    body = _build_fine_response(session, vcode)
    reply = f"{lead.strip()}\n\n{body}" if lead and lead.strip() else body
    return reply, card


def _answer_violation(session: dict, text: str, vcode: str, *, lead: str = "",
                      intent: str = "narrate", **extra) -> dict:
    import nlu
    vcode = nlu.adjust_for_vehicle(vcode, session.get("vehicle_segment"),
                                   session.get("last_user_story") or text or "")
    reply, card = grounded_answer(session, vcode, lead=lead)
    session["violation_code"] = vcode
    if card:
        session["last_fine_card"] = card
        session["stage"] = "answered"
    explanation = _build_explanation(
        session, vcode, card, match_method=extra.pop("match_method", "keyword"),
        match_confidence="high",
    ) if card else None
    chips = None
    multi = {}
    # One-time driver-context checkboxes (repeat offence / no licence / minor
    # change the outcome) — offered on the first answer of a conversation only.
    if (card and intent == "narrate" and not session.get("_driver_context_done")
            and not session.get("_driver_context_offered")):
        session["_driver_context_offered"] = True
        fu = build_driver_context_clarification()
        reply = reply.rstrip() + "\n\n" + fu["question"]
        chips = fu["chips"]
        multi = {"multi_select": True, "selection_mode": "multi", "allow_other": True,
                 "allow_text": True}
        session["pending_multi"] = "driver_context"
    out = _reply_turn(session, text, reply, intent=intent, fine_card=card,
                      detail_table=_build_detail_table(card) if card else None,
                      explanation=explanation, chips=chips, **multi)
    out.update({k: v for k, v in extra.items() if not (k == "explanation" and v is None)})
    return out


_HINGLISH_RE = re.compile(
    r"\b(hai|tha|thi|ke|ki|ka|ne|nahi|nahin|mera|meri|mujhe|bina|kya|kaise|kyun|aur|"
    r"pakda|pakad|chala|chalate|raha|rahi|gaadi|gadi|bhai|yaar|kar|diya|liya)\b", re.I)
_HINGLISH_MIN = 2


def _llm_lead(session: dict, text: str, vcode: str) -> str:
    """Cloud mode only: one or two warm, FACT-FREE sentences from the LLM to
    open a grounded answer. Tiny prompt (~150 tokens) instead of the ~1.7k
    narrate prompt; anything factual it says is stripped by sanitize_lead,
    and any failure just returns "" (the grounded template stands alone)."""
    if not text or is_short_slot_answer(text) or len(text.split()) < 4:
        return ""
    try:
        from llm_chatbot import call_groq_narrate
        import nlu
        name = (get_graph_engine().get_violation(vcode) or {}).get("name", vcode)
        hinglish = len({m.lower() for m in _HINGLISH_RE.findall(text)}) >= _HINGLISH_MIN
        lang = "Hinglish (Hindi in Latin script), like the user" if hinglish else "English"
        system = (
            "You are DriveLegal, a warm, concise Indian traffic-law assistant. The user's "
            f"situation has been identified as: {name}. Write exactly ONE short sentence in {lang} "
            "that acknowledges what happened to them, in plain words. Do NOT give advice or "
            "tips (an official tip follows separately), do NOT mention money, fines, rupees, "
            "sections, laws or penalties, and do NOT ask questions. No markdown."
        )
        raw = call_groq_narrate(system, [], text, temperature=0.4, max_tokens=60)
        raw = _PROTOCOL_LINE_RE.sub("", raw or "")
        return nlu.sanitize_lead(raw, max_sentences=1)
    except Exception as exc:          # offline / rate-limited / anything
        log.info("lead: LLM unavailable (%s) — template only", exc)
        return ""


def _pre_route(session: dict, text: str, eng, *, calculator: bool = False) -> Optional[dict]:
    """Everything that can be answered deterministically before violation
    matching / the LLM. Order matters: specific intents first."""
    import nlu

    if not text:
        return None

    # Answer to our own "what vehicle were you on?" question.
    if (session.get("pending_slot") == "vehicle_segment" and session.get("violation_code")
            and not session.get("last_fine_card")):
        seg = eng.detect_vehicle_segment(text)
        if seg:
            session["pending_slot"] = None
            _set_vehicle(session, seg)
            return _answer_violation(session, text, session["violation_code"])

    small = nlu.smalltalk_reply(text)
    if small:
        return _reply_turn(session, text, small)

    card = session.get("last_fine_card")
    masked = nlu.mask_followup_terms(text)
    resolved = resolve_violation_from_text(
        masked, session.get("road_bucket"), session.get("vehicle_segment"), eng)
    new_match = resolved.get("match")
    is_question = bool(re.match(r"\s*(what|how|is|are|can|could|do|does|will|would|should|why|when|where|which)\b",
                                text, re.I)) or text.strip().endswith("?")

    if _SPEED_LIMIT_Q_RE.search(text):
        from offline_engine import _speed_limit_response
        bucket = ("highway" if re.search(r"\b(highways?|expressways?|nh|sh)\b", text, re.I)
                  else "street" if re.search(r"\b(city|streets?|residential|school)\b", text, re.I)
                  else session.get("road_bucket"))
        return _reply_turn(session, text, _speed_limit_response({**session, "road_bucket": bucket}))

    faq = nlu.faq_reply(text)
    if faq and (is_question or not new_match):
        return _reply_turn(session, text, faq)

    meta = nlu.meta_reply(text, session)
    if meta:
        return _reply_turn(session, text, meta, fine_card=None)

    # "what if it was a truck?" → same offence, new vehicle (or its sibling).
    seg = nlu.vehicle_what_if(text, session)
    if seg and card and not (new_match and new_match["code"] != card.get("violation_code")):
        prev = card
        vcode = card["violation_code"]
        note = ""
        if not nlu.violation_applies(vcode, seg):
            sib = nlu.sibling_for_vehicle(vcode, seg, session.get("last_user_story") or "")
            if not sib:
                name = (eng.get_violation(vcode) or {}).get("name", vcode)
                return _reply_turn(session, text,
                    f"**{name}** only applies to {SEGMENT_LABEL.get(session.get('vehicle_segment'), 'the original vehicle')}"
                    f" — it doesn't apply to a {SEGMENT_LABEL.get(seg, seg).lower()}. "
                    "Tell me what happened with that vehicle and I'll find the right rule.")
            note = (f"For a {SEGMENT_LABEL.get(seg, seg).lower()} the matching rule is "
                    f"**{(eng.get_violation(sib) or {}).get('name', sib)}**.")
            vcode = sib
        _set_vehicle(session, seg)
        out = _answer_violation(session, text, vcode, lead=note, intent="answer_updated",
                                previous_fine_card=prev)
        return out

    # "difference between this and drug driving" → side-by-side, grounded.
    if card and new_match and new_match["code"] != card.get("violation_code") \
            and re.search(r"\b(difference|differ\w*|compare\w*|comparison|vs\.?|versus)\b", text, re.I):
        other = eng.quick_fine(new_match["code"], session.get("state_code"),
                               session.get("city_code"), session.get("vehicle_fine_class"))
        if other:
            def _row(c):
                sec = f"MV Act §{c['mv_section']}" if c.get("mv_section") else "—"
                rep = f" · repeat {nlu._fmt(c.get('fine_repeat'))}" if c.get("fine_repeat") else ""
                imp = f" · imprisonment: {c['imprisonment']}" if c.get("imprisonment") else ""
                comp = "compoundable" if c.get("compoundable") else "not compoundable (court)"
                return (f"**{c['violation_name']}** — {sec}: {nlu._fmt(c.get('fine_first'))} first offence"
                        f"{rep}{imp}; {comp}.")
            reply = ("Here's how they compare in "
                     f"{session.get('city_name') or session.get('state_code')}:\n\n• {_row(card)}\n• {_row(other)}")
            return _reply_turn(session, text, reply, fine_card=card, fine_cards=[card, other])

    # Follow-up about the current answer ("is it compoundable?").
    followup = _maybe_followup(session, text, eng, new_match=new_match)
    if followup is not None:
        return followup

    # Several offences in one message → one combined, grounded answer.
    multi = nlu.resolve_multi(text, session.get("road_bucket"), session.get("vehicle_fine_class"))
    cats = {_get_vio_excl_category(c) for c in multi} - {None}
    if len(cats) > 1:          # helmet + seatbelt etc. → contradiction, clarify below
        multi = []
    if multi and session.get("state_code"):
        if not session.get("vehicle_segment"):
            _set_vehicle(session, eng.detect_vehicle_segment(text)
                         or next((nlu.vehicle_from_violation(c) for c in multi
                                  if nlu.vehicle_from_violation(c)), None))
        parts, cards, total = [], [], 0
        for code in multi:
            c = eng.quick_fine(code, session.get("state_code"), session.get("city_code"),
                               session.get("vehicle_fine_class"))
            if not c:
                continue
            cards.append(c)
            total += c.get("fine_first") or 0
            sec = f" (MV Act §{c['mv_section']})" if c.get("mv_section") else ""
            comp = "compoundable" if c.get("compoundable") else "**not compoundable** — goes to court"
            parts.append(f"• **{c['violation_name']}**{sec}: **{nlu._fmt(c.get('fine_first'))}** "
                         f"first offence — {comp}.")
        if len(cards) >= 2:
            loc = session.get("city_name") or session.get("state_code")
            reply = (f"You've described **{len(cards)} separate offences** in {loc}:\n\n"
                     + "\n".join(parts)
                     + f"\n\nTogether that's **{nlu._fmt(total)}** if "
                       + ("both are" if len(cards) == 2 else "all are") + " first offences. "
                       "Ask me about any of them for details (e.g. 'is the first one compoundable?').")
            session["violation_code"] = cards[0]["violation_code"]
            session["last_fine_card"] = cards[0]
            session["stage"] = "answered"
            session["card_history"] = (session.get("card_history") or []) + cards[1:]
            session["last_user_story"] = text
            return _reply_turn(session, text, reply, fine_card=cards[0],
                               detail_table=_build_detail_table(cards[0]), fine_cards=cards)

    # "I drive a car" — a vehicle on its own. (The calculator harvests
    # vehicles as slot answers instead.)
    seg = None if calculator else nlu.vehicle_statement(text)
    if seg:
        _set_vehicle(session, seg)
        pending = session.get("violation_code")
        if pending and not card and session.get("state_code"):
            return _answer_violation(session, text, pending)
        return _reply_turn(session, text,
            f"Got it — {SEGMENT_LABEL.get(seg, seg)}. What happened, or which rule are you "
            "worried about? (e.g. 'no helmet', 'jumped a signal', 'no insurance')")
    return None


def _history_reference(session: dict, text: str) -> Optional[dict]:
    """'the licence one' / 'the helmet fine' → an offence discussed earlier
    in this conversation whose name contains that word."""
    m = re.search(r"\b(?:the|that)\s+([a-z]+)\s+(?:one|fine|offence|challan|case|thing|rule)\b",
                  text or "", re.I)
    if not m:
        return None
    word = m.group(1).lower()
    word_alts = {word, {"license": "licence", "licence": "license"}.get(word, word)}
    for c in reversed(session.get("card_history") or []):
        name = (c.get("violation_name") or "").lower()
        if any(re.search(rf"\b{re.escape(w)}", name) for w in word_alts):
            return {"code": c["violation_code"], "name": c.get("violation_name"), "score": 99}
    return None


def _maybe_followup(session: dict, text: str, eng=None, *, new_match=None) -> Optional[dict]:
    """Answer a short follow-up about the last fine card deterministically.

    Skipped when there's no prior answer, during an incident thread, or when
    the text clearly names a *different* violation (that's a new question)."""
    from followups import followup_reply

    card = session.get("last_fine_card")
    if not card or session.get("conversation_mode") == "incident":
        return None
    reply = followup_reply(text, card)
    if reply is None:
        return None
    m = new_match
    if m is None:
        import nlu
        m = resolve_violation_from_text(
            nlu.mask_followup_terms(text), session.get("road_bucket"),
            session.get("vehicle_segment"), eng,
        ).get("match")
    if m is None:
        m = _history_reference(session, text)
    if m and m["code"] != card.get("violation_code"):
        # "is the helmet one compoundable?" — a follow-up question about a
        # DIFFERENT (usually earlier) offence: answer it about that one.
        eng = eng or get_graph_engine()
        other = eng.quick_fine(m["code"], session.get("state_code"), session.get("city_code"),
                               session.get("vehicle_fine_class"))
        other_reply = followup_reply(text, other) if other else None
        if not other_reply:
            return None
        card, reply = other, other_reply
        session["violation_code"] = card["violation_code"]
        session["last_fine_card"] = card
    session.setdefault("messages", []).append({"role": "user", "content": text})
    session.setdefault("messages", []).append({"role": "assistant", "content": reply})
    session["pending_slot"] = None
    return {
        "intent":       "narrate",
        "reply":        reply,
        "fine_card":    card,
        "detail_table": _build_detail_table(card),
        "chips":        None,
        "explanation":  None,
    }


def extract_and_reply(
    session: dict,
    user_text: str,
    *,
    llm_fn: Optional[Callable[[str, List[dict], str], str]] = None,
) -> dict:
    """LLM-driven narrate-phase turn.

    Pipeline: deterministic violation resolution → coherence / stale-slot
    guards → optional LLM (single call) → validate slots → fine_card +
    explanation. Skips Ollama when ambiguity-only or all slots are filled
    from keyword match.
    """
    if llm_fn is None:
        llm_fn = _call_narrate_llm

    eng = get_graph_engine()
    text = (user_text or "").strip()
    match_method = "llm"
    match_confidence = "medium"
    deterministic_match: Optional[dict] = None
    override_note: Optional[str] = None
    parsed: dict = {"slots": {}, "chips": None, "clean_reply": ""}

    # ── Guardrail layer (pre-LLM, zero added latency) ─────────────────────────
    guard = guardrail_response(text)
    if guard is not None:
        if text:
            session.setdefault("messages", []).append(
                {"role": "user", "content": text}
            )
        session.setdefault("messages", []).append(
            {"role": "assistant", "content": guard["reply"]}
        )
        return guard

    # ── Out-of-scope / greeting gate (pre-LLM) ────────────────────────────────
    scoped = scope_response(text)
    if scoped is not None:
        if text:
            session.setdefault("messages", []).append(
                {"role": "user", "content": text}
            )
        session.setdefault("messages", []).append(
            {"role": "assistant", "content": scoped.get("reply") or scoped.get("question") or ""}
        )
        if scoped.get("slot"):
            session["pending_slot"] = scoped["slot"]
        return scoped

    # ── Scenario Engine: multi-person / multi-offence / incident stories ─────
    # (also answers its own clarifying question and folds in corrections).
    try:
        from scenario import maybe_handle as _scenario_turn
        sc = _scenario_turn(session, text)
    except Exception:
        logging.getLogger("drivelegal.scenario").exception("scenario engine failed")
        sc = None
    if sc is not None:
        return sc

    # ── Deterministic understanding layer (nlu.py) ───────────────────────────
    pre = _pre_route(session, text, eng)
    if pre is not None:
        return pre

    # ── Intent / incident / slot-answer routing ───────────────────────────────
    routed = _route_conversation(session, text)
    if routed is not None:
        return routed

    # ── Stale slot guard ──────────────────────────────────────────────────────
    _clear_stale_violation_if_needed(session, text)

    vio_text = _text_for_violation_resolution(session, text)
    if text and not is_short_slot_answer(text):
        session["last_user_story"] = text

    resolved = resolve_violation_from_text(
        vio_text,
        session.get("road_bucket"),
        session.get("vehicle_segment"),
        eng,
    )
    deterministic_match = resolved.get("match")

    combined_text = _recent_user_text(session, text, n=2)

    # Infer vehicle from text when not set — or from the rule itself when it
    # only exists for one vehicle (helmet / pillion / triple riding → 2W).
    # A new story that clearly names a different vehicle ("…on my car")
    # replaces the remembered one.
    if text and session.get("vehicle_segment"):
        explicit = eng.explicit_vehicle_segment(text)
        if explicit and explicit != session["vehicle_segment"]:
            _set_vehicle(session, explicit)
    if not session.get("vehicle_segment") and text:
        import nlu
        seg = eng.detect_vehicle_segment(text)
        if not seg and deterministic_match:
            seg = nlu.vehicle_from_violation(deterministic_match["code"])
        if seg in _ALLOWED_SEGMENTS:
            _set_vehicle(session, seg)

    # ── Pre-LLM ambiguous vehicle (canned — no Ollama) ────────────────────────
    if not session.get("vehicle_segment"):
        hist_and_cur = combined_text
        pre_two_w = _scan_text_for_vio_keywords(
            hist_and_cur, _VEH_VIO_EXCLUSIVE["two_wheeler_only"]
        )
        pre_four_w = _scan_text_for_vio_keywords(
            hist_and_cur, _VEH_VIO_EXCLUSIVE["four_wheeler_only"]
        )
        if pre_two_w and pre_four_w:
            session["vehicle_segment"]    = None
            session["vehicle_fine_class"] = None
            session["vehicle_type"]       = None
            session["violation_code"]     = None
            session["last_fine_card"]       = None
            reply = _CANNED_AMBIGUOUS_VEHICLE
            if text:
                session.setdefault("messages", []).append(
                    {"role": "user", "content": text}
                )
            session.setdefault("messages", []).append(
                {"role": "assistant", "content": reply}
            )
            return {
                "intent":       "narrate",
                "reply":        reply,
                "fine_card":    None,
                "detail_table": None,
                "chips":        None,
                "explanation":  None,
            }

    # ── Ambiguous but RELEVANT candidates → vertical MCQ (not random chips) ───
    candidates = filter_violation_candidates(
        vio_text, resolved.get("candidates") or []
    )
    if not deterministic_match and len(candidates) >= 1:
        top_s = candidates[0][1]
        run_s = candidates[1][1] if len(candidates) > 1 else 0
        ambiguous = (
            len(candidates) >= 2
            and top_s >= _VIO_MATCH_MIN_SCORE
            and top_s < _VIO_MATCH_DOMINANCE * max(run_s, 1)
        )
        single_weak = len(candidates) == 1 and top_s >= _VIO_MATCH_MIN_SCORE
        if ambiguous or single_weak:
            if text:
                session.setdefault("messages", []).append(
                    {"role": "user", "content": text}
                )
            question = (
                "Based on what you described, these are the closest matches — "
                "pick the one that fits best (or choose 'Something else' to "
                "explain in your own words):"
            )
            mcq = build_violation_clarification(question, candidates, multi=True)
            session["pending_slot"] = "violation_code"
            session.setdefault("messages", []).append(
                {"role": "assistant", "content": question}
            )
            return {**mcq, "fine_card": None,
                    "detail_table": None, "explanation": None}

    # ── Zero relevant violations ──────────────────────────────────────────────
    # Not about traffic at all → polite out-of-scope (both engines; the LLM
    # would only decline anyway, so don't spend tokens on it).
    referential = bool(session.get("last_fine_card")) and bool(
        re.search(r"\b(this|that|it|this one|the fine|the challan)\b", text, re.I)) and len(text.split()) <= 12
    if (not deterministic_match and not candidates and text and not referential
            and not is_traffic_related(vio_text) and not is_short_slot_answer(text)
            and session.get("conversation_mode") != "incident"):
        from clarification_engine import _OUT_OF_SCOPE_REPLY
        if _STORY_START_RE.search(text):
            # Someone starting to tell what happened — invite them to go on.
            return _reply_turn(session, text,
                "Go on — I'm listening. What happened next? Were you stopped by the police, "
                "given a challan, or was there an accident?")
        return _reply_turn(session, text, _OUT_OF_SCOPE_REPLY, scope="out_of_scope")

    # Traffic-related but unmatched: the cloud LLM can reason about it; the
    # rules engine offers the topic router (with a context-aware question).
    use_llm = not session.get("_force_rules")
    if (
        not deterministic_match
        and not candidates
        and not use_llm
        and not is_short_slot_answer(text)
        and (is_traffic_related(vio_text) or substantive_tokens(vio_text))
        and session.get("conversation_mode") != "incident"
        and not session.get("_topic_router_done")
    ):
        if text:
            session.setdefault("messages", []).append(
                {"role": "user", "content": text}
            )
        mcq = build_zero_match_clarification(session)
        import nlu
        if nlu.has_enforcement_context(text):
            q = ("Got it. What did the officer say you did? Tick the closest area (or type it) "
                 "and I'll find the exact rule:")
            mcq = {**mcq, "question": q, "reply": q}
        session["pending_slot"] = "topic_router"
        session.setdefault("messages", []).append(
            {"role": "assistant", "content": mcq["question"]}
        )
        return {**mcq, "fine_card": None, "detail_table": None, "explanation": None}

    # ── High-confidence keyword match ───────────────────────────────────────
    known_violation: Optional[str] = None
    if deterministic_match:
        session["violation_code"] = deterministic_match["code"]
        known_violation = deterministic_match["code"]
        match_method = "keyword"
        match_confidence = "high"

    # ── Fast path: violation certain + location + vehicle → grounded answer ──
    # The fine lookup doesn't depend on road type, so don't wait for it. The
    # LLM (cloud mode) only adds a short fact-free lead; facts come from the
    # graph. Saves ~2k tokens per turn on Groq's free tier.
    if (known_violation
            and session.get("state_code")
            and session.get("vehicle_segment")):
        lead = "" if session.get("_force_rules") else _llm_lead(session, text, known_violation)
        if text and not is_short_slot_answer(text):
            session["last_user_story"] = text
        return _answer_violation(session, text, known_violation, lead=lead)

    # Violation certain, vehicle unknown, but the fine is the same for every
    # vehicle here → just answer.
    if (known_violation and session.get("state_code")
            and not session.get("vehicle_segment")
            and not eng.fine_varies_by_vehicle(known_violation, session.get("state_code"),
                                               session.get("city_code"))):
        lead = "" if session.get("_force_rules") else _llm_lead(session, text, known_violation)
        if text and not is_short_slot_answer(text):
            session["last_user_story"] = text
        return _answer_violation(session, text, known_violation, lead=lead)

    # Violation certain but vehicle unknown → ask for the vehicle directly
    # (no LLM guess — the LLM used to assume "car").
    if (known_violation and session.get("state_code")
            and not session.get("vehicle_segment")):
        name = (eng.get_violation(known_violation) or {}).get("name", known_violation)
        q = f"Got it — **{name}**. What vehicle were you on? The fine can differ by vehicle."
        session["pending_slot"] = "vehicle_segment"
        return _reply_turn(session, text, q, intent="ask_slot", slot="vehicle_segment",
                           chips=[{"id": k, "label": v} for k, v in (
                               ("two_wheeler", "Bike / Scooter"), ("four_wheeler", "Car / SUV"),
                               ("three_wheeler", "Auto-rickshaw"), ("four_wheeler_plus", "Bus / Minibus"),
                               ("heavy_vehicle", "Truck / LCV"))],
                           allow_text=True)

    if (known_violation
            and session.get("state_code")
            and session.get("road_bucket")
            and session.get("vehicle_segment")):
        fine_card = eng.quick_fine(
            violation_code     = session["violation_code"],
            state_code         = session.get("state_code"),
            city_code          = session.get("city_code"),
            vehicle_fine_class = session.get("vehicle_fine_class"),
        )
        if fine_card and _sanity_check_fine(
                fine_card, session["violation_code"], combined_text):
            session["last_fine_card"] = fine_card
            session["stage"] = "answered"
            reply = _template_narrate_reply(session, fine_card)
            detail_table = _build_detail_table(fine_card)
            explanation = _build_explanation(
                session, session["violation_code"], fine_card,
                match_method=match_method,
                match_confidence=match_confidence,
                deterministic_name=deterministic_match["name"],
            )
            if text:
                session.setdefault("messages", []).append(
                    {"role": "user", "content": text}
                )
            session.setdefault("messages", []).append(
                {"role": "assistant", "content": reply}
            )
            return {
                "intent":       "narrate",
                "reply":        reply,
                "fine_card":    fine_card,
                "detail_table": detail_table,
                "chips":        None,
                "explanation":  explanation,
            }

    # ── Single LLM call (Groq → offline_engine fallback) ─────────────────────
    history = (session.get("messages") or [])[-4:]
    system_prompt = _build_narrate_system_prompt(
        session, known_violation=known_violation
    )
    if session.get("_force_rules"):
        from offline_engine import narrate_protocol
        raw_reply = narrate_protocol(session, text, deterministic_match)
    else:
        # Pass session + deterministic_match so the offline fallback has context
        _llm = functools.partial(
            llm_fn,
            _session_ref         = session,
            _deterministic_match = deterministic_match,
        ) if llm_fn is _call_narrate_llm else llm_fn
        raw_reply = _llm(system_prompt, history, text)
    parsed = _parse_protocol(raw_reply)
    reply = parsed["clean_reply"]
    if not reply:
        reply = (
            "Could you give me a bit more detail about what happened? "
            "Even one extra line helps me find the right rule."
        )

    llm_used = not session.get("_force_rules")
    _apply_extracted_slots(session, parsed["slots"],
                           evidence=combined_text if llm_used else None)

    # Validate LLM violation_code against deterministic + subgraph allow-list.
    llm_vc = parsed["slots"].get("violation_code")
    allowed = _allowed_violation_codes(
        session.get("road_bucket"),
        session.get("vehicle_fine_class"),
        session.get("state_code"),
        eng,
    )
    if llm_vc and llm_vc != "?":
        validated, override_note = _validate_llm_violation_code(
            llm_vc, session, deterministic_match, allowed
        )
        if validated:
            session["violation_code"] = validated
            if override_note:
                match_method = "coherence_override" if deterministic_match else "keyword"
                match_confidence = "high"
            elif not deterministic_match:
                match_method = "llm"
                match_confidence = "medium"
        else:
            session["violation_code"] = (
                deterministic_match["code"] if deterministic_match else None
            )
    elif deterministic_match:
        session["violation_code"] = deterministic_match["code"]

    # (override_note is kept for the explanation panel; prefixing it to the
    #  reply read awkwardly — "I've matched this to X…" before every answer.)

    # ── Post-slot coherence (before fine_card) ────────────────────────────────
    _coh = _check_coherence(session)
    if _coh.get("contradiction"):
        _conflict = _coh.get("conflict")
        fine_card = None

        if _conflict == "vehicle_vs_violation":
            vc = session.get("violation_code")
            seg = session.get("vehicle_segment") or ""
            vio_cat = _get_vio_excl_category(vc) if vc else None
            vio_node = eng.get_violation(vc) if vc else None
            vio_display = (vio_node or {}).get("name", vc) if vc else "that violation"
            veh_type_a = (
                "two-wheeler (bike/scooter)"
                if vio_cat == "two_wheeler_only"
                else "four-wheeler (car/SUV)"
            )
            veh_type_b = (
                "two-wheeler (bike/scooter)"
                if seg in ("two_wheeler",)
                else "four-wheeler (car/SUV)"
            )
            clarification = (
                f"Wait — I noticed something. You mentioned '{vio_display}' which only "
                f"applies to {veh_type_a}s, but earlier I understood you were on a "
                f"{veh_type_b}. Could you help me understand — what exactly were you "
                "riding or driving?"
            )
            reply = clarification + ("\n\n" + reply if reply else "")
            session["violation_code"]     = None
            session["vehicle_segment"]    = None
            session["vehicle_fine_class"] = None
            session["vehicle_type"]       = None
            session["last_fine_card"]       = None

        elif _conflict == "ambiguous_vehicle":
            reply = _CANNED_AMBIGUOUS_VEHICLE
            session["vehicle_segment"]    = None
            session["vehicle_fine_class"]   = None
            session["vehicle_type"]         = None
            session["violation_code"]       = None
            session["last_fine_card"]       = None
    else:
        fine_card = None
        # Road type isn't an input to the fine lookup, so don't block the card
        # on it — otherwise a typed location (no map pin) never gets a card.
        if (session.get("state_code")
                and session.get("vehicle_segment")
                and session.get("violation_code")):
            fine_card = eng.quick_fine(
                violation_code     = session["violation_code"],
                state_code         = session.get("state_code"),
                city_code          = session.get("city_code"),
                vehicle_fine_class = session.get("vehicle_fine_class"),
            )
            if fine_card and not _sanity_check_fine(
                    fine_card, session["violation_code"], combined_text):
                log.warning(
                    "fine sanity mismatch: vio=%s text=%r",
                    session["violation_code"], combined_text[:80],
                )
                fine_card = None
                session["violation_code"] = (
                    deterministic_match["code"] if deterministic_match else None
                )
                if session["violation_code"]:
                    fine_card = eng.quick_fine(
                        violation_code     = session["violation_code"],
                        state_code         = session.get("state_code"),
                        city_code          = session.get("city_code"),
                        vehicle_fine_class = session.get("vehicle_fine_class"),
                    )
                    if fine_card and not _sanity_check_fine(
                            fine_card, session["violation_code"], combined_text):
                        fine_card = None
                        session["violation_code"] = None

            if fine_card:
                age = session.get("driver_age")
                if age is not None and age < 18:
                    fine_card["juvenile_surcharge"] = (
                        "Guardian/parent liability may apply — MV Act §199A. "
                        "The vehicle may be impounded and the guardian prosecuted."
                    )
                session["last_fine_card"] = fine_card
                session["stage"] = "answered"

    # ── Grounding: the LLM never states facts on its own ─────────────────────
    if llm_used:
        import nlu
        if fine_card:
            # Keep the model's human touch, but every number / section /
            # consequence comes from the graph template.
            lead = nlu.sanitize_lead(reply)
            grounded, _ = grounded_answer(session, session["violation_code"], lead=lead)
            reply = grounded
        else:
            problems = nlu.llm_reply_problems(
                reply, allowed_amounts=set(), allowed_sections=[], session=session)
            if problems:
                log.info("LLM reply had ungrounded facts (%s) — stripping", problems)
                sents = re.split(r"(?<=[.!?])\s+", reply)
                kept = [x for x in sents if not nlu.llm_reply_problems(
                    x, allowed_amounts=set(), allowed_sections=[], session=session)]
                reply = " ".join(kept).strip() or (
                    "Could you tell me a bit more about what happened — what were you "
                    "doing, and what did the officer say? I'll find the exact rule.")
        parsed["clean_reply"] = reply

    # ── Proactive inquiry policy (legacy text append — superseded by MCQ) ───
    inquiry_parts: List[str] = []
    clean_reply_for_history = parsed["clean_reply"]

    detail_table: Optional[List[dict]] = None
    explanation: Optional[dict] = None
    chips_payload: Optional[List[dict]] = None
    multi_select_flag = False
    selection_mode = "single"
    allow_other_flag = False

    if fine_card:
        detail_table = _build_detail_table(fine_card)
        explanation = _build_explanation(
            session,
            session["violation_code"],
            fine_card,
            match_method=match_method,
            match_confidence=match_confidence,
            deterministic_name=(
                (deterministic_match or {}).get("name")
            ),
        )
        # Attach driver-context checkbox MCQ (same UX as chip-driven path) —
        # offered once per conversation, not after every answer.
        if not session.get("_driver_context_done") and not session.get("_driver_context_offered"):
            session["_driver_context_offered"] = True
            followup = build_driver_context_clarification()
            reply = reply.rstrip() + "\n\n" + followup["question"]
            chips_payload = followup["chips"]
            multi_select_flag = True
            selection_mode = "multi"
            allow_other_flag = True
            session["pending_multi"] = "driver_context"

    if text:
        session.setdefault("messages", []).append(
            {"role": "user", "content": text}
        )
    session.setdefault("messages", []).append(
        {"role": "assistant", "content": clean_reply_for_history or reply}
    )

    if parsed.get("chips") and not chips_payload:
        chips_payload = [
            {"id": f"free:{opt}", "label": opt}
            for opt in parsed["chips"]
        ]
        multi_select_flag = True
        selection_mode = "multi"

    return {
        "intent":         "narrate",
        "reply":          reply,
        "fine_card":      fine_card,
        "detail_table":   detail_table,
        "chips":          chips_payload,
        "multi_select":   multi_select_flag,
        "selection_mode": selection_mode,
        "allow_other":    allow_other_flag,
        "allow_text":     True,
        "explanation":    explanation,
    }
