#!/usr/bin/env python3
"""
violation_resolver.py — Keyword → violation_code resolver
=========================================================
Uses the `keyword_to_vio` precomputed index on the graph (plus per-violation
`keywords` / `name` / `common_misconception` fields exposed by graph_engine as
`violations_by_keyword`) to deterministically map user free-text to one (or a
few) violation codes.

Scoring (rule of thumb):
  +5  exact phrase match (full keyword string contained in text)
  +3  multi-word keyword partial overlap
  +1  single-word match
  +2  road-bucket bonus if violation is registered under the current bucket
  +1  vehicle-applicability bonus
The top candidate dominates if its score is at least twice the runner-up
*and* it has score >= 3 → treated as a deterministic hit; otherwise the
caller (dialog_manager) renders chips for the top-N choices.

Public surface:

- resolve(text, road_bucket=None, vehicle_fine_class=None) -> list[(code, score)]
- top_chips(text, road_bucket=None, vehicle_fine_class=None, k=4) -> list[chip]
- chips_for_category(group, road_bucket=None, vehicle_fine_class=None, k=8) -> list[chip]
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from graph_engine import GraphEngine, _norm, get_graph_engine

# Skip these tokens when computing single-word overlap — they appear in too
# many violation strings to be useful as signal.
_STOPWORDS = {
    "a", "an", "and", "the", "of", "or", "to", "in", "on", "at", "for",
    # NOTE: "without" is intentionally NOT here — it is high-signal in traffic law
    # ("without helmet", "without licence", "without seatbelt", "without DL").
    # "with" is kept as a stopword since it rarely distinguishes violations.
    "with", "is", "was", "were", "be", "by", "from", "i", "my",
    "me", "we", "you", "your", "any", "no", "not", "do", "did", "but",
    "this", "that", "these", "those", "as", "if", "so", "it", "its",
    "got", "get", "gotten", "have", "has", "had", "vehicle", "vehicles",
    "driver", "drivers", "police", "fine", "fines", "penalty",
    # Contraction fragments ("don't" → "don t") carry no signal on their own.
    "dont", "don", "didnt", "didn", "doesnt", "doesn", "wasnt", "isnt", "cant",
    "can", "will", "would", "should", "what", "why", "how", "when", "which",
}

# Generic action verbs — high-frequency filler in user stories ("parked in",
# "driving on") that must NOT create phrase matches on their own. Note: the
# NOUN "parking" is deliberately not here (it's a real violation subject).
_ACTION_VERBS = {
    "parked", "driving", "drove", "driven", "riding", "rode", "ridden",
    "went", "going", "gone", "moving", "moved", "stopped", "standing", "waiting",
}


# Words too generic to count in single-token overlap (they still count inside
# full keyword phrases such as "drunk driving" or "phone while riding").
_OVERLAP_NOISE = _ACTION_VERBS | {
    "while", "ride", "drive", "bike", "car", "scooter", "scooty", "motorcycle",
    "road", "person", "people", "one", "time", "caught", "cop", "cops", "officer",
    "someone", "something", "said", "told", "today", "yesterday",
}


# Generic words that can't make a 2–3 word phrase distinctive on their own
# ("while driving", "broke the", "not working", "give way").
_PHRASE_GENERIC = {
    "while", "broke", "break", "working", "work", "give", "gave", "given", "using", "wearing",
    "took", "take", "taken", "left", "kept", "keep", "made", "make", "came", "come", "side",
    "road", "time", "near", "after", "before", "front", "back", "with", "have", "been", "there",
    "refused", "refuse", "refusing",
    # vehicle nouns: "on my bike" says nothing about WHICH offence
    "bike", "scooter", "scooty", "motorcycle", "truck", "lorry", "auto", "vehicle", "car", "cars",
    "bikes", "taxi",
}


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _edit1(a: str, b: str) -> bool:
    """True when a and b differ by exactly one insert/delete/substitute/swap."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


