"""
Deterministic legal reasoning over a Scenario.

    candidates (from extraction + fact triggers)
      → required-fact checks (applies / conditional / excluded, vehicle alternatives)
      → liability: roles → people (driver, owner, guardian, conductor, …)
      → s.199A: a juvenile's offences are charged to the guardian / owner
      → relations: implies · aggravates · subsumes
      → fines from the city → state → central schedule
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from .kb import SEVERITY_RANK, get_kb

_MINOR_KIN = {"son", "daughter", "kid", "child", "boy", "girl", "teenager", "grandson", "granddaughter",
              "nephew", "niece"}
_DRIVE_ROLES = {"driver", "rider"}
# People whose licence the owner can't take for granted (household members can be assumed licensed).
_OUTSIDERS = {"friend", "cousin", "colleague", "neighbour", "neighbor", "employee", "driver", "roommate", "other"}
_SEG_DEFAULT_USE = {"heavy_vehicle": "commercial_goods", "four_wheeler_plus": "commercial_passenger",
                    "three_wheeler": "commercial_passenger"}
_FOUR_WHEEL_HINT = {"SAFETY_NO_SEATBELT_DRIVER", "SAFETY_NO_SEATBELT_PASSENGER", "SAFETY_NO_CHILD_RESTRAINT",
                    "MOD_TINTED_GLASS", "SAFETY_NO_AIRBAG"}


def _cmp(value, op: str, target) -> Optional[bool]:
    if value is None:
        return None
    try:
        if op == "=":
            return value == target
        if op == "!=":
            return value != target
        if op == "<":
            return value < target
        if op == "<=":
            return value <= target
        if op == ">":
            return value > target
        if op == ">=":
            return value >= target
        if op == "in":
            return value in target
        if op == "not_in":
            return value not in target
    except TypeError:
        return None
    return None


class Reasoner:
    def __init__(self, scenario: dict, *, state_code: Optional[str], city_code: Optional[str]):
        self.kb = get_kb()
        self.scn = copy.deepcopy(scenario)
        self.state_code, self.city_code = state_code, city_code
        self.actors: Dict[str, dict] = {a["id"]: a for a in self.scn.get("actors", [])}
        self.vehicles: Dict[str, dict] = {v["id"]: v for v in self.scn.get("vehicles", [])}
        self.facts: dict = self.scn.setdefault("facts", {})
        self.assumptions: List[str] = []
        self.findings: List[dict] = []
        self.unknowns: List[dict] = []

    # ── helpers ───────────────────────────────────────────────────────────
    def _new_actor(self, label: str, role: str, relation: str = "other", placeholder: bool = True) -> dict:
        key = f"placeholder:{role}"
        for a in self.actors.values():
            if a.get("ref") == key:
                return a
        aid = f"P{len(self.actors) + 1}"
        a = {"id": aid, "label": label, "relation": relation, "roles": [role], "age": None, "licence": None,
             "is_victim": False, "vehicle": None, "count": 1, "ref": key, "placeholder": placeholder}
        self.actors[aid] = a
        self.scn["actors"].append(a)
        return a

    def _new_vehicle(self, segment: Optional[str], owner: Optional[str]) -> dict:
        vid = f"V{len(self.vehicles) + 1}"
        while vid in self.vehicles:
            vid += "x"
        v = {"id": vid, "segment": segment, "use": _SEG_DEFAULT_USE.get(segment), "owner": owner,
             "occupants": None, "label": ""}
        self.vehicles[vid] = v
        self.scn.setdefault("vehicles", []).append(v)
        return v

    def _self(self) -> Optional[dict]:
        return next((a for a in self.actors.values() if a.get("relation") == "self"), None)

    # ── 1. normalise the scenario ─────────────────────────────────────────
    def normalise(self) -> None:
        kb = self.kb
        events = self.scn.setdefault("events", [])
        for e in events:
            e["offences"] = [c for c in e.get("offences", []) if kb.exists(c)]
            a = self.actors.get(e.get("actor"))
            if a and not e.get("vehicle"):
                e["vehicle"] = a.get("vehicle")
        # infer roles from what people did
        for e in events:
            a = self.actors.get(e["actor"])
            if not a:
                continue
            if not a.get("roles"):
                ride_along = {"SAFETY_NO_SEATBELT_PASSENGER", "SAFETY_NO_HELMET_PILLION", "SAFETY_NO_CHILD_RESTRAINT",
                              "SAFETY_NO_CHILD_2W"}
                if all(c in ride_along for c in e["offences"]) or (a.get("age") is not None and a["age"] < 10):
                    a["roles"] = ["pillion" if any("HELMET" in c or "2W" in c for c in e["offences"]) else "passenger"]
                    e["offences"] = ["SAFETY_NO_SEATBELT_PASSENGER" if c == "SAFETY_NO_SEATBELT_DRIVER" else c
                                     for c in e["offences"]]
                elif any(kb.violation(c).get("grp") not in ("commercial_and_permits",) for c in e["offences"]):
                    a["roles"] = ["driver"]
        # passengers / pillions / conductors share a vehicle with a driver
        drivers = [a for a in self.actors.values() if set(a.get("roles", [])) & _DRIVE_ROLES and not a.get("is_victim")]
        for a in self.actors.values():
            if not a.get("vehicle") and set(a.get("roles", [])) & {"passenger", "pillion", "conductor"}:
                host = next((d for d in drivers if d.get("vehicle")), None) or (drivers[0] if drivers else None)
                if host:
                    if not host.get("vehicle"):
                        host["vehicle"] = self._new_vehicle(None, host["id"] if host.get("relation") == "self" else None)["id"]
                    a["vehicle"] = host["vehicle"]
        # every driver gets a vehicle; segment inferred from the offences
        for a in self.actors.values():
            if set(a.get("roles", [])) & _DRIVE_ROLES and not a.get("vehicle"):
                a["vehicle"] = self._new_vehicle(None, a["id"] if a.get("relation") == "self" else None)["id"]
        for e in events:
            if not e.get("vehicle"):
                a = self.actors.get(e["actor"])
                e["vehicle"] = a.get("vehicle") if a else None
        import nlu
        for v in self.vehicles.values():
            if v.get("segment"):
                continue
            codes = [c for e in events if e.get("vehicle") == v["id"] for c in e["offences"]]
            seg = next((nlu.vehicle_from_violation(c) for c in codes if nlu.vehicle_from_violation(c)), None)
            if not seg and any(c in _FOUR_WHEEL_HINT for c in codes):
                seg = "four_wheeler"
            if not seg and any(c.startswith("OVERLOAD_GOODS") for c in codes):
                seg = "heavy_vehicle"
            if seg:
                v["segment"] = seg
                if not v.get("use"):
                    v["use"] = _SEG_DEFAULT_USE.get(seg)
        # plain "overspeeding" with no vehicle named: the LMV rate (car / two-wheeler) applies
        for e in events:
            v = self.vehicles.get(e.get("vehicle"))
            if v and not v.get("segment") and "SPEED_OVER_LMV" in e["offences"]:
                v["segment"] = "four_wheeler"
                self.assumptions.append("vehicle not stated — the car / two-wheeler speeding rate is shown")
        # a child passenger without a belt is a child-restraint offence (s.194B(2))
        for e in events:
            a = self.actors.get(e["actor"]) or {}
            if a.get("age") is not None and a["age"] < 14 and "SAFETY_NO_SEATBELT_PASSENGER" in e["offences"]:
                e["offences"] = ["SAFETY_NO_CHILD_RESTRAINT" if c == "SAFETY_NO_SEATBELT_PASSENGER" else c for c in e["offences"]]
        # a helmet charge on someone who wasn't riding belongs to the pillion offence
        for e in events:
            a = self.actors.get(e["actor"]) or {}
            if "SAFETY_NO_HELMET_RIDER" in e["offences"] and "rider" not in a.get("roles", []):
                riders = [x for x in self.actors.values() if "rider" in x.get("roles", []) and x is not a
                          and (not a.get("vehicle") or x.get("vehicle") == a.get("vehicle"))]
                if riders:
                    e["offences"] = ["SAFETY_NO_HELMET_PILLION" if c == "SAFETY_NO_HELMET_RIDER" else c for c in e["offences"]]
                    a["roles"] = ["pillion"]
                    if not a.get("vehicle"):
                        a["vehicle"] = riders[0].get("vehicle")
        for v in self.vehicles.values():
            if v.get("segment") and not v.get("use"):
                v["use"] = _SEG_DEFAULT_USE.get(v["segment"], "private")
            # a stated owner ("the owner knew") owns the commercial vehicle
            if not v.get("owner"):
                owners = [a for a in self.actors.values() if "owner" in a.get("roles", []) and not a.get("vehicle")]
                if owners and len([x for x in self.vehicles.values() if not x.get("owner")]) == 1:
                    v["owner"] = owners[0]["id"]
                    owners[0]["vehicle"] = v["id"]
        # a pillion / passenger rides on a vehicle whose driver may be "you"
        for a in self.actors.values():
            if set(a.get("roles", [])) & _DRIVE_ROLES and a.get("vehicle"):
                v = self.vehicles.get(a["vehicle"])
                if v and v.get("segment") == "two_wheeler" and "driver" in a["roles"]:
                    a["roles"] = ["rider" if r == "driver" else r for r in a["roles"]]

    # ── 2. fact-driven candidates ─────────────────────────────────────────
    def triggers(self) -> None:
        f = self.facts
        events = self.scn["events"]
        have = {(e["actor"], c) for e in events for c in e["offences"]}

        def add(actor_id: str, code: str, why: str) -> None:
            if (actor_id, code) in have or not self.kb.exists(code):
                return
            a = self.actors.get(actor_id)
            events.append({"id": f"T{len(events) + 1}", "actor": actor_id, "vehicle": a.get("vehicle") if a else None,
                           "offences": [code], "text": why, "derived": True})
            have.add((actor_id, code))

        for a in list(self.actors.values()):
            if not (set(a.get("roles", [])) & _DRIVE_ROLES) or a.get("is_victim"):
                continue          # a victim isn't charged for how they were riding the vehicle that was hit
            if (self.vehicles.get(a.get("vehicle")) or {}).get("segment") in ("bicycle", "cycle", "non_motor"):
                continue
            age = a.get("age")
            if age is not None and age < 18:
                add(a["id"], "DOC_UNDERAGE", "driver under 18")
            elif age is None and a.get("relation") in _MINOR_KIN:
                add(a["id"], "DOC_UNDERAGE", "driver may be under 18")
            if a.get("licence") in ("none", "expired", "learner"):
                add(a["id"], "DOC_NO_DL", "no valid licence")
            v = self.vehicles.get(a.get("vehicle"))
            if v and v.get("segment") == "two_wheeler" and (v.get("occupants") or 0) >= 3:
                add(a["id"], "SAFETY_MORE_THAN_2_ON_2W", f"{v['occupants']} people on a two-wheeler")
        # someone else's vehicle, licence not stated → s.180 for the owner hinges on it
        for a in list(self.actors.values()):
            v = self.vehicles.get(a.get("vehicle")) or {}
            if set(a.get("roles", [])) & _DRIVE_ROLES and not a.get("is_victim") and a.get("licence") is None \
                    and v.get("owner") and v["owner"] != a["id"] and (a.get("age") is None or a["age"] >= 18) \
                    and a.get("relation") not in _MINOR_KIN \
                    and ("liability" in (self.scn.get("asks") or []) or self.facts.get("owner_permitted")):
                add(a["id"], "DOC_NO_DL", "licence not stated")
        # took the vehicle without the owner's permission (s.197)
        if f.get("owner_permitted") is False:
            for a in list(self.actors.values()):
                v = self.vehicles.get(a.get("vehicle")) or {}
                if set(a.get("roles", [])) & _DRIVE_ROLES and v.get("owner") and v["owner"] != a["id"]:
                    add(a["id"], "DOC_TAKING_VEHICLE_WITHOUT_AUTH", "took the vehicle without permission")
        agent = self._collision_agent()
        if agent in self.actors and f.get("fled"):
            add(agent, "ACC_HIT_AND_RUN", "left the scene after the collision")
        # Someone was hit and we know who did it, but no specific traffic offence
        # explains the crash ("a guy on a scooter hit my mom"). Negligence causing
        # hurt / death still has to be considered — whether it applies depends on
        # the outcome, so the planner will ask "Was anyone hurt?".
        if agent in self.actors and any(a.get("is_victim") for a in self.actors.values()):
            aggravators = {r["from_code"] for r in self.kb.relations
                           if r["type"] == "aggravates" and r["to_code"] in ("CRIM_HURT_RASH_NEGLIGENT", "ACC_CAUSING_DEATH")}
            agent_codes = {c for e in events if e["actor"] == agent for c in e["offences"]}
            if not (agent_codes & aggravators):
                for code in ("CRIM_HURT_RASH_NEGLIGENT", "ACC_CAUSING_DEATH"):
                    if (agent, code) in have or not self.kb.exists(code):
                        continue
                    a = self.actors.get(agent)
                    events.append({"id": f"T{len(events) + 1}", "actor": agent,
                                   "vehicle": a.get("vehicle") if a else None, "offences": [code],
                                   "text": "hit someone (negligence is for the court to decide)",
                                   "derived": True, "may": True})
                    have.add((agent, code))
        speeders = [e["actor"] for e in events if any(c.startswith("SPEED_") for c in e["offences"])]
        if (f.get("speed_over_pct") or 0) >= 50:
            for s in speeders or [x["id"] for x in self.actors.values() if x.get("relation") == "self"]:
                add(s, "SPEED_EXCESSIVE_50PLUS", f"{f['speed_over_pct']}% over the limit")

    def _collision_agent(self) -> Optional[str]:
        """Who caused the collision: a non-victim who was driving/riding and wasn't stationary."""
        f = self.facts
        if not f.get("collision"):
            return None
        events = self.scn["events"]
        parked = {e["actor"] for e in events if any(c.startswith("PARK_") for c in e["offences"])}

        def ok(aid):
            a = self.actors.get(aid)
            return bool(a) and not a.get("is_victim") and aid not in parked \
                and bool(set(a.get("roles", [])) & _DRIVE_ROLES)
        agent = f.get("collision_agent")
        if ok(agent):
            return agent
        cands = [a["id"] for a in self.actors.values() if ok(a["id"])]
        if len(cands) == 1:
            return cands[0]
        me = next((a["id"] for a in self.actors.values() if a.get("relation") == "self"), None)
        if me and not self.actors[me].get("is_victim") and (agent in parked or not cands):
            return me                      # drove into a parked vehicle / only "you" can have caused it
        return None

    # ── 3. evaluate conditions ────────────────────────────────────────────
    def fact_value(self, name: str, actor: Optional[dict], vehicle: Optional[dict]):
        scope, _, key = name.partition(".")
        if scope == "actor":
            val = (actor or {}).get(key)
            if key == "age" and val is None and actor and actor.get("relation") not in _MINOR_KIN \
                    and not actor.get("placeholder"):
                return 18          # adults unless the story says otherwise (only kin can be minors)
            return val
        if scope == "vehicle":
            return (vehicle or {}).get(key)
        if scope == "event":
            return self.facts.get(key)
        if name == "owner.permitted":
            return self.facts.get("owner_permitted")
        return None

    def check(self, code: str, actor: Optional[dict], vehicle: Optional[dict]) -> Tuple[str, List[str]]:
        unknown, failed = [], []
        for c in self.kb.conditions.get(code, []):
            if c["kind"] != "required":
                continue
            ok = _cmp(self.fact_value(c["fact"], actor, vehicle), c["op"], c["value"])
            if ok is None:
                unknown.append(c["fact"])
            elif not ok:
                failed.append(c["fact"])
        if failed:
            return "excluded", failed
        if unknown:
            return "conditional", unknown
        return "applies", []

    # ── 4. liability ──────────────────────────────────────────────────────
    def _driver_of(self, vid: Optional[str], doer: Optional[dict]) -> Optional[dict]:
        if doer and set(doer.get("roles", [])) & _DRIVE_ROLES:
            return doer
        for a in self.actors.values():
            if a.get("vehicle") == vid and set(a.get("roles", [])) & _DRIVE_ROLES:
                return a
        return doer

    def _resolve_role(self, role: str, doer: Optional[dict], vehicle: Optional[dict]) -> Tuple[Optional[dict], bool]:
        """(actor, explicit) — explicit=False for placeholders we had to create."""
        vid = (vehicle or {}).get("id")
        if role in _DRIVE_ROLES:
            d = self._driver_of(vid, doer)
            return d, True
        if role in ("pillion", "passenger"):
            if doer and role in doer.get("roles", []):
                return doer, True
            return None, False
        if role == "owner":
            oid = (vehicle or {}).get("owner")
            if oid and oid in self.actors:
                own = self.actors[oid]
                if own.get("age") is not None and own["age"] < 18:
                    # a minor's vehicle is registered to / kept by the parent
                    return self._resolve_role("guardian", own, vehicle)
                return own, True
            named = next((a for a in self.actors.values() if "owner" in a.get("roles", [])), None)
            if named:
                return named, True
            return None, False
        if role == "guardian":
            if doer and (doer.get("age") or 99) < 18 or (doer and doer.get("relation") in _MINOR_KIN):
                me = self._self()
                if doer.get("relation") in _MINOR_KIN and me:
                    return me, True
                g = next((a for a in self.actors.values() if "guardian" in a.get("roles", [])
                          or a.get("relation") in ("parent", "father", "dad", "mother", "mom")), None)
                if g:
                    return g, True
                return self._new_actor("The parent / guardian", "guardian"), False
            return None, False
        if role == "conductor":
            c = next((a for a in self.actors.values() if "conductor" in a.get("roles", [])), None)
            if c:
                return c, True
            if (vehicle or {}).get("segment") == "four_wheeler_plus":
                return self._new_actor("The conductor", "conductor"), False
            return None, False
        if role in ("operator", "employer"):
            o = next((a for a in self.actors.values() if role in a.get("roles", [])), None)
            if o:
                return o, True
            return self._new_actor("The operator / employer", role), False
        if role in ("any", "pedestrian"):
            return doer, True
        return doer, True

    def _finding(self, code: str, target: dict, event: dict, *, status: str, certainty: str,
                 reason: Optional[str] = None, missing: Optional[List[str]] = None, deemed: bool = False,
                 role: Optional[str] = None) -> dict:
        return {"code": code, "actor": target["id"], "event": event.get("id"), "doer": event.get("actor"),
                "vehicle": event.get("vehicle"), "status": status, "certainty": certainty, "reason": reason,
                "missing": missing or [], "deemed": deemed, "role": role, "event_text": event.get("text")}

    def assign(self) -> None:
        for e in self.scn["events"]:
            doer = self.actors.get(e["actor"])
            vehicle = self.vehicles.get(e.get("vehicle"))
            for code in e["offences"]:
                status, why = self.check(code, doer, vehicle)
                if status == "excluded" and any(w.startswith("vehicle.") for w in why):
                    for alt in self.kb.alternatives.get(code, []):
                        s2, w2 = self.check(alt, doer, vehicle)
                        if s2 != "excluded":
                            code, status, why = alt, s2, w2
                            break
                if status == "excluded":
                    continue
                for r in self.kb.roles(code):
                    target, explicit = self._resolve_role(r["role"], doer, vehicle)
                    if not target:
                        continue
                    certainty = r.get("certainty") or "liable"
                    if certainty == "may_be_liable" and not explicit:
                        continue
                    if e.get("may"):
                        certainty = "may_be_liable"
                    if certainty == "may_be_liable" and target.get("relation") == "self" and r["role"] != "owner":
                        pass
                    self.findings.append(self._finding(
                        code, target, e, status=status, certainty=certainty, missing=why if status == "conditional" else [],
                        reason=r.get("note"), role=r["role"]))

    # ── 5. s.199A: juvenile's offences → guardian + owner ─────────────────
    def juvenile_rule(self) -> None:
        extra = []
        for f in self.findings:
            a = self.actors.get(f["actor"])
            if not a or f["code"] in ("DOC_UNDERAGE", "JUV_MINOR_DRIVING") or f.get("deemed"):
                continue
            age = a.get("age")
            if age is None or age >= 18 or f["role"] not in _DRIVE_ROLES:
                continue
            f["juvenile"] = True
            if (f.get("section") or self.kb.violation(f["code"]).get("mv_section") or "").upper().startswith("BNS"):
                continue      # s.199A covers MV Act offences; criminal law goes to the Juvenile Justice Board
            if f["code"] == "DOC_TAKING_VEHICLE_WITHOUT_AUTH":
                continue          # the owner is the one wronged here
            vehicle = self.vehicles.get(f["vehicle"])
            roles_ = ("guardian",) if self.facts.get("owner_permitted") is False else ("guardian", "owner")
            for role in roles_:
                tgt, _ = self._resolve_role(role, a, vehicle)
                if tgt and tgt["id"] != a["id"]:
                    extra.append({**f, "actor": tgt["id"], "deemed": True, "juvenile": False, "role": role,
                                  "reason": "Deemed guilty for an offence by a juvenile (s.199A)."})
        best: Dict[Tuple[str, str], dict] = {}
        for f in self.findings + extra:
            k = (f["actor"], f["code"])
            if k not in best or (f["status"] == "applies" and best[k]["status"] != "applies"):
                best[k] = f
        self.findings = list(best.values())

    # ── 6. relations ──────────────────────────────────────────────────────
    def relations(self) -> None:
        added = []
        for f in list(self.findings):
            if f.get("deemed"):
                continue
            for r in self.kb.rel_from.get(f["code"], []):
                if r["type"] not in ("implies", "aggravates"):
                    continue
                doer = self.actors.get(f["doer"])
                vehicle = self.vehicles.get(f["vehicle"])
                cond = r.get("condition")
                ok = True
                missing: List[str] = []
                if cond:
                    ok = _cmp(self.fact_value(cond["fact"], doer, vehicle), cond["op"], cond["value"])
                    if ok is False:
                        continue
                    if ok is None:
                        if r["type"] == "aggravates" and not self.facts.get("collision"):
                            continue
                        missing = [cond["fact"]]
                to = r["to_code"]
                status, why = self.check(to, doer, vehicle)
                if status == "excluded":
                    continue
                if f["status"] == "conditional":
                    missing = missing + [m for m in f.get("missing", []) if m not in missing]
                status = "conditional" if (missing or status == "conditional") else "applies"
                missing = missing + [w for w in why if w not in missing]
                if r["type"] == "aggravates":
                    target = self._driver_of(f["vehicle"], doer) or doer
                    if not target or target["id"] != f["actor"]:
                        continue
                    added.append(self._finding(to, target, {"id": f["event"], "actor": f["doer"],
                                                            "vehicle": f["vehicle"], "text": f["event_text"]},
                                               status=status, certainty="may_be_liable", missing=missing,
                                               reason=r.get("note"), role="driver"))
                else:
                    target, explicit = self._resolve_role(r.get("target_role") or "driver", doer, vehicle)
                    if not target or target["id"] == f["actor"] and r.get("target_role") == "owner":
                        if not target:
                            continue
                        # the driver is also the owner — nothing extra to charge
                        continue
                    added.append(self._finding(to, target, {"id": f["event"], "actor": f["doer"],
                                                            "vehicle": f["vehicle"], "text": f["event_text"]},
                                               status=status, certainty="liable" if explicit else "may_be_liable",
                                               missing=missing, reason=r.get("note"), role=r.get("target_role")))
        self.findings.extend(added)
        # subsumes: the broader charge replaces the narrower for the same person
        subs = [(r["from_code"], r["to_code"]) for r in self.kb.relations if r["type"] == "subsumes"]
        by_actor: Dict[str, set] = {}
        for f in self.findings:
            if f["status"] == "applies":
                by_actor.setdefault(f["actor"], set()).add(f["code"])
        keep = []
        for f in self.findings:
            codes = by_actor.get(f["actor"], set())
            if any(frm in codes and to == f["code"] for frm, to in subs):
                continue
            keep.append(f)
        # dedupe (actor, code): prefer applies over conditional, liable over may
        best: Dict[Tuple[str, str], dict] = {}
        rank = lambda x: (x["status"] == "applies", x["certainty"] == "liable", not x.get("deemed"))
        for f in keep:
            k = (f["actor"], f["code"])
            if k not in best or rank(f) > rank(best[k]):
                best[k] = f
        self.findings = list(best.values())

    # ── 7. fines ──────────────────────────────────────────────────────────
    def price(self) -> None:
        eng = self.kb.eng
        for f in self.findings:
            v = self.vehicles.get(f["vehicle"]) or {}
            card = eng.quick_fine(f["code"], self.state_code, self.city_code, self.kb.fine_class(v.get("segment")))
            vio = self.kb.violation(f["code"])
            a = self.actors.get(f["actor"]) or {}
            f["name"] = vio.get("name", f["code"])
            f["section"] = vio.get("mv_section")
            f["severity"] = self.kb.sev(f["code"])
            f["licence_action"] = vio.get("licence_action")
            f["count"] = a.get("count", 1) or 1
            f["card"] = card
            if card:
                f["fine_first"] = card.get("fine_first")
                f["fine_repeat"] = card.get("fine_repeat")
                f["imprisonment"] = card.get("imprisonment")
                f["compoundable"] = card.get("compoundable")
                f["fine_source"] = card.get("fine_source")
            else:
                f.update({"fine_first": None, "fine_repeat": None, "imprisonment": None,
                          "compoundable": vio.get("compoundable"), "fine_source": None})
            f["provisions"] = [p["ref"] for p in self.kb.vprov.get(f["code"], [])]

    # ── 8. assemble ───────────────────────────────────────────────────────
    # Damage from the crash ("my tail light is broken") is not an equipment offence.
    _DAMAGE_RE = re.compile(r"\b(broke|broken|damaged?|smashed|cracked|dented|bent|shattered|scratched)\b", re.I)
    _EQUIPMENT_GROUPS = {"modifications_and_compliance", "emission_and_noise"}

    def drop_crash_damage(self) -> None:
        if not self.facts.get("collision"):
            return
        keep = []
        for e in self.scn["events"]:
            if not e.get("derived") and self._DAMAGE_RE.search(e.get("text") or ""):
                e["offences"] = [c for c in e["offences"]
                                 if (self.kb.violation(c) or {}).get("grp") not in self._EQUIPMENT_GROUPS]
                if not e["offences"]:
                    continue
            keep.append(e)
        self.scn["events"] = keep

    def run(self) -> dict:
        self.normalise()
        self.drop_crash_damage()
        self.triggers()
        self.assign()
        self.relations()
        self.juvenile_rule()
        self.price()

        persons: Dict[str, dict] = {}
        conditional = []
        for f in self.findings:
            if f["status"] == "conditional":
                conditional.append(f)
                continue
            a = self.actors[f["actor"]]
            p = persons.setdefault(a["id"], {"actor": a, "findings": []})
            p["findings"].append(f)
        order = []
        for aid, p in persons.items():
            a = p["actor"]
            fs = sorted(p["findings"], key=lambda f: (-SEVERITY_RANK.get(f["severity"]), -(f["fine_first"] or 0)))
            p["findings"] = fs
            p["total_first"] = sum((f["fine_first"] or 0) * f["count"] for f in fs
                                   if f["certainty"] == "liable" and not f.get("juvenile"))
            p["is_victim"] = bool(a.get("is_victim"))
            order.append(p)
        order.sort(key=lambda p: (p["is_victim"], p["actor"].get("relation") != "self",
                                  -max([SEVERITY_RANK.get(f["severity"]) for f in p["findings"]] or [0]),
                                  -p["total_first"]))
        for v in self.vehicles.values():
            if v.get("segment") is None and any(f["vehicle"] == v["id"] for f in self.findings):
                self.assumptions.append("vehicle type not stated — standard (non-vehicle-specific) fine shown")
                break
        if not any(f["role"] == "owner" for f in self.findings):
            for f in self.findings:
                code_roles = {r["role"] for r in self.kb.roles(f["code"]) if r.get("certainty") == "liable"}
                v = self.vehicles.get(f["vehicle"]) or {}
                if "owner" in code_roles and not v.get("owner") and f["status"] == "applies":
                    self.assumptions.append("the driver is taken to be the vehicle's owner")
                    break
        return {"persons": order, "conditional": conditional, "actors": self.actors, "vehicles": self.vehicles,
                "facts": self.facts, "assumptions": list(dict.fromkeys(self.assumptions)),
                "scenario": self.scn}


def analyse(scenario: dict, *, state_code: Optional[str], city_code: Optional[str]) -> dict:
    return Reasoner(scenario, state_code=state_code, city_code=city_code).run()


def signature(result: dict) -> List[Tuple[str, str, Any]]:
    """Comparable summary: who is charged with what, at what first-offence fine."""
    sig = []
    for p in result["persons"]:
        for f in p["findings"]:
            sig.append((p["actor"]["id"], f["code"], f["certainty"], f.get("fine_first")))
    return sorted(sig, key=str)
