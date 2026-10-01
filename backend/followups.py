#!/usr/bin/env python3
"""
followups.py — deterministic answers to follow-up questions about the LAST fine
================================================================================
After an answer, users ask things like "is it compoundable?", "will I go to
jail?", "what about a repeat offence?", "what should I do now?". These carry no
violation keywords, so the resolver finds nothing and the conversation used to
fall into a topic-router MCQ (or crash in calculator mode without a cloud key).

This module answers them straight from the graph-sourced `last_fine_card`, so
the reply is instant, offline, and can never misquote an amount.
"""

from __future__ import annotations

import re
from typing import List, Optional

from graph_engine import get_graph_engine

# Each topic: (key, pattern). Order matters only for the reply layout.
_TOPICS = [
    ("compoundable", re.compile(r"\b(compound\w*|pay\s+(it\s+)?on\s+the\s+spot|spot\s+fine|settle|court)\b", re.I)),
    ("jail",         re.compile(r"\b(jail|prison|imprison\w*|arrest\w*|custody|lock(ed)?\s+up)\b", re.I)),
    ("repeat",       re.compile(r"\b(repeat\w*|second\s+time|third\s+time|next\s+time|subsequent|"
                                r"(caught|fined|stopped|do\s+it|happens?|did\s+it)\s+again)\b", re.I)),
    ("licence",      re.compile(r"\b(licen[cs]e|dl|suspend\w*|cancel\w*|disqualif\w*|points?)\b", re.I)),
    ("bail",         re.compile(r"\b(bail|bailable|non[-\s]?bailable|get\s+out\s+of\s+(jail|custody))\b", re.I)),
    ("section",      re.compile(r"\b(section|sec\.?|which\s+law|what\s+law|mv\s*act|legal\s+basis)\b", re.I)),
    ("pay",          re.compile(r"\b(pay\s+(it|this|that|the\s+(fine|challan))|how\s+(do|to|can)\s+i\s+pay|where\s+(do|can)\s+i\s+pay|payment|pay\s+online)\b", re.I)),
    ("next",         re.compile(r"\b(what\s+(should|do|can)\s+i\s+do|what\s+now|next\s+steps?|stopped|contest|dispute|appeal)\b", re.I)),
    ("avoid",        re.compile(r"\b(avoid|prevent|tips?|how\s+not\s+to)\b", re.I)),
    ("amount",       re.compile(r"\b(how\s+much|amount|cost|fine\s+again|total)\b", re.I)),
]

# Follow-ups are short and refer back ("it", "this", "that") or are bare
# questions. Long messages with new content are new stories, not follow-ups.
_MAX_FOLLOWUP_WORDS = 14


def _rs(v: Optional[int]) -> str:
    return f"₹{v:,}" if isinstance(v, (int, float)) and v else "not specified"


def detect_followup_topics(text: str) -> List[str]:
    t = (text or "").strip()
    if not t or len(t.split()) > _MAX_FOLLOWUP_WORDS:
        return []
    # Accidents, paperwork and licence how-tos are their own topics, not
    # questions about the previous fine ("accident — what do I do?").
    from clarification_engine import looks_like_incident
    from dynamic_chatbot import classify_intent
    if looks_like_incident(t) or classify_intent(t) in (
        "post_incident", "documents", "license_guidance",
    ):
        return []
    topics = [k for k, rx in _TOPICS if rx.search(t)]
    # "the licence one" is a reference to an offence, not a licence question.
    if "licence" in topics and re.search(r"\blicen[cs]e\s+(one|fine|offence|violation|challan|case)\b", t, re.I) \
            and not re.search(r"\b(suspend|cancel|disqualif|points?)\w*", t, re.I):
        topics.remove("licence")
    return topics


def followup_reply(text: str, card: Optional[dict]) -> Optional[str]:
    """Return a grounded reply, or None if `text` isn't a follow-up we handle."""
    if not card or not card.get("violation_code"):
        return None
    topics = detect_followup_topics(text)
    if not topics:
        return None

    vio  = get_graph_engine().get_violation(card["violation_code"]) or {}
    name = card.get("violation_name") or vio.get("name") or card["violation_code"]
    sec  = card.get("mv_section") or vio.get("mv_section")
    head = f"**{name}**" + (f" (MV Act §{sec})" if sec else "")
    first  = card.get("patch_fine_first") or card.get("fine_first")
    repeat = card.get("patch_fine_repeat") or card.get("fine_repeat")

    lines: List[str] = []
    for t in topics:
        if t == "compoundable":
            lines.append(
                "✅ Yes — it's **compoundable**: you can pay it on the spot or online "
                "via e-challan without going to court."
                if card.get("compoundable") else
                "❌ No — it's **not compoundable**. The challan goes to court and "
                "you'll need to appear (or pay through the virtual court)."
            )
        elif t == "jail":
            imp = card.get("imprisonment")
            lines.append(
                f"⚖️ Imprisonment: **{imp}**." if imp else
                "⚖️ No imprisonment is prescribed for this offence — it's a fine only."
            )
        elif t == "repeat":
            lines.append(
                f"🔁 Repeat offence: **{_rs(repeat)}** (first offence: {_rs(first)})."
                if repeat else
                f"🔁 The schedule doesn't list a separate repeat amount — the same "
                f"{_rs(first)} applies, though officers may add licence action."
            )
        elif t == "licence":
            lc = (card.get("licence_consequence") or vio.get("dl_consequence")
                  or vio.get("consequence"))
            lines.append(
                f"🪪 Licence impact: {lc}" if lc else
                "🪪 No mandatory licence suspension is listed for this offence, "
                "but repeat offences can be referred to the RTO."
            )
        elif t == "bail":
            if card.get("imprisonment"):
                lines.append(
                    "🔓 Most Motor Vehicles Act offences are **bailable** — if you're arrested, bail "
                    "can be granted at the police station or by the magistrate. Offences that "
                    "involve causing death (e.g. BNS §106) are treated more seriously. A lawyer "
                    "can confirm for your specific case.")
            else:
                lines.append("🔓 There's no arrest or jail for this offence — it's fine-only, so bail "
                             "doesn't come into it.")
        elif t == "section":
            lines.append(
                f"📖 It falls under **MV Act §{sec}**." if sec else
                "📖 The graph doesn't record a specific section for this one."
            )
        elif t == "pay":
            lines.append(
                "💳 Pay online at **echallan.parivahan.gov.in** (or your state traffic-police "
                "portal/app) using the challan number from the SMS — UPI, card and net banking "
                "work. If you pay on the spot, insist on the officer's e-challan device and a receipt."
                + ("" if card.get("compoundable") else
                   "\n\n⚠️ This offence isn't compoundable, so it may be listed for court — "
                   "check the challan status before paying."))
        elif t == "next":
            nxt = card.get("what_to_do_next") or vio.get("what_to_do_next")
            lines.append(
                (f"📋 {nxt}\n\n" if nxt else "")
                + "You can pay or contest any e-challan at **echallan.parivahan.gov.in**."
            )
        elif t == "avoid":
            tip = card.get("tips_to_avoid") or vio.get("tips_to_avoid")
            if tip:
                lines.append(f"💡 {tip}")
        elif t == "amount":
            lines.append(
                f"💰 First offence: **{_rs(first)}**"
                + (f" · Repeat: **{_rs(repeat)}**" if repeat else "")
                + f" ({card.get('fine_source', 'central')} schedule)."
            )

    if not lines:
        return None
    return f"About {head}:\n\n" + "\n\n".join(lines)