# Everyday words that must never be "typo-corrected" into a keyword.
_NO_CORRECT = {
    "night", "light", "being", "bring", "while", "where", "there", "their", "these",
    "those", "wrong", "right", "coming", "going", "doing", "since", "stole", "still",
    "spent", "sent", "want", "went", "cops", "copy", "file", "fire", "hire", "here",
}

_vocab_cache: Optional[set] = None


def _vocab(kw_index: dict) -> set:
    global _vocab_cache
    if _vocab_cache is None:
        _vocab_cache = {t for k in kw_index for t in _tokens(k) if len(t) >= 4}
    return _vocab_cache


def correct_typos(text: str, kw_index: dict) -> str:
    """Conservative spelling repair against the keyword vocabulary.

    Only tokens of 5+ letters that aren't already keywords are considered, the
    candidate must share the first letter, be a single edit away, and be the
    ONLY such candidate — so "helmt"→"helmet", "signel"→"signal", but "night"
    never becomes "light". Kept in sync with apps/mobile/src/offline/resolver.ts.
    """
    vocab = _vocab(kw_index)
    out = []
    changed = False
    for tok in re.split(r"(\W+)", text):
        low = tok.lower()
        if (len(low) >= 5 and low.isalpha() and low not in vocab
                and low not in _NO_CORRECT and low not in _STOPWORDS):
            cands = [v for v in vocab if v[0] == low[0] and _edit1(low, v)]
            if len(cands) == 1:
                out.append(cands[0])
                changed = True
                continue
        out.append(tok)
    return "".join(out) if changed else text


# (cue, preferred code, code it should beat). Kept in sync with
# apps/mobile/src/offline/resolver.ts.
# (topic required, cue, preferred code, code it should beat)
_WHO_DISAMBIG = [
    (re.compile(r"\bhelmet", re.I),
     re.compile(r"\b(pillion|passenger|co-?rider|back\s*seat|"
                r"(on|at|in)\s+the\s+back|sitting\s+(behind|at\s+the\s+back)|"
                r"behind\s+me|riding\s+behind)\b", re.I),
     "SAFETY_NO_HELMET_PILLION", "SAFETY_NO_HELMET_RIDER"),
    (re.compile(r"\bseat\s*-?belt", re.I),
     re.compile(r"\b(back\s*seat|rear|passenger|behind)\b", re.I),
     "SAFETY_NO_SEATBELT_PASSENGER", "SAFETY_NO_SEATBELT_DRIVER"),
    (re.compile(r"\bbreath|\bblow\b|\bbreathaly", re.I),
     re.compile(r"\brefus", re.I),
     "IMPAIRED_REFUSE_TEST", "IMPAIRED_DRUNK"),
]

# Stated ages: "4 year old on my bike" (child passenger) vs "my 17 year old
# drove" (underage driving).
_AGE_IN_TEXT = re.compile(r"\b(\d{1,2})\s*-?\s*(?:years?|yrs?)\s*-?\s*old\b", re.I)
_DRIVING_VERB = re.compile(r"\b(driv\w*|drove|rode|rid(e|es|ing)|riding)\b", re.I)


