#!/usr/bin/env python3
"""
location_resolver.py — State / city free-text resolver
=======================================================
Wraps the pre-built alias dicts on `GraphEngine` (`state_by_alias`,
`city_by_alias`) so the rule-based dialog manager can map user free text
("madras", "Bengaluru", "TN") to canonical {state_code, city_code} pairs.

Public surface (all pure functions, no Ollama):

- parse_state(text)                       → state_code | None
- parse_city(text, state_code=None)       → city_code  | None
- disambiguate(text, state_code=None)     → {"ambiguous": bool, "candidates": [...]}
- resolve_pin(lat, lng)                   → {"state_code", "city_code", "city_name"}

Cities that exist in more than one state (e.g. "Hyderabad") are surfaced via
`disambiguate`; the dialog manager should then ask the user to pick a state
before retrying.
"""

from __future__ import annotations

from typing import Optional

from graph_engine import GraphEngine, _norm, get_graph_engine


class LocationResolver:
    """All methods are O(1) dict lookups over the precomputed indexes."""

    def __init__(self, engine: Optional[GraphEngine] = None) -> None:
        self._eng = engine or get_graph_engine()

    # ── State ────────────────────────────────────────────────────────────────

    def parse_state(self, text: str) -> Optional[str]:
        if not text:
            return None
        n = _norm(text)
        if not n:
            return None
        alias = self._eng.state_by_alias()
        if n in alias:
            return alias[n]
        # Substring fallback — longest match wins so "tamil nadu state" still hits.
        best_code = None
        best_len  = 0
        for k, code in alias.items():
            if len(k) <= 2:
                continue
            if k in n and len(k) > best_len:
                best_code = code
                best_len  = len(k)
        return best_code

    # ── City ─────────────────────────────────────────────────────────────────

    def parse_city(self, text: str, state_code: Optional[str] = None) -> Optional[str]:
        if not text:
            return None
        n = _norm(text)
        if not n:
            return None
        alias = self._eng.city_by_alias()
        hits = alias.get(n)
        if not hits:
            # Substring fallback for "I was in Mumbai today" style input.
            best = None
            best_len = 0
            for k, candidates in alias.items():
                if len(k) <= 3:
                    continue
                if k in n and len(k) > best_len:
                    best = candidates
                    best_len = len(k)
            hits = best
        if not hits:
            return None
        if state_code:
            for code, st in hits:
                if st == state_code:
                    return code
        if len(hits) == 1:
            return hits[0][0]
        return None  # ambiguous → caller should use `disambiguate`

    def disambiguate(self, text: str, state_code: Optional[str] = None) -> dict:
        """Return all candidate (city_code, city_name, state_code) entries.

        Result shape:
            {"ambiguous": True,  "candidates": [{"code","name","state_code","state_name"}]}
            {"ambiguous": False, "candidates": [...one entry...]}
        """
        n = _norm(text)
        alias = self._eng.city_by_alias()
        hits = list(alias.get(n) or [])
        if not hits:
            # Substring fallback — collect every alias that appears in the
            # normalized text (longest first). "I'm in Bangalore" → BLR.
            substr_hits = []
            for k, candidates in alias.items():
                if len(k) <= 3:
                    continue
                if k in n:
                    substr_hits.append((len(k), candidates))
            substr_hits.sort(key=lambda x: -x[0])
            seen = set()
            for _, candidates in substr_hits:
                for entry in candidates:
                    if entry not in seen:
                        hits.append(entry)
                        seen.add(entry)
        if state_code:
            hits = [h for h in hits if h[1] == state_code]

        cities = self._eng.cities_by_code()
        states = self._eng.states_by_code()
        out = []
        for code, st in hits:
            city = cities.get(code, {})
            state = states.get(st, {})
            out.append({
                "code":       code,
                "name":       city.get("name", code),
                "state_code": st,
                "state_name": state.get("name", st),
            })
        return {"ambiguous": len(out) > 1, "candidates": out}

    # ── Pin → state/city ──────────────────────────────────────────────────────

    def resolve_pin(self, lat: float, lng: float) -> dict:
        """Map (lat, lng) → nearest city + its state, using the existing
        spatial index on the engine."""
        nearest = self._eng.nearest_city(lat, lng, n=1)
        if not nearest:
            return {"state_code": None, "city_code": None, "city_name": None}
        c = nearest[0]
        return {
            "state_code":  c.get("state"),
            "city_code":   c.get("code"),
            "city_name":   c.get("name"),
            "distance_km": c.get("distance_km"),
        }


_singleton: Optional[LocationResolver] = None


def get_location_resolver() -> LocationResolver:
    global _singleton
    if _singleton is None:
        _singleton = LocationResolver()
    return _singleton
