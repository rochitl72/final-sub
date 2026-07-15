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
}

# Generic action verbs — high-frequency filler in user stories ("parked in",
# "driving on") that must NOT create phrase matches on their own. Note: the
# NOUN "parking" is deliberately not here (it's a real violation subject).
_ACTION_VERBS = {
    "parked", "driving", "drove", "driven", "riding", "rode", "ridden",
    "went", "going", "gone", "moving", "moved", "stopped", "standing", "waiting",
}


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


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
        n_text = _norm(text)
        if not n_text:
            return []
        n_text_padded = f" {n_text} "

        kw_index = self._eng.violations_by_keyword()
        scores: dict = {}

        # 1. Exact / substring phrase hits
        for kw, vcodes in kw_index.items():
            if not kw or len(kw) < 3:
                continue
            if kw in n_text_padded or kw == n_text:
                bonus = 5 if (f" {kw} " in n_text_padded or kw == n_text) else 3
                for vc in vcodes:
                    scores[vc] = scores.get(vc, 0) + bonus

        # 2. Per-token overlap — catches "no helmet" when keyword is
        # "two-wheeler rider without helmet".
        text_tokens = set(t for t in _tokens(text) if t not in _STOPWORDS and len(t) > 2)
        if text_tokens:
            for kw, vcodes in kw_index.items():
                kw_tokens = set(t for t in _tokens(kw) if t not in _STOPWORDS and len(t) > 2)
                if not kw_tokens:
                    continue
                overlap = text_tokens & kw_tokens
                if overlap:
                    delta = len(overlap)
                    for vc in vcodes:
                        scores[vc] = scores.get(vc, 0) + delta

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
                          for w in p.split())]
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
            if road_bucket and nid in bucket_set:
                scores[vc] += 2
            if vehicle_fine_class:
                node = self._eng.get_violation(vc) or {}
                va = node.get("vehicle_applicability") or ["ALL"]
                if va == ["ALL"] or vehicle_fine_class in va:
                    scores[vc] += 1

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
