#!/usr/bin/env python3
"""
nlu.py — deterministic conversation understanding shared by every mode
======================================================================
Both the Calculator (dialog_manager) and the Chatbot (dynamic_chatbot) run
free text through these helpers BEFORE slot-filling, violation matching or
any LLM call. Everything here is offline, instant and grounded:

  guardrail_reply   bribery / forgery / evasion requests → refusal
  smalltalk_reply   thanks / bye / "who are you" / "what can you do"
  faq_reply         common legal-process questions (DigiLocker, receipts,
                    contesting, compounding, towing, Good Samaritan, …)
  meta_reply        "what was the fine?", "which city am I in?", recap
  vehicle_*         "I drive a car", "what if it was a truck?"
  split_clauses     "no helmet and no licence" → two violations
  validate_llm_*    catch invented ₹ amounts / sections / codes in LLM text

Conversation evals (scripts/eval_conversations.py) showed these cases were
either falling into a generic topic menu or being answered wrongly by the
LLM; handling them here fixes both engines at once and saves LLM tokens.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from graph_engine import SEGMENT_FINE_CLASS, SEGMENT_LABEL, get_graph_engine

# ── Safety guardrail ──────────────────────────────────────────────────────────

UNSAFE_RE = re.compile(
    r"\b("
    r"bribe|bribing|bribed|ghoos|ghus|rishwat|chai\s*pani|"
    r"(pay|give|offer)\s+(the\s+)?(cop|cops|police|officer|constable)\s+(some\s+)?(money|cash|something)|"
    r"settle\s+(it\s+)?(with|in)\s+cash|"
    r"fake\s+(driving\s+)?(licen[cs]e|dl|rc|puc|number\s*plate|plate|insurance|challan|documents?|certificate)|"
    r"forged?\s+(driving\s+)?(licen[cs]e|dl|rc|puc|documents?|plate|certificate)|"
    r"duplicate\s+(number\s*plate|plate)|"
    r"(avoid|escape|evade|dodge|get\s+out\s+of|wriggle\s+out\s+of|skip)\s+"
    r"(?:(?:paying|pay|the|this|that|my|a)\s+){0,3}"
    r"(fine|challan|chalan|penalty|cop|cops|police|checkpost|check\s*post|rto)|"
    r"(avoid|escape|evade|dodge|beat|fool|trick)\s+(getting\s+)?(caught|detected|"
    r"(the\s+)?speed\s+cameras?|(the\s+)?cameras?|(the\s+)?breathaly[sz](er|ser)|(the\s+)?breath\s+test)|"
    r"how\s+(to|do\s+i|can\s+i)\s+(not\s+)?(get\s+)?(caught|fined|challaned)|"
    r"(run|flee|escape)\s+(away\s+)?from\s+(the\s+)?(cops?|police)|"
    r"(pass|cheat|beat)\s+(a\s+|the\s+)?breath(aly[sz](er|ser))?(\s+test)?|"
    r"tamper\w*\s+(with\s+)?(the\s+|my\s+)?(speed\s+governor|meter|number\s*plate|fastag|odometer)|"
    r"(remove|hide|cover|mask)\s+(my\s+|the\s+)?number\s*plate"
    r")\b",
    re.I,
)

UNSAFE_REPLY = (
    "I hear you — fines are frustrating. But I can't help with avoiding, "
    "evading, or 'managing' a challan, or with fake documents. That's a "
    "separate offence and can land you in much bigger trouble. What I *can* "
    "do: explain exactly what the rule is, what the correct fine should be, "
    "how to pay or contest it the legal way, and how to avoid it next time. "
    "Want me to walk you through any of those?"
)


def is_unsafe(text: str) -> bool:
    return bool(text and UNSAFE_RE.search(text))


# ── Small talk ────────────────────────────────────────────────────────────────

_THANKS_RE = re.compile(r"^\s*(ok(ay)?\s+)?(thanks?|thank\s+you|thx|ty|dhanyavaad|shukriya)"
                        r"(\s+(so\s+much|a\s+lot|bro|man))?[\s!.]*$", re.I)
_BYE_RE = re.compile(r"^\s*(bye|goodbye|see\s+you|good\s*night|tata|cya)[\s!.]*$", re.I)
_ACK_RE = re.compile(r"^\s*(ok|okay|k|cool|got\s+it|alright|fine|great|nice|hmm+|oh|i\s+see)[\s!.]*$", re.I)
_IDENTITY_RE = re.compile(r"\b(who\s+are\s+you|what\s+are\s+you|your\s+name|are\s+you\s+(a\s+)?(bot|human|ai|real))\b", re.I)
_CAPABILITY_RE = re.compile(r"\b(what\s+can\s+you\s+do|how\s+can\s+you\s+help|what\s+do\s+you\s+do|help\s+me\s+with\s+what)\b", re.I)
_HOWAREYOU_RE = re.compile(r"^\s*(how\s+are\s+you|how'?s\s+it\s+going|how\s+r\s+u)[\s?!.]*$", re.I)


def smalltalk_reply(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None
    if _THANKS_RE.match(t):
        return "You're welcome! Drive safe — ask me anytime you need the exact rule or fine. 🚦"
    if _BYE_RE.match(t):
        return "Take care and drive safe! 🙏"
    if _HOWAREYOU_RE.match(t):
        return "Doing well, thanks! What traffic question can I help you with?"
    if _IDENTITY_RE.search(t):
        return ("I'm **DriveLegal** — an assistant for Indian road law. I match what happened to "
                "the exact Motor Vehicles Act rule and give you the correct fine for your state "
                "and city, plus what to do next. I also work offline.")
    if _CAPABILITY_RE.search(t):
        return ("I can: find the exact fine and MV Act section for a traffic offence in your state "
                "or city, tell you if it's compoundable or goes to court, walk you through what to "
                "do after an accident, explain documents (DL, RC, insurance, PUC), and show how a "
                "fine changes in another city. Just describe what happened.")
    if _ACK_RE.match(t):
        return "👍 Anything else you'd like to check — another fine, a different city, or what to do next?"
    return None


# ── FAQ (grounded, law-process knowledge) ─────────────────────────────────────
# Each entry: (pattern, answer). Answers stick to well-established, national
# rules and point to official portals; state-specific amounts come from the
# graph, never from here.

_FAQ: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(digi\s*locker|m\s*parivahan|digital\s+(copy|copies|documents?)|soft\s*copy|e[-\s]?copy|photo\s+of\s+(my\s+)?(dl|licen[cs]e|rc))\b", re.I),
     "✅ Yes — documents in **DigiLocker** or **mParivahan** are legally valid. The Central Motor "
     "Vehicles Rules (Rule 139) allow DL, RC, insurance and PUC to be shown in electronic form, "
     "and officers can verify them online. A photo or scanned PDF on your phone is **not** the "
     "same — use the DigiLocker/mParivahan app copy."),
    (re.compile(r"\b(receipt|e[-\s]?challan\s+(slip|copy)|proof\s+of\s+payment|pay\s+(in\s+)?cash|cash\s+to\s+(the\s+)?(cop|police|officer))\b", re.I),
     "🧾 Most states issue **e-challans** — you get an SMS with the challan number and can pay "
     "online. If you pay on the spot, it should be through the officer's e-challan device "
     "(card/UPI) and you should get an official receipt. Don't hand over cash without a receipt. "
     "You can check any challan at **echallan.parivahan.gov.in**."),
    (re.compile(r"\b(contest|dispute|challenge|appeal|wrong(ly)?\s+(fined|challan\w*)|not\s+my\s+fault|virtual\s+court)\b", re.I),
     "⚖️ If you believe a challan is wrong, **don't pay it** — unpaid challans go to court. Many "
     "states send traffic challans to the **Virtual Courts** (vcourts.gov.in), where you can "
     "contest online; otherwise you appear before the traffic magistrate with your evidence "
     "(photos, dashcam, documents). Check the challan status first at echallan.parivahan.gov.in."),
    (re.compile(r"\b(what\s+(does|is)\s+(a\s+)?compound\w*|compound\w*\s+(means?|meaning)|meaning\s+of\s+compound\w*|non[-\s]?compoundable\s+(means?|meaning))\b", re.I),
     "📘 A **compoundable** offence can be settled by paying the compounding fee (on the spot or "
     "online) — no court case (MV Act §200). A **non-compoundable** offence (e.g. drunk driving) "
     "must go to court, where the magistrate decides the penalty."),
    (re.compile(r"\b(why\s+(is|are)\s+(the\s+)?(fine|fines|penalt\w+)\s+(so\s+)?(high|much|expensive|steep))\b", re.I),
     "📈 Fines were raised sharply by the **Motor Vehicles (Amendment) Act, 2019** — India records "
     "well over 1.5 lakh road deaths a year, so penalties were made a real deterrent. States can "
     "also set their own compounding amounts, which is why the same offence can cost different "
     "amounts in different states."),
    (re.compile(r"\b(seize|seized|seizure|impound\w*|detain\w*)\b|"
                r"\b(police|cops?|officer|they)\b.{0,25}\b(take|took|taking|snatch\w*)\b.{0,15}\b(keys?|vehicle|bike|car)\b", re.I),
     "🚓 Under **MV Act §207**, police can detain a vehicle driven without a valid licence, "
     "registration or permit (and for some serious offences). You should get a seizure memo/"
     "receipt, and you get the vehicle back after producing documents and paying the fine. "
     "Officers shouldn't snatch keys — you can ask for their name and badge number."),
    (re.compile(r"\b(can|could|may|are|is)\b.{0,25}\b(police|cops?|officer)\b.{0,25}\b(check|search|see|look\s+(at|through)|unlock|go\s+through)\b.{0,20}\b(my\s+)?(phone|mobile|bag|boot|dickey|trunk|car)\b", re.I),
     "🛡️ At a routine traffic check, officers can ask for your **DL, RC, insurance and PUC** "
     "(DigiLocker copies are valid). The Motor Vehicles Act doesn't give them a general power "
     "to go through your phone or belongings for a traffic offence — a search or seizure "
     "has to follow legal procedure, and anything seized must be recorded with a receipt. "
     "Stay polite, ask for the officer's name/badge, and consult a lawyer if something "
     "seems wrong."),
    (re.compile(r"\b(who\s+(has\s+to|should|will|must)\s+pay|someone\s+else\s+was\s+driving|"
                r"(friend|brother|sister|son|daughter|driver)\s+was\s+driving\s+my)\b", re.I),
     "👥 It depends on the challan type. A challan issued **on the spot** is made out to the "
     "person driving (against their DL). **Camera / e-challans** are linked to the vehicle "
     "number and sent to the **registered owner**, who has to pay or contest it — you can "
     "settle it with whoever was driving. Note: letting someone **without a valid licence** "
     "drive your vehicle is a separate offence for the owner (MV Act §180)."),
    (re.compile(r"\b(tow(ed|ing)?\s+(away|yard|charges?)|where\s+is\s+my\s+(car|bike|vehicle)|get\s+my\s+(car|bike|vehicle)\s+back)\b", re.I),
     "🚚 A towed vehicle goes to the traffic police towing yard for that area. Call the city "
     "traffic helpline or check the towing notice to locate it; you pay the parking fine plus a "
     "towing charge to release it. Carry your DL and RC."),
    (re.compile(r"\b(check|see|find|know)\s+(my\s+)?(pending\s+)?(challans?|e[-\s]?challans?|fines?)\b|\bpending\s+challans?\b|\bhow\s+(do|can)\s+i\s+pay\s+(a|my|the)\s+challan\b", re.I),
     "🔎 Check and pay challans at **echallan.parivahan.gov.in** → *Check Challan Status* "
     "(enter your vehicle number or DL number). Many states also have their own traffic-police "
     "portal/app. Pay only on official sites."),
    (re.compile(r"\b(good\s+samaritan|help(ing)?\s+(an\s+)?(accident\s+)?victim|take\s+(someone|victim|him|her)\s+to\s+(the\s+)?hospital)\b", re.I),
     "🤝 Helping is protected: **MV Act §134A (Good Samaritan)** — someone who helps an accident "
     "victim in good faith can't be held liable, and hospitals can't make you pay or wait. "
     "Call **112** or **108** and stay only as long as you're comfortable."),
    (re.compile(r"\b(hit[-\s]and[-\s]run\s+(compensation|victim)|compensation\s+for\s+(hit|accident|victim))\b", re.I),
     "💰 Victims of **hit-and-run** accidents can claim compensation under the Hit and Run Motor "
     "Accidents Scheme (₹2 lakh for death, ₹50,000 for grievous injury). For other accidents, "
     "claims go to the Motor Accident Claims Tribunal (MACT) or through the insurer."),
    (re.compile(r"\b(what\s+should\s+i\s+do\s+if\s+(i'?m|i\s+am|a\s+cop|police)\s+(stopped|pull\w*\s+over)|what\s+are\s+my\s+rights|my\s+rights)\b", re.I),
     "🛑 If you're stopped: stay calm, pull over safely, switch off the engine, and show your DL, "
     "RC, insurance and PUC (DigiLocker copies are valid). Ask for an **e-challan** rather than "
     "paying cash, and note the officer's name/badge. If you disagree with the challan, you can "
     "contest it in court instead of paying."),
    (re.compile(r"\b(report|complain\w*)\b.{0,30}\b(bribe|corrupt\w*|cop|police|officer)\b|\b(cop|police|officer)\b.{0,20}\b(asked|demand\w*)\b.{0,20}\b(money|bribe|cash)\b", re.I),
     "📣 If an officer demands a bribe, don't pay. Note the name/badge, time and place, and "
     "complain to your state's **Anti-Corruption Bureau / Vigilance** helpline or the traffic "
     "police's official grievance portal. Insist on an official e-challan."),
]


def faq_reply(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t or is_unsafe(t):
        return None
    for rx, answer in _FAQ:
        if rx.search(t):
            return answer
    return None


# ── Session meta / recall ─────────────────────────────────────────────────────

_RECALL_FINE_RE = re.compile(
    r"\b(what\s+(was|is)\s+(the|that)\s+(fine|amount|penalty)(\s+(again|you\s+(just\s+)?(told|said|gave|mentioned)))?"
    r"|how\s+much\s+(was|is)\s+(it|that)\s+again|remind\s+me\s+(of\s+)?(the\s+)?(fine|amount)|say\s+(that|the\s+fine)\s+again)\b", re.I)
_WHERE_RE = re.compile(r"\b(where\s+am\s+i|which\s+(city|state|place)\s+(am\s+i|did\s+i|is\s+this)|what('?s|\s+is)\s+my\s+(location|city|state))\b", re.I)
_WHICH_VEH_RE = re.compile(r"\b(what|which)\s+vehicle\s+(am\s+i|did\s+i|do\s+i|was\s+i)\b", re.I)
_RECAP_RE = re.compile(r"\b(summar\w+|recap|what\s+have\s+we\s+(discussed|covered)|total\s+(fine|amount)s?|"
                       r"how\s+much\s+(in\s+)?total|my\s+total|total\s+so\s+far|add\s+(it|them)\s+(all\s+)?up|"
                       r"all\s+the\s+fines)\b", re.I)


def _fmt(amount) -> str:
    return f"₹{int(amount):,}" if isinstance(amount, (int, float)) and amount else "not specified"


def meta_reply(text: str, session: dict) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None
    card = session.get("last_fine_card") or {}
    if _RECALL_FINE_RE.search(t) and card:
        rep = f" · repeat offence **{_fmt(card.get('fine_repeat'))}**" if card.get("fine_repeat") else ""
        return (f"For **{card.get('violation_name')}** it's **{_fmt(card.get('fine_first'))}** for a "
                f"first offence{rep} ({card.get('fine_source', 'central')} schedule).")
    if _WHERE_RE.search(t):
        loc = session.get("city_name") or session.get("state_code")
        if loc:
            st = get_graph_engine().get_state(session.get("state_code") or "") or {}
            return (f"📍 I'm using **{session.get('city_name') or st.get('name')}"
                    f"{', ' + st['name'] if session.get('city_name') and st.get('name') else ''}** "
                    "for fines. Tell me another city (e.g. 'what if I was in Pune?') to compare.")
        return "I don't have your location yet — tell me your state or city."
    if _WHICH_VEH_RE.search(t):
        if session.get("vehicle_segment"):
            return f"🚗 I have you on a **{SEGMENT_LABEL.get(session['vehicle_segment'], session['vehicle_segment'])}**."
        return "You haven't told me the vehicle yet — was it a bike/scooter, car, auto, bus or truck?"
    if _RECAP_RE.search(t):
        hist = session.get("card_history") or ([card] if card else [])
        if not hist:
            return None
        lines, total = [], 0
        for c in hist:
            amt = c.get("fine_first")
            total += amt or 0
            lines.append(f"• **{c.get('violation_name')}** — {_fmt(amt)}")
        tail = f"\n\nTotal if all were first offences: **{_fmt(total)}**." if len(hist) > 1 and total else ""
        return "Here's what we've covered:\n" + "\n".join(lines) + tail
    return None


# ── Vehicles ──────────────────────────────────────────────────────────────────

_WHAT_IF_RE = re.compile(r"\b(what\s+if|and\s+if|what\s+about|how\s+about|instead|for\s+an?\b|if\s+(it\s+was|i\s+was|i'?d\s+been)|same\s+for|and\s+(on|in|for)\s+an?)\b", re.I)
_VEH_STATEMENT_RE = re.compile(
    r"^\s*(i|we)\s+(drive|ride|have|own|use|was\s+(on|in|driving|riding)|am\s+(on|in|driving|riding))\s+"
    r"(an?\s+|my\s+|the\s+)?[\w\s-]{0,20}[\s.!]*$", re.I)


def vehicle_what_if(text: str, session: dict) -> Optional[str]:
    """'what if it was a truck?' → new segment (only when a card exists and
    the text is a short hypothetical naming a DIFFERENT vehicle)."""
    t = (text or "").strip()
    if not t or len(t.split()) > 12 or not session.get("last_fine_card"):
        return None
    if not _WHAT_IF_RE.search(t):
        return None
    seg = get_graph_engine().detect_vehicle_segment(t)
    if seg and seg != session.get("vehicle_segment"):
        return seg
    return None


def vehicle_statement(text: str) -> Optional[str]:
    """'I drive a car' / 'I was on my bike' with nothing else → segment."""
    t = (text or "").strip()
    if not t or len(t.split()) > 8 or not _VEH_STATEMENT_RE.match(t):
        return None
    return get_graph_engine().detect_vehicle_segment(t)


def vehicle_from_violation(vcode: Optional[str]) -> Optional[str]:
    """Infer the user's vehicle when the rule only exists for one segment
    (helmet / pillion / triple riding → two-wheeler)."""
    if not vcode:
        return None
    va = (get_graph_engine().get_violation(vcode) or {}).get("vehicle_applicability") or ["ALL"]
    if va == ["2W"]:
        return "two_wheeler"
    return None


def violation_applies(vcode: str, segment: Optional[str]) -> bool:
    if not segment:
        return True
    va = (get_graph_engine().get_violation(vcode) or {}).get("vehicle_applicability") or ["ALL"]
    fc = SEGMENT_FINE_CLASS.get(segment)
    return va == ["ALL"] or not fc or fc in va


def sibling_for_vehicle(vcode: str, segment: str, story: str = "") -> Optional[str]:
    """Same-group violation that applies to *segment* (e.g. SPEED_OVER_LMV →
    SPEED_OVER_MMV_HMV for a truck). None if no sensible sibling exists."""
    eng = get_graph_engine()
    vio = eng.get_violation(vcode) or {}
    grp = vio.get("grp")
    if not grp:
        return None
    from violation_resolver import get_violation_resolver
    ranked = get_violation_resolver().resolve(
        f"{story} {vio.get('name', '')}", vehicle_fine_class=SEGMENT_FINE_CLASS.get(segment))
    for code, _score in ranked:
        node = eng.get_violation(code) or {}
        if code != vcode and node.get("grp") == grp and violation_applies(code, segment):
            generic = {"not", "wearing", "driver", "rider", "a", "of", "the", "without",
                       "in", "on", "by", "vehicle", "driving", "using", "while", "lmv"}
            name_a = set(re.findall(r"[a-z]+", vio.get("name", "").lower())) - generic
            name_b = set(re.findall(r"[a-z]+", node.get("name", "").lower())) - generic
            if len(name_a & name_b) >= 2:          # genuinely the same offence family
                return code
    return None


def adjust_for_vehicle(vcode: str, segment: Optional[str], story: str = "") -> str:
    """If *vcode* doesn't apply to the chosen vehicle, switch to its same-family
    sibling (overspeeding: car rule → heavy-vehicle rule). Otherwise unchanged."""
    if not vcode or not segment or violation_applies(vcode, segment):
        return vcode
    return sibling_for_vehicle(vcode, segment, story) or vcode


# ── Follow-up vs new-violation disambiguation ────────────────────────────────

_NEG_BEFORE_LICENCE = re.compile(r"\b(without|no|expired|fake|forgot|lost|don'?t\s+have|didn'?t\s+have|never\s+had)\s+(a\s+|my\s+|the\s+|driving\s+)*$", re.I)
_FOLLOWUP_TERMS = re.compile(
    r"\b(suspend\w*|cancel\w*|disqualif\w*|jail|prison|imprison\w*|arrest\w*|court|compound\w*|repeat\w*|"
    r"section|contest|appeal|pay|paid|receipt|points?)\b", re.I)


def mask_followup_terms(text: str) -> str:
    """Remove consequence words so the resolver only sees offence words.
    'licence' is masked unless it's the offence itself ("without a licence")."""
    t = _FOLLOWUP_TERMS.sub(" ", text or "")
    out = []
    for m in re.finditer(r"\S+", t):
        w = m.group(0)
        if (re.fullmatch(r"(licen[cs]e|dl)\W*", w, re.I)
                and not _NEG_BEFORE_LICENCE.search(t[:m.start()])
                # "the licence one / licence fine" refers to the offence itself
                and not re.match(r"\s*(one|fine|offence|violation|challan|case|thing)\b", t[m.end():], re.I)):
            continue
        out.append(w)
    return " ".join(out)


