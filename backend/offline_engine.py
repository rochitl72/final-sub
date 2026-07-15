#!/usr/bin/env python3
"""
offline_engine.py — Rich rule-based chatbot fallback (no LLM required)
=======================================================================
Used when Groq API is unreachable (offline / low network).
Produces the SAME <<SLOTS ...>> protocol format as the LLM so
dynamic_chatbot._parse_protocol works identically on both paths.

Coverage:
  • ALL violations in the graph — via auto-template generation from
    violation nodes (name, mv_section, fines, tips, misconceptions, etc.)
  • Slot collection — conversational asks for missing state/vehicle/violation
  • Fine timeline — shows patch history when gov.in updates exist
  • Incidents — 4-step accident procedure (MV Act §134)
  • Documents — DL, RC, Insurance, PUC facts
  • License guidance — Parivahan / RTO steps
  • General law — shows top violations for current road/vehicle context
  • Greetings / out-of-scope — handled gracefully

Public surface:
  narrate_protocol(session, user_text, deterministic_match=None) -> str
    Returns a reply string with a trailing <<SLOTS ...>> protocol line.
    Drop-in replacement for _call_narrate_llm output.
"""

from __future__ import annotations

import random
import re
from typing import Dict, List, Optional

from graph_engine import get_graph_engine
from violation_resolver import get_violation_resolver

# ── Canned greetings / sign-offs ─────────────────────────────────────────────

_GREET_PATTERNS = re.compile(
    r"^\s*(hi|hello|hey|namaste|namaskar|hola|good\s+(morning|afternoon|evening|day)"
    r"|howdy|sup|what'?s up)\b",
    re.I,
)

_FAREWELL_PATTERNS = re.compile(
    r"^\s*(bye|goodbye|ok thanks?|thanks?|thank you|ok|okay|got it|alright|great)\b\s*$",
    re.I,
)

_GREET_REPLIES = [
    "Namaste! 🙏 I'm DriveLegal — your offline traffic law assistant. "
    "Ask me about any fine, traffic rule, or road law in India and I'll give you "
    "the exact answer straight from the Motor Vehicles Act.",
    "Hello! I'm DriveLegal. Even without internet, I have the complete Indian traffic "
    "law dataset ready. What would you like to know — a fine amount, a rule, or something else?",
    "Hey there! DriveLegal here — fully offline and ready. What traffic law question can I help with?",
]

_FAREWELL_REPLIES = [
    "Drive safe out there! 🙏 Come back anytime you need traffic law guidance.",
    "Glad I could help! Stay safe on the roads. 🚦",
    "You're welcome! Remember — most fines are avoidable with small habits. Safe driving!",
]

# ── Document facts (DL, RC, Insurance, PUC) ──────────────────────────────────

_DOC_FACTS: Dict[str, str] = {
    "dl": (
        "**Driving Licence (MV Act §3, §9)** — carry the original or a DigiLocker digital "
        "copy; both are legally valid. Driving **without a valid DL**: ₹5,000 fine (first "
        "offence), up to ₹10,000 on repeat + possible vehicle seizure. Renewal is online via "
        "[parivahan.gov.in](https://parivahan.gov.in) — renew before expiry to avoid the late fee."
    ),
    "rc": (
        "**RC — Registration Certificate (MV Act §39)** — your vehicle's identity document. "
        "Must be carried (original or DigiLocker copy) whenever you drive. Driving **without RC**: "
        "₹5,000 fine + possible vehicle seizure. RC renewal is due every 15 years (private vehicles)."
    ),
    "insurance": (
        "**Third-party insurance (MV Act §146)** — MANDATORY. Comprehensive cover is optional "
        "but strongly recommended. Driving **without valid insurance**: ₹2,000 fine + up to "
        "3 months imprisonment. Renew online with any insurer; keep a soft copy in DigiLocker."
    ),
    "puc": (
        "**PUC — Pollution Under Control certificate (CMVR Rule 115)** — required for all "
        "vehicles. Validity: petrol cars 1 year, diesel 6 months, two-wheelers/older vehicles "
        "3 months. Driving **without valid PUC**: ₹10,000 fine. Get it done at any authorized "
        "PUC centre — takes 5 minutes, costs ₹50–₹100."
    ),
}

# ── Accident procedure ────────────────────────────────────────────────────────

