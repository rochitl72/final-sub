#!/usr/bin/env python3
"""
catalogs.py — Pre-built chip catalogs for the UI
=================================================
All chip lists are computed **once** from the in-memory graph at process
start so the catalog endpoints can answer in <1 ms without any traversal.

Exposed:
- states()                → 36 entries [{code, name}]
- cities(state_code)      → cities for that state, sorted by population
- vehicle_segments()      → 5 product-friendly vehicle buckets
- road_buckets()          → 3 buckets with example labels
- violation_categories()  → 18 taxonomy groups (id, label, count)
"""

from __future__ import annotations

from typing import List, Optional

from graph_engine import GraphEngine, SEGMENT_LABEL, get_graph_engine

# Display labels for road buckets (mirrors `dialog_manager`'s slot questions).
_ROAD_BUCKET_LABELS = [
    ("highway",   "Highway / Expressway",     "NH, SH, expressway, toll road"),
    ("main_road", "Main / District Road",     "Arterial city road, MDR, ODR"),
    ("street",    "City Street / Residential","Lanes, school / hospital zones"),
]

# Product-friendly vehicle segments (in the order the UI should show them).
_VEHICLE_SEGMENTS_ORDER = [
    "two_wheeler",
    "four_wheeler",
    "three_wheeler",
    "four_wheeler_plus",
    "heavy_vehicle",
]

# Friendly labels for the 18 violation taxonomy groups (auto-derived if missing).
_GROUP_LABELS = {
    "speeding":                    "Speeding",
    "safety_gear":                 "Helmet / Seatbelt / Child seat",
    "signal_and_signage":          "Signals & Signage",
    "documents":                   "DL / RC / Insurance / PUC",
    "impaired_driving":            "Drunk / Drug driving",
    "distracted_driving":          "Mobile phone / Distracted",
    "dangerous_driving":           "Rash / Dangerous driving",
    "lane_and_direction":          "Lane / Direction",
    "parking_and_stopping":        "Parking / Stopping",
    "pedestrian_and_vulnerable":   "Pedestrian / Vulnerable users",
    "overloading_and_capacity":    "Overloading / Capacity",
    "modifications_and_compliance":"Modifications / Compliance",
    "emission_and_noise":          "Emission / Noise",
    "commercial_and_permits":      "Commercial / Permits",
    "accident_and_emergency":      "Accident / Emergency",
    "highway_specific":            "Highway-specific",
    "ev_specific":                 "EV-specific",
    "juvenile_and_guardian":       "Juvenile / Guardian",
}


def _humanise(slug: str) -> str:
    return slug.replace("_", " ").title()


class Catalogs:
    """All catalog lists are precomputed in __init__ and stored on self."""

    def __init__(self, engine: Optional[GraphEngine] = None) -> None:
        self._eng = engine or get_graph_engine()
        self._states  = self._build_states()
        self._cities  = self._build_cities_by_state()
        self._vehicles = self._build_vehicle_segments()
        self._roads   = self._build_road_buckets()
        self._vcats   = self._build_violation_categories()

    # ── Builders ─────────────────────────────────────────────────────────────

    def _build_states(self) -> List[dict]:
        rows = []
        for code, node in self._eng.states_by_code().items():
            rows.append({
                "code":    code,
                "name":    node.get("name", code),
                "capital": node.get("capital"),
                "kind":    node.get("kind", "state"),
            })
        rows.sort(key=lambda r: r["name"])
        return rows

    def _build_cities_by_state(self) -> dict:
        by_state: dict = {}
        for code, node in self._eng.cities_by_code().items():
            st = node.get("state_code")
            if not st:
                continue
            by_state.setdefault(st, []).append({
                "code":           code,
                "name":           node.get("name", code),
                "state_code":     st,
                "population_est": node.get("population_est") or 0,
            })
        for st, lst in by_state.items():
            lst.sort(key=lambda c: -(c["population_est"]))
            for c in lst:
                # Don't leak the population number to the wire — used only for sorting.
                c.pop("population_est", None)
        return by_state

    def _build_vehicle_segments(self) -> List[dict]:
        return [
            {"id": seg, "label": SEGMENT_LABEL.get(seg, _humanise(seg))}
            for seg in _VEHICLE_SEGMENTS_ORDER
        ]

    def _build_road_buckets(self) -> List[dict]:
        return [
            {"id": bid, "label": label, "hint": hint}
            for bid, label, hint in _ROAD_BUCKET_LABELS
        ]

    def _build_violation_categories(self) -> List[dict]:
        groups = self._eng.indexes.get("vio_by_group", {})
        out = []
        for gid, nids in groups.items():
            out.append({
                "id":    gid,
                "label": _GROUP_LABELS.get(gid, _humanise(gid)),
                "count": len(nids),
            })
        out.sort(key=lambda g: g["label"])
        return out

    # ── Public surface ───────────────────────────────────────────────────────

    def states(self) -> List[dict]:
        return self._states

    def cities(self, state_code: str) -> List[dict]:
        return self._cities.get(state_code, [])

    def vehicle_segments(self) -> List[dict]:
        return self._vehicles

    def road_buckets(self) -> List[dict]:
        return self._roads

    def violation_categories(self) -> List[dict]:
        return self._vcats


_singleton: Optional[Catalogs] = None


def get_catalogs() -> Catalogs:
    global _singleton
    if _singleton is None:
        _singleton = Catalogs()
    return _singleton