_FINE_QUERY_RE = re.compile(
    r"\b((fine|penalty|punishment|challan)\s+(for|of|if|on)|how\s+much\s+(is|would|will)|"
    r"what('?s|\s+is)\s+the\s+(fine|penalty|punishment))\b", re.I)
_ENFORCEMENT_RE = re.compile(
    r"\b(stopped|caught|fined|challan\w*|booked|pulled\s+over|police|cops?|officer|ticket|towed|"
    r"without|expired|forgot|lapsed|didn'?t\s+have|don'?t\s+have|no\s+(valid\s+)?\w+)\b", re.I)


def is_fine_query(text: str) -> bool:
    return bool(_FINE_QUERY_RE.search(text or ""))


def has_enforcement_context(text: str) -> bool:
    return bool(_ENFORCEMENT_RE.search(text or ""))


# ── Multiple violations in one message ───────────────────────────────────────

_CLAUSE_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\s+also\b|\balso\b|\bplus\b|\bas\s+well\s+as\b|&|\band\b)\s*", re.I)


def split_clauses(text: str) -> List[str]:
    parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(text or "") if p and p.strip()]
    return [p for p in parts if len(p) > 2]


def resolve_multi(text: str, road_bucket: Optional[str], vehicle_fine_class: Optional[str]) -> List[str]:
    """Distinct deterministic violations named in separate clauses (≥2), else []."""
    clauses = split_clauses(text)
    if len(clauses) < 2:
        return []
    from violation_resolver import get_violation_resolver
    r = get_violation_resolver()
    found: List[str] = []
    for i, cl in enumerate(clauses):
        # Carry the verb/object context forward: "no helmet and no licence while riding"
        ranked = r.resolve(cl, road_bucket, vehicle_fine_class)
        if ranked and r.is_deterministic(ranked) and ranked[0][0] not in found:
            found.append(ranked[0][0])
    # Collapse rider/pillion style near-duplicates (same group, same wording).
    return found if len(found) >= 2 else []


