#!/usr/bin/env python3
"""
DriveLegal resolution layer.

The only component permitted to answer a public question. It reads the SERVING
graph exclusively — the quarantine file is a review queue and is never loaded
here, so an unsourced value cannot reach a user even by mistake.

Every answer names the jurisdiction level that supplied it and the instrument it
rests on. An answer with no citable basis is a refusal, not a number.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
SERVING = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"


class Answer(dict):
    @property
    def ok(self):
        return self.get("status") == "answer"

    def __str__(self):
        if not self.ok:
            return f"[{self['status']}] {self['message']}"
        c = self.get("citation") or "no citation"
        return (f"₹{self['amount_inr']} — {self['level']} level ({self['level_name']}) "
                f"— {c}")


class DriveLegal:
    def __init__(self, path=SERVING):
        g = json.loads(Path(path).read_text())
        assert g["meta"].get("tier") == "serving", \
            "resolver refuses to load a non-serving graph"
        self.g = g
        self.nodes = g["nodes"]
        self.policy = g["meta"]["resolution_policy"]
        self.fines = [n for n in self.nodes.values() if n.get("type") == "fine"]
        self.vio = {n["code"]: n for n in self.nodes.values()
                    if n.get("type") == "violation"}
        self.states = {n["code"]: n for n in self.nodes.values()
                       if n.get("type") == "state"}
        self.cities = {n["code"]: n for n in self.nodes.values()
                       if n.get("type") == "city"}

    # ------------------------------------------------------------------ fines
    def fine(self, violation_code, city=None, state=None, vehicle_class=None):
        """Cascade city -> state -> central, per the declared policy."""
        pol = self.policy["field_classes"]["fine"]
        v = self.vio.get(violation_code)
        if not v:
            return Answer(status="unknown_violation",
                          message=f"No violation {violation_code!r} in the serving graph.")

        def pick(level):
            out = []
            for f in self.fines:
                if f.get("violation_code") != violation_code:
                    continue
                if level == "city" and f.get("city_code") != city:
                    continue
                if level == "state" and (f.get("city_code") or f.get("state_code") != state):
                    continue
                if level == "central" and f.get("scope") != "central":
                    continue
                if vehicle_class and f.get("vehicle_class") not in (None, vehicle_class):
                    continue
                if f.get("fine_first", {}).get("resolved"):
                    out.append(f)
            return out

        trail = []
        for level in pol["precedence"] if False else ["city", "state", "central"]:
            if level == "city" and not city:
                trail.append("city: not supplied")
                continue
            if level == "state" and not state:
                trail.append("state: not supplied")
                continue
            hits = pick(level)
            if not hits:
                trail.append(f"{level}: no verified row")
                continue
            f = hits[0]
            m = f["fine_first"]
            ver = (f.get("_verification", {}).get("fields", {})
                   .get("first_offence", {}))
            name = {"city": self.cities.get(city, {}).get("name", city),
                    "state": self.states.get(state, {}).get("name", state),
                    "central": "India (Motor Vehicles Act)"}[level]
            note = None
            if level == "central" and state:
                note = (f"{self.states.get(state, {}).get('name', state)} has no verified "
                        f"notification of a different amount, so the figure fixed by the "
                        f"Motor Vehicles Act applies.")
            return Answer(
                status="answer", amount_inr=m["amount_inr"], max_inr=m.get("max_inr"),
                basis=m["basis"], per_unit=m.get("per_unit"),
                level=level, level_name=name, violation=v.get("name"),
                citation=f"MV Act s.{v.get('mv_section')}" if v.get("mv_section") else None,
                source_url=ver.get("source_url"), retrieved_at=ver.get("retrieved_at"),
                fallback_note=note, trail=trail,
            )

        return Answer(
            status="no_verified_amount", violation=v.get("name"), trail=trail,
            message=(f"I don't have a verified penalty amount for "
                     f"'{v.get('name')}'{' in ' + state if state else ''}. "
                     f"Check your challan on the official e-challan portal."),
            withheld=v.get("_withheld_count", 0),
        )

    # ----------------------------------------------------------- speed limits
    def speed_limit(self, road_class_code, category="M1_passenger_car"):
        """Safety-critical: never cascades. Absence defers to the posted sign."""
        rc = self.nodes.get(f"road:{road_class_code}")
        if not rc:
            return Answer(status="unknown_road_class",
                          message=f"No road class {road_class_code!r}.")
        lim = (rc.get("speed_limits") or {}).get(category)
        if lim is None:
            return Answer(
                status="defer_to_sign",
                message=("I don't have a verified speed limit for this road and vehicle "
                         "type. The limit on the posted sign governs — follow it."))
        ver = (rc.get("_verification", {}).get("fields", {})
               .get(f"speed_limits.{category}", {}))
        return Answer(status="answer", amount_inr=None, limit_kmph=lim,
                      level="road_class", level_name=rc.get("name"),
                      citation=ver.get("source_title"), source_url=ver.get("source_url"),
                      caveat=("This is the national ceiling. A state or local authority "
                              "may have notified a lower limit for this road — the posted "
                              "sign governs."))

    # ---------------------------------------------------------------- summary
    def coverage(self):
        srv = sum(1 for n in self.nodes.values() if n.get("_serveable"))
        return {"nodes": len(self.nodes), "serveable": srv,
                "recognised_only": len(self.nodes) - srv,
                "fine_rows": len(self.fines),
                "fine_rows_answerable": sum(
                    1 for f in self.fines if f.get("fine_first", {}).get("resolved"))}


if __name__ == "__main__":
    dl = DriveLegal()
    print("coverage:", json.dumps(dl.coverage()))
    print("\npolicy precedence:", dl.policy["precedence"])
    print("\n--- worked examples ---")
    for vc, city, state in [
        ("DRUNK_DRIVING", None, "KA"), ("NO_HELMET", None, "MH"),
        ("RED_LIGHT_JUMP", None, "DL"), ("SPEEDING", None, None),
    ]:
        vc2 = vc if vc in dl.vio else next(
            (k for k in dl.vio if vc.split("_")[0] in k), None)
        if vc2:
            print(f"\n{vc2} / state={state}:")
            print("  ", dl.fine(vc2, state=state))
    print("\nspeed limit, expressway M1:", dl.speed_limit("EXP"))
    print("speed limit, expressway N1_LCV:", dl.speed_limit("EXP", "N1_LCV"))