_ACCIDENT_STEPS = [
    "Stop immediately and switch on hazard lights — do NOT drive away (MV Act §134).",
    "Check on anyone injured. If serious: call **112** (emergency) or **108** (ambulance) right away.",
    "Exchange name, phone, RC number, and insurance details with the other party. Take photos of vehicles, damage, and the scene.",
    "Report at the nearest police station within 24 hours (MV Act §134) — even for minor accidents, a written FIR protects you legally.",
    "Inform your insurance company as soon as possible to initiate the claim process.",
]

# ── License guidance ──────────────────────────────────────────────────────────

_LICENSE_STEPS = [
    "Apply for a **Learner's Licence** on [parivahan.gov.in](https://parivahan.gov.in/parivahansewa) or at your local RTO. Minimum age: 16 (gearless 50cc & under), 18 (car/other two-wheelers), 20 (transport vehicles).",
    "Pass the online **theory test** — multiple choice on road signs and basic rules (free, takes ~15 min).",
    "Wait **at least 30 days** after getting the LL before applying for the permanent DL.",
    "Book a **driving test slot** at your RTO. On passing, the permanent DL is issued (usually mailed within 2–3 weeks or available digitally).",
    "While on an LL, display the **'L' plate** and have a licensed driver beside you at all times.",
]

# ── Slot collection questions ─────────────────────────────────────────────────

_ASK_STATE = (
    "To get the exact fine amount I need to know **which state** you're in — "
    "fines under the MV Act can vary by state. Just tell me your state or tap one below."
)

_ASK_VEHICLE = (
    "Got it! One more thing — **what type of vehicle** were you on? "
    "Bike/scooter, car, auto, or something else? The fine can differ by vehicle class."
)

_ASK_VIOLATION = (
    "I'd be happy to help with the fine. Could you describe **what happened** — "
    "for example: 'no helmet', 'jumped a red light', 'speeding', or 'no seatbelt'? "
    "I'll match it to the exact rule."
)