class ViolationResolver:
    def __init__(self, engine: Optional[GraphEngine] = None) -> None:
        self._eng = engine or get_graph_engine()

    # ── Core resolver ────────────────────────────────────────────────────────

    def resolve(
        self,
        text: str,
        road_bucket: Optional[str] = None,
        vehicle_fine_class: Optional[str] = None,
    ) -> List[Tuple[str, int]]:
        """Return `[(violation_code, score)]` sorted high → low.
        Empty list = nothing matched (caller should fall back to LLM)."""
        if not text:
            return []
        kw_index = self._eng.violations_by_keyword()
        text = correct_typos(text, kw_index)
        n_text = _norm(text)
        if not n_text:
            return []
        n_text_padded = f" {n_text} "

        scores: dict = {}

        # 1. Exact / substring phrase hits. A keyword found INSIDE a word only
        #    counts when it's long (plurals/inflections: "helmet"→"helmets");
        #    short ones were pure noise ("bac" in "back" → drunk driving).
        for kw, vcodes in kw_index.items():
            if not kw or len(kw) < 3:
                continue
            if kw in n_text_padded or kw == n_text:
                whole = f" {kw} " in n_text_padded or kw == n_text
                if not whole and (len(kw) < 5 or f" {kw}" not in n_text_padded):
                    continue
                bonus = 5 if whole else 3
                for vc in vcodes:
                    scores[vc] = scores.get(vc, 0) + bonus

        # 2. Per-token overlap — catches "no helmet" when keyword is
        # "two-wheeler rider without helmet".
        text_tokens = set(t for t in _tokens(text)
                          if t not in _STOPWORDS and t not in _OVERLAP_NOISE and len(t) > 2)
        if text_tokens:
            # Count each distinct overlapping token ONCE per violation. Summing
            # over every keyword string let long synonym lists inflate scores
            # ("school bus without CCTV" → no-licence via ten "without …" keys).
            for vc, vtoks in self._violation_tokens(kw_index).items():
                overlap = text_tokens & vtoks
                if overlap:
                    scores[vc] = scores.get(vc, 0) + len(overlap)

        # 2.5 Phrase match — a multi-word phrase from the user text appearing
        # verbatim inside a keyword is a strong, specific signal that single-token
        # overlap gets wrong. Fixes e.g. "no parking" (user) matching the keyword
        # "parking in no-parking zone" instead of "parked ... parking lights".
        toks = n_text.split()
        phrases: list = []
        for i in range(len(toks) - 1):
            phrases.append(f"{toks[i]} {toks[i+1]}")
            if i < len(toks) - 2:
                phrases.append(f"{toks[i]} {toks[i+1]} {toks[i+2]}")
        # keep only phrases carrying a real content word (drops "in no", "i parked")
        phrases = [p for p in phrases
                   if any(len(w) >= 4 and w not in _STOPWORDS and w not in _ACTION_VERBS
                          and w not in _PHRASE_GENERIC for w in p.split())]
        if phrases:
            # Map each phrase → the set of violations whose keyword contains it.
            phrase_hits: dict = {}
            for kw, vcodes in kw_index.items():
                if not kw or len(kw) < 5:
                    continue
                for p in phrases:
                    if p in kw:
                        phrase_hits.setdefault(p, set()).update(vcodes)
            # Only a phrase that points to exactly ONE violation is distinctive
            # enough to boost (e.g. "no parking" → PARK_NO_PARKING). A phrase that
            # hits several near-duplicate rules (e.g. "wear helmet" → rider+pillion)
            # is NOT boosted, so it can never erode the real top match's dominance.
            to_boost: set = set()
            for p, codes in phrase_hits.items():
                if len(codes) == 1:
                    to_boost |= codes
            for vc in to_boost:
                # +6 keeps it dominant even after the +2 road-bucket bonus.
                scores[vc] = scores.get(vc, 0) + 6

        if not scores:
            return []

        # 3. Bucket + vehicle relevance bonuses
        bucket_set = set(
            self._eng.indexes.get("vio_by_bucket", {}).get(road_bucket, [])
        ) if road_bucket else set()

        for vc in list(scores.keys()):
            nid = f"vio:{vc}"
            # Context bonuses refine real matches; they must never turn a
            # one-word coincidence ("how much" ~ "honking too much") into one.
            if scores[vc] < 3:
                continue
            if road_bucket and nid in bucket_set:
                scores[vc] += 2
            if vehicle_fine_class:
                node = self._eng.get_violation(vc) or {}
                va = node.get("vehicle_applicability") or ["ALL"]
                if va == ["ALL"] or vehicle_fine_class in va:
                    scores[vc] += 1

        # 4. Near-duplicate rules that differ only in WHO did it (rider vs
        #    pillion). Generic "helmet" keywords always favour the rider, so a
        #    clear pillion cue flips the ranking.
        def _prefer(prefer: str, losers) -> None:
            top = max([scores.get(prefer, 0)] + [scores.get(l, 0) for l in losers]) + 5
            scores[prefer] = top
            for l in losers:
                if l in scores:          # keep the match deterministic (≥2×)
                    scores[l] = min(scores[l], top // 2)

        for topic, cue, prefer, over in _WHO_DISAMBIG:
            if (prefer in scores or over in scores) and topic.search(text) and cue.search(text):
                _prefer(prefer, [over])

        am = _AGE_IN_TEXT.search(text)
        if am:
            age = int(am.group(1))
            if age <= 4 and re.search(r"\b(bike|scooter|scooty|two[-\s]?wheeler|helmet)\b", text, re.I):
                _prefer("SAFETY_NO_CHILD_2W", ["SAFETY_NO_HELMET_RIDER", "SAFETY_NO_HELMET_PILLION"])
            elif 5 <= age < 18 and _DRIVING_VERB.search(text):
                _prefer("DOC_UNDERAGE", [c for c in list(scores) if c not in ("DOC_UNDERAGE", "JUV_MINOR_DRIVING")])

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return ranked

    # ── Chip helpers (consumed by dialog_manager) ────────────────────────────

    def top_chips(
        self,
        text: str,
        road_bucket: Optional[str] = None,
        vehicle_fine_class: Optional[str] = None,
        k: int = 4,
    ) -> List[dict]:
        ranked = self.resolve(text, road_bucket, vehicle_fine_class)[:k]
        return [self._chip_for(vc, score) for vc, score in ranked]

    _vtok_cache: Optional[dict] = None

    def _violation_tokens(self, kw_index: dict) -> dict:
        """violation_code → set of meaningful tokens across all its keywords."""
        if ViolationResolver._vtok_cache is None:
            out: dict = {}
            for kw, vcodes in kw_index.items():
                toks = {t for t in _tokens(kw) if t not in _STOPWORDS and len(t) > 2}
                for vc in vcodes:
                    out.setdefault(vc, set()).update(toks)
            ViolationResolver._vtok_cache = out
        return ViolationResolver._vtok_cache

    def is_deterministic(self, ranked: List[Tuple[str, int]]) -> bool:
        """Single high-score hit — dialog_manager should accept it directly."""
        if not ranked:
            return False
        if len(ranked) == 1:
            return ranked[0][1] >= 3
        top, runner = ranked[0][1], ranked[1][1]
        return top >= 3 and top >= 2 * max(runner, 1)

    def chips_for_category(
        self,
        group: str,
        road_bucket: Optional[str] = None,
        vehicle_fine_class: Optional[str] = None,
        k: int = 8,
    ) -> List[dict]:
        """Return up to `k` violations chips inside a taxonomy group, filtered
        by the current road bucket + vehicle applicability when known."""
        vio_by_group = self._eng.indexes.get("vio_by_group", {}).get(group, [])
        bucket_set = set(
            self._eng.indexes.get("vio_by_bucket", {}).get(road_bucket, [])
        ) if road_bucket else set()

        chips: List[dict] = []
        for nid in vio_by_group:
            node = self._eng.nodes.get(nid)
            if not node:
                continue
            if bucket_set and nid not in bucket_set:
                continue
            if vehicle_fine_class:
                va = node.get("vehicle_applicability") or ["ALL"]
                if va != ["ALL"] and vehicle_fine_class not in va:
                    continue
            chips.append({"id": node["code"], "label": node.get("name", node["code"])})
            if len(chips) >= k:
                break

        if not chips:
            # Fallback: ignore bucket/vehicle filter so the user is never stuck.
            for nid in vio_by_group[:k]:
                node = self._eng.nodes.get(nid)
                if node:
                    chips.append({"id": node["code"], "label": node.get("name", node["code"])})
        return chips

    # ── Internal ─────────────────────────────────────────────────────────────

    def _chip_for(self, vcode: str, score: int = 0) -> dict:
        node = self._eng.get_violation(vcode) or {}
        return {
            "id":    vcode,
            "label": node.get("name", vcode),
            "score": score,
        }


_singleton: Optional[ViolationResolver] = None


def get_violation_resolver() -> ViolationResolver:
    global _singleton
    if _singleton is None:
        _singleton = ViolationResolver()
    return _singleton