# ── LLM output validation / composition ──────────────────────────────────────

_RUPEE_RE = re.compile(r"(?:₹|rs\.?\s?|inr\s?)\s?(\d[\d,\s]*\d|\d)(?:\s*[–-]\s*(?:₹\s?)?(\d[\d,]*))?", re.I)
_SECTION_RE = re.compile(r"(?:§|section|sec\.?)\s*(\d{2,3}[A-Z]?(?:\s*\(\s*\w+\s*\))*)", re.I)
_CODE_RE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+){1,}\b")


def _num(s: str) -> Optional[int]:
    d = re.sub(r"[^\d]", "", s or "")
    return int(d) if d else None


def amounts_in(text: str) -> set:
    return {n for pair in _RUPEE_RE.findall(text or "") for n in (_num(x) for x in pair) if n}


def _norm_section(s: str) -> str:
    return re.sub(r"[\s()]", "", (s or "").lower())


def llm_reply_problems(reply: str, *, allowed_amounts: set, allowed_sections: List[str],
                       session: dict) -> List[str]:
    """Reasons an LLM reply can't be shown as-is (empty list = fine)."""
    problems = []
    bad_amts = sorted(a for a in amounts_in(reply) if a not in allowed_amounts)
    if bad_amts:
        problems.append(f"amounts {bad_amts}")
    ok_secs = {_norm_section(s)[:4] for s in allowed_sections if s}
    for sec in _SECTION_RE.findall(reply or ""):
        ns = _norm_section(sec)
        if not any(ns.startswith(a[:3]) for a in ok_secs):
            problems.append(f"section {sec}")
    if _CODE_RE.search(reply or ""):
        problems.append("internal code")
    return problems


_LEAD_DROP_RE = re.compile(
    r"(₹|\brs\.?\s?\d|§|\bsection\b|\bsec\.|\bfine\b|\bpenalt|\bcompoundable\b|\bimprison|\bjail\b|"
    r"\bwhat'?s\s+your\s+age\b|\bhow\s+old\b|\bwere\s+you\s+(on|in|riding|driving)\b|"
    r"\btwo[-\s]?wheeler\s+or\b|\bbike\s+or\s+(a\s+)?(car|scooter)\b|\b[A-Z]{2,}_[A-Z0-9_]+\b)", re.I)


def sanitize_lead(reply: str, max_sentences: int = 2) -> str:
    """Keep only the LLM's human, fact-free sentences (empathy / context /
    safety tip) — every number, section and slot question is dropped because
    the grounded template supplies those."""
    sents = re.split(r"(?<=[.!?])\s+", (reply or "").strip())
    kept = [s.strip() for s in sents if s.strip() and not _LEAD_DROP_RE.search(s)]
    return " ".join(kept[:max_sentences]).strip()