_UNCLEAR_REPLY = (
    "I want to make sure I give you accurate information. Could you tell me a bit more "
    "about what you're asking? For example: which violation, which state, and what vehicle "
    "you were using. I have the complete Indian traffic law database ready offline."
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slots_line(
    road_bucket: str = "?",
    vehicle_segment: str = "?",
    violation_code: str = "?",
    needs: str = "none",
) -> str:
    return (
        f"<<SLOTS road_bucket={road_bucket} "
        f"vehicle_segment={vehicle_segment} "
        f"violation_code={violation_code} "
        f"driver_age=? has_licence=? licence_type=? repeat_offender=? "
        f"NEEDS={needs}>>"
    )


def _with_slots(reply: str, session: dict, vio_code: str = "?", needs: str = "none") -> str:
    return (
        reply.strip()
        + "\n"
        + _slots_line(
            road_bucket     = session.get("road_bucket") or "?",
            vehicle_segment = session.get("vehicle_segment") or "?",
            violation_code  = vio_code,
            needs           = needs,
        )
    )


def _fmt_fine(amount: Optional[int]) -> str:
    if amount is None:
        return "as per schedule"
    return f"₹{amount:,}"


def _loc_str(session: dict) -> str:
    if session.get("city_name"):
        return session["city_name"]
    if session.get("state_code"):
        eng = get_graph_engine()
        st = eng.get_state(session["state_code"])
        return (st or {}).get("name", session["state_code"])
    return "your area"


# ── Core fine response builder ────────────────────────────────────────────────

def _build_fine_response(session: dict, vio_code: str) -> str:
    """
    Generate a rich natural-language response for a known violation.
    Uses every field in the graph + patches. No LLM needed.
    """
    eng  = get_graph_engine()
    vio  = eng.get_violation(vio_code) or {}
    card = eng.quick_fine(
        violation_code     = vio_code,
        state_code         = session.get("state_code"),
        city_code          = session.get("city_code"),
        vehicle_fine_class = session.get("vehicle_fine_class"),
    )

    name     = vio.get("name") or vio_code
    sec      = vio.get("mv_section")
    compound = vio.get("compoundable", False)
    tips     = vio.get("tips_to_avoid") or ""
    what_do  = vio.get("what_to_do_next") or ""
    conseq   = vio.get("dl_consequence") or vio.get("consequence") or ""
    misc     = vio.get("common_misconception") or ""
    grp      = (vio.get("grp") or "").replace("_", " ").title()
    loc      = _loc_str(session)
    veh      = session.get("vehicle_type") or "your vehicle"

    parts: List[str] = []

    # ── Lead line ──────────────────────────────────────────────────────────────
    sec_str  = f" (MV Act §{sec})" if sec else ""
    src_note = ""
    if card:
        src = card.get("fine_source", "central")
        src_note = f" — {src} schedule"

    if card and card.get("fine_first"):
        first_str  = _fmt_fine(card["fine_first"])
        repeat_str = _fmt_fine(card.get("fine_repeat")) if card.get("fine_repeat") else None

        lead = (
            f"For **{name}**{sec_str} in **{loc}** ({veh}), "
            f"the first-offence fine is **{first_str}**{src_note}."
        )
        if repeat_str:
            lead += f" Repeat offence: **{repeat_str}**."
    else:
        lead = (
            f"**{name}**{sec_str} is an offence under the Motor Vehicles Act. "
            f"I couldn't pinpoint the exact amount for {loc}/{veh} — the central "
            f"MV Act schedule applies."
        )
    parts.append(lead)

    # ── Imprisonment / compoundable ────────────────────────────────────────────
    details: List[str] = []
    if card and card.get("imprisonment"):
        details.append(f"Imprisonment: up to {card['imprisonment']}.")
    compound_note = (
        "✅ Compoundable — can be paid on-the-spot to the officer."
        if compound else
        "❌ NOT compoundable — requires a court appearance."
    )
    details.append(compound_note)
    if details:
        parts.append(" ".join(details))

    # ── Patch timeline (updated fines from gov.in) ─────────────────────────────
    if card and card.get("patch_fine_first"):
        pf    = card["patch_fine_first"]
        pdate = card.get("patch_effective_date") or "recently"
        parts.append(
            f"⚠️ **Updated fine**: ₹{pf:,} effective {pdate} "
            f"(sourced from official gov.in update)."
        )
    if card and card.get("patch_timeline") and len(card["patch_timeline"]) > 1:
        tl = card["patch_timeline"]
        timeline_lines = []
        for entry in tl:
            lbl = entry.get("label", "")
            ff  = entry.get("fine_first")
            src = entry.get("source", "")
            summ = entry.get("summary", "")
            if ff:
                line = f"  • {lbl}: ₹{ff:,}"
                if summ:
                    line += f" — {summ}"
                timeline_lines.append(line)
        if timeline_lines:
            parts.append("**Fine history:**\n" + "\n".join(timeline_lines))

    # ── DL / licence consequence ───────────────────────────────────────────────
    if conseq:
        parts.append(f"🪪 **Licence impact**: {conseq}")

    # ── Tips ──────────────────────────────────────────────────────────────────
    if tips:
        parts.append(f"💡 **Tip to avoid it**: {tips}")

    # ── What to do ────────────────────────────────────────────────────────────
    if what_do:
        parts.append(f"📋 **If you're stopped**: {what_do}")

    # ── Common misconception ──────────────────────────────────────────────────
    if misc:
        parts.append(f"❌ **Common myth**: {misc}")

    # ── City enforcement note ──────────────────────────────────────────────────
    if session.get("city_code"):
        enf = eng.city_enforcement_summary(session["city_code"])
        if enf:
            # Only surface the camera/enforcement line — don't dump everything
            for line in enf.splitlines():
                if "camera" in line.lower() or "anpr" in line.lower() or "enforcement" in line.lower():
                    parts.append(f"📷 {line.strip()}")
                    break

    return "\n\n".join(parts)


# ── Intent / keyword detection ────────────────────────────────────────────────

_DOC_RE    = re.compile(r"\b(rc|registration|dl|driving licen[cs]e|insurance|puc|pollution|emission|fitness)\b", re.I)
_LIC_RE    = re.compile(r"\b(learner|learners|ll|how (to|do i) get|apply for|minimum age|parivahan)\s*(licen[cs]e|dl|driving)?\b", re.I)
_ACC_RE    = re.compile(r"\b(accident|crash|collision|hit (and|&) run|pedestrian.*hit|injured|bleeding|what (do|should) i do)\b", re.I)
_SPEED_RE  = re.compile(r"\b(speed limit|how fast|maximum speed|kmph|km/h)\b", re.I)
_GREET_RE  = _GREET_PATTERNS
_BYE_RE    = _FAREWELL_PATTERNS
_GENERAL_RE = re.compile(
    r"\b(what are|tell me|explain|rules|laws|penalties|common (fines|violations)|what (happens|is the fine))\b", re.I
)


def _detect_document_keys(text: str) -> List[str]:
    keys = []
    t = text.lower()
    if re.search(r"\b(rc|registration certificate)\b", t): keys.append("rc")
    if re.search(r"\b(dl|driving licen[cs]e|licence|license)\b", t):   keys.append("dl")
    if "insurance" in t:                                                keys.append("insurance")
    if re.search(r"\b(puc|pollution|emission)\b", t):                  keys.append("puc")
    return keys or list(_DOC_FACTS.keys())


def _speed_limit_response(session: dict) -> str:
    eng    = get_graph_engine()
    bucket = session.get("road_bucket") or "main_road"
    speeds = eng.speeds_by_bucket(bucket)
    loc    = _loc_str(session)
    veh    = session.get("vehicle_type") or "vehicles"

    if speeds:
        lines = [f"  • **{name}**: {spd} km/h" for name, spd in speeds.items()]
        return (
            f"Speed limits for **{bucket.replace('_',' ')} roads** near {loc} "
            f"(for {veh}):\n" + "\n".join(lines)
            + "\n\nNote: school zones and residential areas may have lower local limits. "
            "Speed cameras (ANPR) are active on most expressways — violations are auto-challaned."
        )
    return (
        "General Indian speed limits (MV Act / CMVR):\n"
        "  • Expressway: 120 km/h (car), 80 km/h (bus/truck)\n"
        "  • National/State highway: 100 km/h (car), 60 km/h (bus/truck)\n"
        "  • City roads: 70 km/h (car), 60 km/h (others)\n"
        "  • School/hospital zone: 25 km/h\n\n"
        "Penalty for speeding: ₹1,000–₹2,000 (first), ₹2,000–₹4,000 (repeat). "
        "Licence suspension for excessive speeding."
    )


def _general_law_response(session: dict) -> str:
    """Show top violations for current context — useful for 'what are the rules here?' queries."""
    eng    = get_graph_engine()
    bucket = session.get("road_bucket") or "main_road"
    veh    = [session["vehicle_fine_class"]] if session.get("vehicle_fine_class") else None
    vios   = eng.get_violation_context(bucket, veh, session.get("state_code"), limit=8)
    loc    = _loc_str(session)

    if not vios:
        return (
            "I have the complete Indian traffic law dataset ready. "
            "Ask me about a specific fine (e.g. 'fine for no helmet'), a rule, "
            "or a situation (e.g. 'I had an accident') and I'll give you the exact answer."
        )

    lines = []
    for v in vios[:6]:
        code = v["code"]
        nm   = v.get("name", code)
        sec  = v.get("mv_section")
        fine = eng.get_fine(code, session.get("state_code"), session.get("city_code"),
                            session.get("vehicle_fine_class"))
        fine_str = f"₹{fine['first_offence']:,}" if (fine and fine.get("first_offence")) else "varies"
        sec_str  = f" §{sec}" if sec else ""
        lines.append(f"  • **{nm}**{sec_str} — {fine_str}")

    veh_label  = session.get("vehicle_type") or "all vehicles"
    road_label = bucket.replace("_", " ")
    return (
        f"Here are the key traffic violations for **{road_label} roads** near "
        f"**{loc}** ({veh_label}):\n\n"
        + "\n".join(lines)
        + "\n\nAsk me about any of these for the full fine details, MV Act section, and tips."
    )


# ── Main public function ──────────────────────────────────────────────────────

def narrate_protocol(
    session: dict,
    user_text: str,
    deterministic_match: Optional[dict] = None,
) -> str:
    """
    Full offline narrate-phase handler.
    Returns a reply string + trailing <<SLOTS ...>> protocol line.
    This is a drop-in replacement for _call_narrate_llm output.
    """
    text = (user_text or "").strip()
    t_lo = text.lower()

    # ── Greeting ──────────────────────────────────────────────────────────────
    if _GREET_RE.match(text):
        reply = random.choice(_GREET_REPLIES)
        return _with_slots(reply, session)

    # ── Farewell ──────────────────────────────────────────────────────────────
    if _BYE_RE.match(text):
        reply = random.choice(_FAREWELL_REPLIES)
        return _with_slots(reply, session)

    # ── Accident / incident ───────────────────────────────────────────────────
    if _ACC_RE.search(text):
        steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_ACCIDENT_STEPS))
        reply = (
            "Take a breath — here's exactly what to do right now:\n\n"
            + steps
            + "\n\n*Reference: MV Act §134, BNS §106 (if fatality involved).*"
        )
        return _with_slots(reply, session)

    # ── Documents ─────────────────────────────────────────────────────────────
    if _DOC_RE.search(text):
        doc_keys = _detect_document_keys(text)
        chunks = [_DOC_FACTS[k] for k in doc_keys if k in _DOC_FACTS]
        reply = (
            "Here's what you need to know about the documents you mentioned:\n\n"
            + "\n\n".join(chunks)
            + "\n\n💡 Keep digital copies in **DigiLocker** — legally equivalent to originals at any checkpoint."
        )
        return _with_slots(reply, session)

    # ── License guidance ──────────────────────────────────────────────────────
    if _LIC_RE.search(text):
        steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_LICENSE_STEPS))
        state_code = session.get("state_code")
        eng = get_graph_engine()
        state_note = ""
        if state_code:
            st = eng.get_state(state_code)
            if st:
                state_note = (
                    f"\n\nIn **{st.get('name', state_code)}**, the standard minimum ages apply: "
                    "16 (gearless 50cc & under), 18 (car/other two-wheelers), 20 (transport vehicles)."
                )
        reply = (
            "Getting a driving licence in India is a straightforward process. Here are the steps:\n\n"
            + steps
            + state_note
            + "\n\n**Portal:** https://parivahan.gov.in/parivahansewa"
        )
        return _with_slots(reply, session)

    # ── Speed limit query ─────────────────────────────────────────────────────
    if _SPEED_RE.search(text):
        reply = _speed_limit_response(session)
        return _with_slots(reply, session)

    # ── Violation-specific fine query ─────────────────────────────────────────
    # Use deterministic match passed from extract_and_reply, or try resolver
    vio_code: Optional[str] = None

    if deterministic_match:
        vio_code = deterministic_match.get("code")

    if not vio_code and session.get("violation_code"):
        vio_code = session["violation_code"]

    if not vio_code:
        # Try to resolve from text
        resolver = get_violation_resolver()
        ranked   = resolver.resolve(
            text,
            road_bucket        = session.get("road_bucket"),
            vehicle_fine_class = session.get("vehicle_fine_class"),
        )
        if ranked and resolver.is_deterministic(ranked):
            vio_code = ranked[0][0]
        elif ranked and ranked[0][1] >= 2:
            # Weak match — still use it but be less confident
            vio_code = ranked[0][0]

    if vio_code:
        # We know the violation — check for missing slots
        if not session.get("state_code"):
            reply = (
                f"I found the rule for that violation. "
                + _ASK_STATE
            )
            return _with_slots(reply, session, vio_code=vio_code, needs="state_code")

        if not session.get("vehicle_segment"):
            reply = (
                f"Got the violation — "
                + _ASK_VEHICLE
            )
            return _with_slots(reply, session, vio_code=vio_code, needs="vehicle_segment")

        # All slots present — generate rich response
        session["violation_code"] = vio_code
        reply = _build_fine_response(session, vio_code)
        return _with_slots(reply, session, vio_code=vio_code)

    # ── We have all context but no clear violation — show top violations ───────
    if session.get("state_code") and session.get("vehicle_segment"):
        # Ask for violation clarification
        eng    = get_graph_engine()
        bucket = session.get("road_bucket") or "main_road"
        veh    = [session["vehicle_fine_class"]] if session.get("vehicle_fine_class") else None
        vios   = eng.get_violation_context(bucket, veh, session.get("state_code"), limit=6)
        loc    = _loc_str(session)
        veh_label = session.get("vehicle_type") or "your vehicle"

        if vios:
            vio_names = ", ".join(f"'{v.get('name', v['code'])}'" for v in vios[:4])
            reply = (
                f"I have all the traffic law data for **{loc}** ({veh_label}). "
                f"Could you tell me which specific violation you're asking about? "
                f"For example: {vio_names} — or describe what happened in your own words."
            )
        else:
            reply = _UNCLEAR_REPLY
        return _with_slots(reply, session, needs="violation_code")

    # ── Missing both state and vehicle — general law overview ─────────────────
    if _GENERAL_RE.search(text) or len(text) > 10:
        reply = _general_law_response(session)
        needs = "none"
        if not session.get("state_code"):
            needs = "state_code"
        elif not session.get("vehicle_segment"):
            needs = "vehicle_segment"
        return _with_slots(reply, session, needs=needs)

    # ── Fallback ──────────────────────────────────────────────────────────────
    return _with_slots(_UNCLEAR_REPLY, session, needs="violation_code")
