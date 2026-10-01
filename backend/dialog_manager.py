#!/usr/bin/env python3
"""
dialog_manager.py — Pure-Python slot-filling rule engine
=========================================================
Drives the conversation deterministically: chooses the next missing slot,
asks a structured question with chips, and only falls back to the LLM when
the rule engine genuinely cannot resolve user intent.

The dialog manager is **stateless** at the module level — all state lives
on the per-session dict that `SessionStore` hands it.

Slot order (4 progress dots in the UI):

  1. state_code      (required — auto-set in map mode; chip in chat mode)
  2. road_bucket     (required)
  3. vehicle_segment (required, also derives vehicle_fine_class)
  4. violation_code  (required — chip OR keyword OR free-text → LLM)

Each "ask_slot" turn returns the structured response from plan §3.1:

    {
      "intent":      "ask_slot",
      "slot":        "vehicle_segment",
      "question":    "What vehicle were you on?",
      "chips":       [{"id": "...", "label": "..."}],
      "allow_text":  True
    }

When all slots resolve, returns the answer shape:

    {
      "intent":        "answer",
      "reply":         "...",
      "fine_card":     {...},
      "session_state": {...}
    }

If a violation cannot be resolved either by chip or by keyword, the manager
calls `llm_chatbot.handle_freeform` and re-packages its output as an
`answer` intent.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

from catalogs import get_catalogs
from graph_engine import SEGMENT_FINE_CLASS, SEGMENT_LABEL, get_graph_engine
from location_resolver import get_location_resolver
from violation_resolver import get_violation_resolver

# Maximum free-text length we'll ever process per turn.
MAX_MESSAGE_LEN = 2000


# ─────────────────────────────────────────────────────────────────────────────
# SessionStore — thread-safe in-memory dict keyed by session_id.
# ─────────────────────────────────────────────────────────────────────────────


def _new_session(session_id: str, mode: str = "static") -> dict:
    return {
        "session_id":           session_id,
        # session mode — "static" (calculator) or "dynamic" (chatbot)
        "mode":                 mode,
        # location
        "geo_mode":             None,    # "map" | "chat" | None (= unset)
        "lat":                  None,
        "lng":                  None,
        "state_code":           None,
        "city_code":            None,
        "city_name":            None,
        "road_bucket":          None,
        "confirmed_road":       None,
        "confirmed_road_class": None,
        "road_options":         [],
        # vehicle
        "vehicle_segment":      None,
        "vehicle_fine_class":   None,
        "vehicle_type":         None,
        # violation
        "violation_category":   None,
        "violation_code":       None,
        # driver context (proactive slot extraction)
        "driver_age":           None,    # int or None
        "has_licence":          None,    # bool or None — valid DL present?
        "licence_type":         None,    # e.g. "MCWG", "LMV", "none"
        "repeat_offender":      None,    # bool or None — prior offence mentioned?
        # bookkeeping
        "pending_slot":         None,    # which slot was last asked (for chip_id)
        "stage":                "open",  # "open" | "answered"
        "messages":             [],      # for LLM freeform context
        "last_fine_card":       None,
        # smart-relocation: previous (state/city + fine_card) snapshot — used
        # by `revert_location` and surfaced on every `answer_updated` turn.
        "prev_location":        None,
        # Chatbot narrate phase — once True we never auto-emit the welcome
        # bubble again for this session.
        "narrate_started":      False,
    }


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}

    def create(self, mode: str = "static") -> dict:
        sid = uuid.uuid4().hex
        with self._lock:
            self._data[sid] = _new_session(sid, mode=mode)
            return self._data[sid]

    def create_with_id(self, sid: str, mode: str = "static") -> dict:
        """Create a session under a caller-provided id (used after lazy load)."""
        with self._lock:
            if sid not in self._data:
                self._data[sid] = _new_session(sid, mode=mode)
            return self._data[sid]

    def get(self, sid: str) -> Optional[dict]:
        with self._lock:
            return self._data.get(sid)

    def get_or_create(self, sid: Optional[str], mode: str = "static") -> dict:
        if sid:
            existing = self.get(sid)
            if existing:
                return existing
            with self._lock:
                self._data[sid] = _new_session(sid, mode=mode)
                return self._data[sid]
        return self.create(mode=mode)

    def delete(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)

    def put(self, s: dict) -> None:
        """Insert (or replace) a fully-formed session dict — used by the
        lazy-loader to hydrate an in-memory entry from SQLite."""
        sid = s.get("session_id")
        if not sid:
            return
        with self._lock:
            self._data[sid] = s


# ─────────────────────────────────────────────────────────────────────────────
# DialogManager — the rule engine.
# ─────────────────────────────────────────────────────────────────────────────


class DialogManager:
    def __init__(
        self,
        freeform_fn: Optional[Callable[[dict, str], dict]] = None,
        dynamic_fn:  Optional[Callable[[dict, str], dict]] = None,
    ) -> None:
        self.engine    = get_graph_engine()
        self.catalogs  = get_catalogs()
        self.locator   = get_location_resolver()
        self.violator  = get_violation_resolver()
        # `freeform_fn(session, user_text) -> {"reply", "fine_card"}`.
        # Injected by api.py (so dialog_manager has no hard dependency on Ollama).
        self.freeform_fn = freeform_fn
        # `dynamic_fn(session, user_text) -> turn-shaped dict`.
        # Optional; injected by api.py when the dynamic_chatbot module is wired.
        self.dynamic_fn  = dynamic_fn

    # ── Mode toggle ──────────────────────────────────────────────────────────

    def set_mode(self, s: dict, mode: str) -> dict:
        if mode not in ("static", "dynamic"):
            raise ValueError(f"mode must be 'static' or 'dynamic', got {mode!r}")
        prev_mode = s.get("mode")
        if mode == prev_mode:
            # No-op toggle — just re-affirm the current state without
            # duplicating a bubble.
            return {
                "intent":        "noop",
                "mode":          mode,
                "reply":         "",
                "session_state": _summary(s),
            }
        s["mode"] = mode

        # → Switching INTO chatbot mode with location already set: drop straight
        #   into the narrate phase (re-emit the welcome bubble once).
        if mode == "dynamic" and _location_bootstrap_done(s):
            s["narrate_started"] = False
            out = self.next_turn(s)
            out["mode"] = "dynamic"
            return out

        # → Switching INTO calculator mode: resume the deterministic chip flow
        #   by asking the next missing slot (or re-deriving the answer when all
        #   slots are already filled). Without this the UI would just sit on an
        #   empty "Switched to Calculator" bubble with no chips to tap.
        if mode == "static":
            s["pending_slot"] = None
            out = self.next_turn(s)
            out.setdefault("mode", "static")
            return out

        # → Switching INTO chatbot mode before location is set: continue the
        #   location bootstrap conversationally.
        s["narrate_started"] = False
        out = self.next_turn(s)
        out["mode"] = mode
        return out

    # ── Inputs that mutate session state ──────────────────────────────────────

    def set_geo_mode(self, s: dict, mode: str) -> dict:
        if mode not in ("map", "chat"):
            raise ValueError(f"mode must be 'map' or 'chat', got {mode!r}")
        s["geo_mode"] = mode
        # Switching to chat after a pin keeps the resolved state/city — only
        # clear when switching to map (the new pin will overwrite anyway).
        if mode == "chat":
            # Don't wipe state/city — plan §4.1: "non-destructive toggle".
            pass
        return self.next_turn(s)

    def set_pin(self, s: dict, lat: float, lng: float) -> dict:
        s["lat"], s["lng"] = lat, lng
        s["geo_mode"] = s["geo_mode"] or "map"
        resolved = self.locator.resolve_pin(lat, lng)
        new_state = resolved.get("state_code")
        new_city  = resolved.get("city_code")
        new_city_name = resolved.get("city_name")

        # Smart-relocation: if we've already resolved an answer and the pin
        # lands in a different state/city, recompute without re-asking.
        relocate = self._maybe_relocate(
            s,
            new_state=new_state,
            new_city=new_city,
            new_city_name=new_city_name,
        )
        if relocate is not None:
            # `_maybe_relocate` already mutated state_code/city_code.
            s["road_options"] = self._road_options(lat, lng, s.get("state_code"))
            return relocate

        if new_state:
            s["state_code"] = new_state
        if new_city:
            s["city_code"] = new_city
            s["city_name"] = new_city_name
        s["road_options"] = self._road_options(lat, lng, s.get("state_code"))
        return self.next_turn(s)

    def set_manual_location(
        self,
        s: dict,
        state_code: Optional[str] = None,
        city_code:  Optional[str] = None,
        text:       Optional[str] = None,
    ) -> dict:
        s["geo_mode"] = s["geo_mode"] or "chat"

        # Resolve free-text up-front so the relocation branch can use it too.
        parsed_state_from_text: Optional[str] = None
        parsed_city_entry: Optional[dict] = None
        if text and not (state_code or city_code):
            parsed_state_from_text = self.locator.parse_state(text)
            disamb = self.locator.disambiguate(text, state_code or s.get("state_code"))
            if disamb["candidates"] and not disamb["ambiguous"]:
                parsed_city_entry = disamb["candidates"][0]
            elif disamb["ambiguous"]:
                chips = [
                    {"id": c["code"], "label": f"{c['name']} ({c['state_name']})"}
                    for c in disamb["candidates"][:6]
                ]
                s["pending_slot"] = "city_code"
                return _ask("city_code",
                           f"Which '{text.strip()}' did you mean?",
                           chips,
                           allow_text=True)

        # Build the proposed new state/city before we mutate the session.
        new_state = state_code if state_code in self.engine.states_by_code() else None
        new_city  = city_code  if city_code  in self.engine.cities_by_code() else None
        new_city_name = (
            self.engine.cities_by_code().get(new_city, {}).get("name") if new_city else None
        )
        if not new_state and parsed_state_from_text:
            new_state = parsed_state_from_text
        if not new_city and parsed_city_entry:
            new_city      = parsed_city_entry["code"]
            new_city_name = parsed_city_entry["name"]
            new_state     = new_state or parsed_city_entry["state_code"]

        # Smart-relocation when already answered.
        relocate = self._maybe_relocate(
            s,
            new_state=new_state,
            new_city=new_city,
            new_city_name=new_city_name,
        )
        if relocate is not None:
            return relocate

        # Direct codes take priority.
        if state_code and state_code in self.engine.states_by_code():
            s["state_code"] = state_code
        if city_code and city_code in self.engine.cities_by_code():
            city = self.engine.cities_by_code()[city_code]
            s["city_code"] = city_code
            s["city_name"] = city.get("name", city_code)
            if not s.get("state_code"):
                s["state_code"] = city.get("state_code")

        # Free-text fallback ("I'm in Bangalore").
        if text and not (state_code or city_code):
            if parsed_state_from_text and not s.get("state_code"):
                s["state_code"] = parsed_state_from_text
            if parsed_city_entry:
                c = parsed_city_entry
                s["city_code"] = c["code"]
                s["city_name"] = c["name"]
                s["state_code"] = s.get("state_code") or c["state_code"]
            else:
                disamb = self.locator.disambiguate(text, s.get("state_code"))
                if disamb["candidates"] and not disamb["ambiguous"]:
                    c = disamb["candidates"][0]
                    s["city_code"] = c["code"]
                    s["city_name"] = c["name"]
                    s["state_code"] = s.get("state_code") or c["state_code"]
                elif disamb["ambiguous"]:
                    chips = [
                        {"id": c["code"], "label": f"{c['name']} ({c['state_name']})"}
                        for c in disamb["candidates"][:6]
                    ]
                    s["pending_slot"] = "city_code"
                    return _ask("city_code",
                               f"Which '{text.strip()}' did you mean?",
                               chips,
                               allow_text=True)

        return self.next_turn(s)

    def set_slot(self, s: dict, slot: str, value: str) -> dict:
        """Explicit slot setter — used by `PUT /api/session/{id}/slot`."""
        return self._apply_slot(s, slot, value)

    # ── Main turn entry-point ─────────────────────────────────────────────────

    def turn(
        self,
        s: dict,
        message: Optional[str] = None,
        chip_id: Optional[str] = None,
        chip_ids: Optional[List[str]] = None,
        other_text: Optional[str] = None,
    ) -> dict:
        """One conversational turn from POST /api/session/{id}/turn."""
        # Multi-select clarification submission (checkboxes) takes priority.
        if chip_ids is not None:
            return self._apply_multiselect(s, chip_ids, other_text=other_text)

        if chip_id:
            # Standard clarification escape hatches — handled by the MCQ engine.
            if chip_id in ("clarify:other", "clarify:none", "clarify:unsure"):
                return self._apply_multiselect(s, [chip_id], other_text=other_text)
            # Narrate-phase "free:<text>" chips are LLM-suggested options
            # — clicking one is semantically a user message, not a slot pick.
            if (
                isinstance(chip_id, str)
                and chip_id.startswith("free:")
                and s.get("mode") == "dynamic"
            ):
                return self.turn(s, message=chip_id[len("free:"):])
            pending = s.get("pending_slot")
            if pending:
                return self._apply_slot(s, pending, chip_id)
            # No pending slot but a chip arrived → assume it's the
            # next-best deterministic slot we'd ask next.
            return self._apply_slot(s, self._next_missing_slot(s) or "violation_code", chip_id)

        if message:
            clipped = message[:MAX_MESSAGE_LEN]
            # "fine for no helmet in Mumbai" → price it for Mumbai. (With an
            # answer already on screen, smart relocation handles this instead.)
            if s.get("state_code") and not s.get("last_fine_card"):
                self._apply_location_mention(s, clipped)
            # Free-form clarification the user typed after picking "Something else".
            if s.get("pending_slot") == "clarify_freeform":
                s["pending_slot"] = None
                s["clarify_notes"] = clipped
                if (
                    s.get("mode") == "dynamic"
                    and self.dynamic_fn
                    and _location_bootstrap_done(s)
                ):
                    try:
                        out = self.dynamic_fn(s, clipped)
                    except Exception:
                        log.exception("dynamic_fn (clarify_freeform) raised")
                        out = None
                    if out is not None:
                        out.setdefault("session_state", _summary(s))
                        return out
            # Dynamic-mode narrate phase: location is locked → LLM owns
            # extraction. Before the bootstrap is done we fall through to
            # the existing rule-engine resolvers (state / city free text).
            if (
                s.get("mode") == "dynamic"
                and self.dynamic_fn
                and _location_bootstrap_done(s)
            ):
                relocate = self._relocate_from_text(s, clipped)
                if relocate is not None:
                    return relocate
                try:
                    out = self.dynamic_fn(s, clipped)
                except Exception:
                    log.exception("dynamic_fn (extract_and_reply) raised")
                    out = None
                if out is not None:
                    out.setdefault("session_state", _summary(s))
                    return out
            return self._handle_message(s, clipped)

        # Empty turn → just ask the next question.
        return self.next_turn(s)

    # ── Core: figure out the next thing to ask, or answer ─────────────────────

    def next_turn(self, s: dict) -> dict:
        """Choose the next slot to ask about, or build the answer."""
        if s.get("geo_mode") is None:
            s["pending_slot"] = "geo_mode"
            return _ask(
                slot="geo_mode",
                question="How would you like to set your location?",
                chips=[
                    {"id": "map",  "label": "Use map pin"},
                    {"id": "chat", "label": "Tell me in chat"},
                ],
                allow_text=False,
            )

        slot = self._next_missing_slot(s)
        if slot is None:
            # Dynamic-mode narrate phase: emit the welcome bubble once, then
            # rely on every subsequent user message to flow through
            # `extract_and_reply` via `turn()`.
            if s.get("mode") == "dynamic":
                return self._dynamic_next(s)
            return self._answer(s)

        s["pending_slot"] = slot
        return self._ask_for_slot(s, slot)

    # ── Dynamic-mode resolution after a slot is set (chip / extraction) ───────

    def _dynamic_next(self, s: dict) -> dict:
        """Decide what to do in dynamic (chatbot) mode once location is locked.

        This runs after any deterministic slot mutation (e.g. the user taps a
        violation chip during the narrate phase). Previously this path always
        fell into `_narrate_welcome_or_noop`, which returned an empty `noop`
        once the welcome bubble had been shown — so picking a violation chip
        silently stalled the conversation. Now, if we already have a
        violation, we resolve it into a real fine answer (asking for the
        vehicle first when the violation is vehicle-specific)."""
        # Welcome bubble hasn't fired yet → emit it (or no-op if already shown).
        if not s.get("narrate_started"):
            return self._narrate_welcome_or_noop(s)

        vc = s.get("violation_code")
        if vc:
            # Vehicle-specific violations need a vehicle to price correctly.
            if not s.get("vehicle_segment") and self._violation_requires_vehicle(vc):
                s["pending_slot"] = "vehicle_segment"
                vio_name = (self.engine.get_violation(vc) or {}).get("name", "that")
                chips = [
                    {"id": v["id"], "label": v["label"]}
                    for v in self.catalogs.vehicle_segments()
                ]
                return _ask(
                    "vehicle_segment",
                    f"Got it — {vio_name}. One quick thing so I can price it "
                    "right: what were you riding or driving?",
                    chips,
                    allow_text=True,
                )
            return self._dynamic_answer_for_violation(s)

        # Nothing actionable yet — wait for the next user message.
        return {
            "intent":        "noop",
            "reply":         "",
            "session_state": _summary(s),
        }

    def _driver_context_multiselect(self, s: dict) -> Optional[dict]:
        """Build a single checkbox clarification covering the driver-context
        unknowns (licence, repeat offence, minor). Returns None when there's
        nothing left to ask."""
        if s.get("_driver_context_done") or s.get("_driver_context_offered"):
            return None
        from clarification_engine import build_driver_context_clarification
        return build_driver_context_clarification()

    def _apply_topic_router(
        self,
        s: dict,
        ids: set,
        *,
        other_text: Optional[str] = None,
    ) -> dict:
        """Re-resolve after the user narrows an unmatched story via topic chips."""
        from clarification_engine import (
            UNSURE_FREEFORM_PROMPT,
            enrich_text_with_topic_hints,
        )

        if "clarify:unsure" in ids:
            return self._prompt_clarify_freeform(s, preamble=UNSURE_FREEFORM_PROMPT)

        if "clarify:other" in ids and (other_text or "").strip():
            note = other_text.strip()
            s["clarify_notes"] = note
            s["pending_slot"] = None
            s["_topic_router_done"] = True
            s.setdefault("messages", []).append({"role": "user", "content": note})
            if s.get("mode") == "dynamic" and self.dynamic_fn and _location_bootstrap_done(s):
                try:
                    out = self.dynamic_fn(s, note)
                except Exception:
                    log.exception("dynamic_fn (topic_router other) raised")
                    out = None
                if out is not None:
                    out.setdefault("session_state", _summary(s))
                    return out

        topics = [x for x in ids if x.startswith("topic:")]
        if not topics and "clarify:other" in ids:
            return self._prompt_clarify_freeform(
                s,
                preamble=(
                    "Please describe what happened in the box below — I'll use "
                    "your words to find the right rule and next steps."
                ),
            )

        base = s.get("last_user_story") or s.get("clarify_notes") or ""
        enriched = enrich_text_with_topic_hints(base, topics)
        if not enriched and topics:
            enriched = enrich_text_with_topic_hints("", topics)

        s["topic_hints"] = topics
        s["pending_slot"] = None
        s["_topic_router_done"] = True
        s.setdefault("messages", []).append(
            {"role": "user", "content": enriched or "traffic violation help"}
        )

        if s.get("mode") == "dynamic" and self.dynamic_fn and _location_bootstrap_done(s):
            try:
                out = self.dynamic_fn(s, enriched or "traffic violation help")
            except Exception:
                log.exception("dynamic_fn (topic_router) raised")
                out = None
            if out is not None:
                out.setdefault("session_state", _summary(s))
                return out

        reply = (
            "Thanks — tell me a bit more about what the officer said or what "
            "happened, and I'll look up the exact rule."
        )
        s.setdefault("messages", []).append({"role": "assistant", "content": reply})
        return {
            "intent":        "narrate",
            "reply":         reply,
            "session_state": _summary(s),
        }

    def _apply_incident_context(self, s: dict, ids: set) -> dict:
        """Turn incident MCQ ticks into practical next-step guidance."""
        s["incident_flags"] = sorted(ids - {"clarify:other", "clarify:none"})
        s["pending_slot"]   = None
        s["pending_multi"]  = None
        s["conversation_mode"] = "incident"
        s["violation_code"] = None
        s["last_fine_card"] = None

        loc = s.get("city_name") or s.get("state_code") or "your area"
        parts: List[str] = [
            f"Thanks — here's what I'd prioritise for the incident near {loc}:"
        ]
        if "inc_injury" in ids:
            parts.append(
                "If anyone is hurt, call **108/102** immediately and don't move "
                "seriously injured people unless they're in immediate danger."
            )
        if "inc_police" in ids:
            parts.append(
                "File an **FIR** at the nearest police station as soon as you can — "
                "you'll need it for insurance and any follow-up."
            )
        if "inc_animal" in ids:
            parts.append(
                "For livestock/stray animals: stop safely, note the exact spot, "
                "take photos, and report to local police. There may **not** be a "
                "standard traffic-violation challan for the collision itself — "
                "focus on reporting, insurance, and any local cattle-protection rules."
            )
        if "inc_vehicle" in ids:
            parts.append(
                "If your vehicle is damaged, document it before moving (if safe), "
                "and notify your insurer promptly."
            )
        if "inc_insurance" in ids:
            parts.append(
                "For insurance: keep the FIR, photos, and any witness details — "
                "third-party claims often need police intimation within 24 hours."
            )
        if len(parts) == 1:
            parts.append(
                "Tell me anything else you're worried about — reporting, insurance, "
                "or whether a challan might apply — and I'll walk you through it."
            )
        reply = " ".join(parts)
        s.setdefault("messages", []).append({"role": "assistant", "content": reply})
        return {
            "intent":         "narrate",
            "reply":          reply,
            "fine_card":      None,
            "detail_table":   None,
            "chips":          None,
            "multi_select":   False,
            "selection_mode": "single",
            "allow_other":    False,
            "allow_text":     True,
            "session_state":  _summary(s),
        }

    def _prompt_clarify_freeform(self, s: dict, *, preamble: str) -> dict:
        """Ask the user to type their own clarification."""
        s["pending_slot"]  = "clarify_freeform"
        s["pending_multi"] = None
        reply = preamble
        s.setdefault("messages", []).append({"role": "assistant", "content": reply})
        return {
            "intent":         "ask_slot",
            "slot":           "clarify_freeform",
            "reply":          reply,
            "question":       reply,
            "chips":          None,
            "multi_select":   False,
            "selection_mode": "single",
            "allow_other":    False,
            "allow_text":     True,
            "session_state":  _summary(s),
        }

    def _apply_multiselect(
        self,
        s: dict,
        chip_ids: List[str],
        *,
        other_text: Optional[str] = None,
    ) -> dict:
        """Apply MCQ submissions: incident context, violation pick, driver context."""
        ids = set(chip_ids or [])
        pending = s.get("pending_slot") or s.get("pending_multi")

        from clarification_engine import UNSURE_FREEFORM_PROMPT

        # ── "I'm not sure" ────────────────────────────────────────────────────
        if "clarify:unsure" in ids:
            return self._prompt_clarify_freeform(
                s,
                preamble=UNSURE_FREEFORM_PROMPT,
            )

        # ── "Something else" with free text ───────────────────────────────────
        if "clarify:other" in ids and (other_text or "").strip():
            note = other_text.strip()
            s["clarify_notes"] = note
            s["pending_slot"]  = None
            s["pending_multi"] = None
            s.setdefault("messages", []).append({"role": "user", "content": note})
            if s.get("mode") == "dynamic" and self.dynamic_fn and _location_bootstrap_done(s):
                try:
                    out = self.dynamic_fn(s, note)
                except Exception:
                    log.exception("dynamic_fn (clarify:other) raised")
                    out = None
                if out is not None:
                    out.setdefault("session_state", _summary(s))
                    return out
            return self._handle_message(s, note)

        # ── "Something else" without text yet ─────────────────────────────────
        if "clarify:other" in ids and not (other_text or "").strip():
            return self._prompt_clarify_freeform(
                s,
                preamble=(
                    "Please describe what happened in the box below — I'll use "
                    "your words to find the right rule and next steps."
                ),
            )

        # ── "None of these" ───────────────────────────────────────────────────
        if "clarify:none" in ids:
            if pending in ("incident_context",) or s.get("conversation_mode") == "incident":
                return self._apply_incident_context(s, ids)
            s["violation_code"] = None
            s["pending_slot"]   = None
            s["pending_multi"]  = None
            return self._prompt_clarify_freeform(
                s,
                preamble=(
                    "Understood — none of those fit. Describe in your own words "
                    "what happened or what the officer flagged you for."
                ),
            )

        # ── Topic router (zero graph match) ───────────────────────────────────
        if pending == "topic_router" or ids & {
            "topic:safety", "topic:speed", "topic:docs", "topic:parking",
            "topic:accident", "topic:dui", "topic:overload", "topic:officer",
        }:
            return self._apply_topic_router(s, ids, other_text=other_text)

        # ── Incident context MCQ ──────────────────────────────────────────────
        if pending == "incident_context" or (
            s.get("conversation_mode") == "incident"
            and ids & {"inc_injury", "inc_police", "inc_animal", "inc_vehicle", "inc_insurance"}
        ):
            return self._apply_incident_context(s, ids)

        # ── Violation clarification (multi-select MCQ) ────────────────────────
        if pending == "violation_code":
            vcodes = [x for x in ids if not x.startswith("clarify:")]
            if vcodes:
                s["pending_slot"] = None
                s["pending_multi"] = None
                s["violation_code"] = vcodes[0]
                if len(vcodes) > 1:
                    s["secondary_violation_codes"] = vcodes[1:]
                if s.get("mode") == "dynamic":
                    return self._dynamic_answer_for_violation(s)
                return self._apply_slot(s, "violation_code", vcodes[0])

        # ── Bootstrap slot MCQ (state / city / road / vehicle / geo) ──────────
        if pending in (
            "geo_mode", "state_code", "city_code", "road_bucket",
            "vehicle_segment", "vehicle", "violation_category",
        ):
            picks = [x for x in ids if not x.startswith("clarify:")]
            if len(picks) == 1:
                return self._apply_slot(s, pending, picks[0])
            if picks:
                return self._apply_slot(s, pending, picks[0])

        # ── Driver-context checkbox MCQ ───────────────────────────────────────
        if pending == "driver_context" or s.get("pending_multi") == "driver_context" or ids & {
            "ctx_no_licence", "ctx_repeat", "ctx_minor",
        }:
            if s.get("has_licence") is None:
                s["has_licence"] = not ("ctx_no_licence" in ids)
            if s.get("repeat_offender") is None:
                s["repeat_offender"] = "ctx_repeat" in ids
            if s.get("driver_age") is None:
                s["driver_age"] = 16 if "ctx_minor" in ids else 18
            s["_driver_context_done"] = True
            s["pending_multi"] = None
            s["pending_slot"]  = None

            vc = s.get("violation_code")
            if not vc:
                reply = "Thanks — got it. Tell me anything else and I'll help."
                s.setdefault("messages", []).append(
                    {"role": "assistant", "content": reply}
                )
                return {
                    "intent":         "narrate",
                    "reply":          reply,
                    "fine_card":      None,
                    "chips":          None,
                    "session_state":  _summary(s),
                }

            out = self._dynamic_answer_for_violation(s, with_followup=False)

            extras: List[str] = []
            if s.get("repeat_offender"):
                card = out.get("fine_card") or {}
                if card.get("fine_repeat"):
                    extras.append(
                        f"Since this is a repeat offence, the higher repeat fine of "
                        f"₹{int(card['fine_repeat']):,} applies."
                    )
                else:
                    extras.append("As a repeat offence, expect a steeper penalty.")
            if s.get("has_licence") is False:
                extras.append(
                    "You also mentioned not having a valid licence — that's a "
                    "separate ₹5,000 offence (MV Act §3/§181). You can apply on the "
                    "Parivahan portal, starting with a Learner's Licence."
                )
            if isinstance(s.get("driver_age"), int) and s["driver_age"] < 18:
                extras.append(
                    "Since the rider/driver is under 18, guardian liability may "
                    "apply under MV Act §199A and the vehicle can be impounded."
                )
            if extras:
                out["reply"] = (out.get("reply", "").rstrip() + "\n\n" + " ".join(extras))
                s["messages"].append({"role": "assistant", "content": out["reply"]})

            out["session_state"] = _summary(s)
            return out

        # Fallback — treat as a single chip pick on the pending slot.
        if pending and len(ids) == 1:
            return self._apply_slot(s, pending, next(iter(ids)))

        reply = "Thanks — tell me a bit more and I'll help."
        s.setdefault("messages", []).append({"role": "assistant", "content": reply})
        return {
            "intent":        "narrate",
            "reply":         reply,
            "session_state": _summary(s),
        }

    def _violation_requires_vehicle(self, vc: str) -> bool:
        """True if the violation only applies to a specific vehicle class, so
        we must know the vehicle before quoting a fine."""
        node = self.engine.get_violation(vc) or {}
        va = node.get("vehicle_applicability") or ["ALL"]
        return va != ["ALL"]

    def _dynamic_answer_for_violation(self, s: dict, *, with_followup: bool = True) -> dict:
        """Build the warm narrate-phase fine answer for an already-resolved
        violation_code. Reuses the dynamic_chatbot presentation helpers so the
        chip-driven path and the LLM-driven path produce identical cards.

        When *with_followup* is True and driver-context slots are still
        unknown, a multi-select checkbox clarification is attached so the user
        can refine the fine in one tap-and-submit instead of a serial Q&A."""
        # Lazy import avoids any import-order coupling with dynamic_chatbot.
        from dynamic_chatbot import (
            _build_detail_table,
            _build_explanation,
            _template_narrate_reply,
        )

        from nlu import adjust_for_vehicle
        vc = adjust_for_vehicle(s["violation_code"], s.get("vehicle_segment"),
                                s.get("last_user_story") or "")
        s["violation_code"] = vc
        card = self.engine.quick_fine(
            violation_code     = vc,
            state_code         = s.get("state_code"),
            city_code          = s.get("city_code"),
            vehicle_fine_class = s.get("vehicle_fine_class"),
        )
        s["pending_slot"] = None

        if not card:
            reply = (
                "I couldn't find a specific fine for that exact combination. "
                "Could you describe what happened a little differently, or pick "
                "a closer match?"
            )
            s.setdefault("messages", []).append(
                {"role": "assistant", "content": reply}
            )
            return {
                "intent":        "narrate",
                "reply":         reply,
                "fine_card":     None,
                "detail_table":  None,
                "chips":         None,
                "explanation":   None,
                "session_state": _summary(s),
            }

        # Juvenile surcharge note when an under-18 driver was flagged earlier.
        age = s.get("driver_age")
        if age is not None and isinstance(age, int) and age < 18:
            card["juvenile_surcharge"] = (
                "Guardian/parent liability may apply — MV Act §199A. The "
                "vehicle may be impounded and the guardian prosecuted."
            )

        s["last_fine_card"] = card
        s["stage"]          = "answered"

        reply        = _template_narrate_reply(s, card)
        detail_table = _build_detail_table(card)
        explanation  = _build_explanation(
            s, vc, card,
            match_method="chip",
            match_confidence="high",
            deterministic_name=card.get("violation_name"),
        )

        # Attach a one-shot multi-select clarification for driver context.
        followup = self._driver_context_multiselect(s) if with_followup else None
        chips = None
        multi_select = False
        selection_mode = "single"
        allow_other = False
        if followup:
            s["_driver_context_offered"] = True
            reply = reply.rstrip() + "\n\n" + followup["question"]
            chips = followup["chips"]
            multi_select = True
            selection_mode = followup.get("selection_mode", "multi")
            allow_other = bool(followup.get("allow_other"))
            s["pending_multi"] = "driver_context"

        s.setdefault("messages", []).append(
            {"role": "assistant", "content": reply}
        )
        return {
            "intent":         "narrate",
            "reply":          reply,
            "fine_card":      card,
            "detail_table":   detail_table,
            "chips":          chips,
            "multi_select":   multi_select,
            "selection_mode": selection_mode,
            "allow_other":    allow_other,
            "allow_text":     True,
            "explanation":    explanation,
            "session_state":  _summary(s),
        }

    # ── Slot ordering ─────────────────────────────────────────────────────────

    def _next_missing_slot(self, s: dict) -> Optional[str]:
        if not s.get("state_code"):
            return "state_code"
        if s.get("geo_mode") == "chat" and not s.get("city_code"):
            return "city_code"
        # Dynamic mode stops asking after location is set — the LLM
        # extraction loop owns road / vehicle / violation slots.
        if s.get("mode") == "dynamic":
            return None
        # Road type only narrows the violation list; once the violation is
        # known (e.g. from a typed story) it isn't needed for the fine.
        if not s.get("road_bucket") and not s.get("violation_code"):
            return "road_bucket"
        if not s.get("vehicle_segment"):
            return "vehicle_segment"
        if not s.get("violation_code"):
            return "violation_code"
        return None

    # ── Narrate welcome bubble (dynamic mode only) ───────────────────────────

    def _narrate_welcome_or_noop(self, s: dict) -> dict:
        """First time the user finishes location bootstrap in dynamic mode,
        emit the warm welcome bubble. Idempotent — subsequent empty turns
        return a no-op so the frontend doesn't double-render."""
        s["pending_slot"] = None
        if s.get("narrate_started"):
            return {
                "intent":        "noop",
                "reply":         "",
                "session_state": _summary(s),
            }
        s["narrate_started"] = True
        city  = s.get("city_name") or s.get("city_code")
        state = s.get("state_code") or "your state"
        loc   = f"{city}, {state}" if city else state
        reply = (
            f"Great — you're all set for {loc}. I'm all ears: tell me what "
            "happened in your own words. What were you riding or driving, and "
            "what did the officer pull you up for (or which rule are you "
            "worried about)? Don't stress about the legal jargon — I'll piece "
            "together the exact rule, the section, and the fine for you."
        )
        s.setdefault("messages", []).append(
            {"role": "assistant", "content": reply}
        )
        return {
            "intent":        "narrate",
            "reply":         reply,
            "fine_card":     None,
            "chips":         None,
            "session_state": _summary(s),
        }

    # ── Question builder per slot ─────────────────────────────────────────────

    def _ask_for_slot(self, s: dict, slot: str) -> dict:
        if slot == "state_code":
            chips = [
                {"id": st["code"], "label": st["name"]}
                for st in self.catalogs.states()
            ]
            return _ask(slot, "Which state are you in?", chips, allow_text=True)

        if slot == "city_code":
            state_code = s.get("state_code")
            cities = self.catalogs.cities(state_code) if state_code else []
            chips = [
                {"id": c["code"], "label": c["name"]}
                for c in cities[:12]
            ]
            return _ask(slot, "Which city or town?", chips, allow_text=True)

        if slot == "road_bucket":
            chips = [
                {"id": b["id"], "label": b["label"]}
                for b in self.catalogs.road_buckets()
            ]
            return _ask(slot, "What kind of road were you on?",
                        chips, allow_text=True)

        if slot == "vehicle_segment":
            chips = [
                {"id": v["id"], "label": v["label"]}
                for v in self.catalogs.vehicle_segments()
            ]
            return _ask(slot, "What vehicle were you on?",
                        chips, allow_text=True)

        if slot == "violation_code":
            # First chance: present the 18 category chips, then drill in.
            if not s.get("violation_category"):
                chips = [
                    {"id": g["id"], "label": g["label"]}
                    for g in self.catalogs.violation_categories()
                ]
                s["pending_slot"] = "violation_category"
                return _ask("violation_category",
                            "What kind of violation are you asking about?",
                            chips, allow_text=True)
            chips = self.violator.chips_for_category(
                s["violation_category"],
                road_bucket=s.get("road_bucket"),
                vehicle_fine_class=s.get("vehicle_fine_class"),
                k=8,
            )
            return _ask("violation_code",
                        "Which specific violation?",
                        chips, allow_text=True)

        # Shouldn't happen.
        return _ask(slot, f"Need value for {slot}", chips=[], allow_text=True)

    # ── Free-text message routing ─────────────────────────────────────────────

    def _handle_message(self, s: dict, message: str) -> dict:
        from clarification_engine import scope_response
        scoped = scope_response(message)
        if scoped is not None:
            s.setdefault("messages", []).append({"role": "user", "content": message})
            s.setdefault("messages", []).append(
                {"role": "assistant", "content": scoped["reply"]}
            )
            scoped["session_state"] = _summary(s)
            return scoped

        # Calculator: understand typed text before treating it as a chip answer.
        pre = self._calculator_pre(s, message)
        if pre is not None:
            return pre

        slot = self._next_missing_slot(s)

        # 1. State / city slot → try the location resolver.
        if slot == "state_code":
            code = self.locator.parse_state(message)
            if code:
                return self._apply_slot(s, "state_code", code)
            return _ask("state_code",
                        f"I couldn't match '{message.strip()[:60]}' to a state — try a chip below.",
                        [{"id": st["code"], "label": st["name"]}
                         for st in self.catalogs.states()],
                        allow_text=True)

        if slot == "city_code":
            disamb = self.locator.disambiguate(message, s.get("state_code"))
            if disamb["candidates"] and not disamb["ambiguous"]:
                c = disamb["candidates"][0]
                return self._apply_slot(s, "city_code", c["code"])
            if disamb["ambiguous"]:
                chips = [
                    {"id": c["code"], "label": f"{c['name']} ({c['state_name']})"}
                    for c in disamb["candidates"][:6]
                ]
                s["pending_slot"] = "city_code"
                return _ask("city_code",
                            f"Which '{message.strip()[:40]}' did you mean?",
                            chips, allow_text=True)
            return self._ask_for_slot(s, "city_code")

        # 2. Vehicle slot → use the existing graph vehicle matcher.
        if slot == "vehicle_segment":
            matches = self.engine.match_vehicle(message)
            if matches:
                seg = matches[0].get("segment")
                if seg:
                    return self._apply_slot(s, "vehicle_segment", seg)
            return self._ask_for_slot(s, "vehicle_segment")

        # 3. Road bucket → simple keyword test before falling back to chips.
        if slot == "road_bucket":
            ml = message.lower()
            for kw, bucket in (
                ("highway", "highway"), ("expressway", "highway"),
                ("nh", "highway"), ("sh", "highway"), ("toll", "highway"),
                ("main", "main_road"), ("arterial", "main_road"),
                ("district road", "main_road"),
                ("street", "street"), ("lane", "street"),
                ("residential", "street"), ("colony", "street"),
            ):
                if kw in ml:
                    return self._apply_slot(s, "road_bucket", bucket)
            return self._ask_for_slot(s, "road_bucket")

        # 4. Violation slot → keyword resolver, then LLM fallback.
        if slot == "violation_code":
            ranked = self.violator.resolve(
                message,
                road_bucket=s.get("road_bucket"),
                vehicle_fine_class=s.get("vehicle_fine_class"),
            )
            if self.violator.is_deterministic(ranked):
                return self._apply_slot(s, "violation_code", ranked[0][0])
            if ranked:
                from clarification_engine import build_violation_clarification
                s["pending_slot"] = "violation_code"
                s["messages"].append({"role": "user", "content": message})
                candidates = [
                    (vc, score, (self.engine.get_violation(vc) or {}).get("name", vc))
                    for vc, score in ranked[:4]
                ]
                return build_violation_clarification(
                    "Based on what you said, tick anything that might apply "
                    "(or pick 'Something else' to explain in your own words):",
                    candidates,
                    multi=True,
                )
            # Zero matches → LLM freeform.
            return self._freeform(s, message)

        # 5. Already answered → follow-up / relocation / new violation / LLM.
        return self._after_answer(s, message)

    def _with_reask(self, s: dict, out: dict) -> dict:
        """Info reply during slot-filling → append the pending question + chips
        so the calculator flow can continue."""
        out = dict(out)
        out["intent"] = out.get("intent") if out.get("intent") != "narrate" else "answer"
        if self._next_missing_slot(s) and s.get("stage") != "answered":
            nxt = self.next_turn(s)
            q = nxt.get("question") or ""
            if q:
                out["reply"] = f"{(out.get('reply') or '').rstrip()}\n\n{q}"
                out["question"] = out["reply"]
            for k in ("slot", "chips", "multi_select", "selection_mode", "allow_text", "allow_other"):
                if k in nxt:
                    out[k] = nxt[k]
        out["session_state"] = _summary(s)
        return out

    def _calculator_pre(self, s: dict, message: str) -> Optional[dict]:
        """Calculator mode, before an answer exists: handle guardrail / small
        talk / FAQ / info questions, and harvest every slot a typed story
        contains ("riding my bike without a helmet on a city street" fills
        vehicle + violation + road at once) instead of re-asking one chip."""
        if s.get("mode") == "dynamic" or self._next_missing_slot(s) is None:
            return None          # answered → _after_answer owns the turn
        from dynamic_chatbot import (
            guardrail_response, _pre_route, classify_intent, _handle_documents,
            _handle_license_guidance, _handle_post_incident, _set_vehicle,
        )
        from clarification_engine import looks_like_incident
        import nlu

        guard = guardrail_response(message)
        if guard is not None:
            s["messages"].append({"role": "user", "content": message})
            s["messages"].append({"role": "assistant", "content": guard["reply"]})
            return self._with_reask(s, guard)

        pre = _pre_route(s, message, self.engine, calculator=True)
        if pre is not None:
            return self._with_reask(s, pre)

        ranked = self.violator.resolve(
            nlu.mask_followup_terms(message),
            road_bucket=s.get("road_bucket"),
            vehicle_fine_class=s.get("vehicle_fine_class"),
        )
        offence = ranked[0][0] if ranked and self.violator.is_deterministic(ranked) else None

        intent = classify_intent(message)
        info = None
        if not (offence and (nlu.is_fine_query(message) or nlu.has_enforcement_context(message))):
            if intent == "post_incident" or looks_like_incident(message):
                info = _handle_post_incident(s, message)
            elif intent == "documents":
                info = _handle_documents(s, message)
            elif intent == "license_guidance":
                info = _handle_license_guidance(s, message)
        if info is not None:
            s["messages"].append({"role": "user", "content": message})
            s["messages"].append({"role": "assistant", "content": info.get("reply", "")})
            return self._with_reask(s, {"intent": "answer", "reply": info.get("reply", ""),
                                        "fine_card": None})

        # Harvest slots from the story (never overwrite what's already set).
        filled = []
        pending = self._next_missing_slot(s)
        from clarification_engine import is_traffic_related, _OUT_OF_SCOPE_REPLY
        if (pending not in ("state_code", "city_code") and not ranked
                and len(message.split()) > 3 and not is_traffic_related(message)):
            s["messages"].append({"role": "user", "content": message})
            return self._with_reask(s, {"intent": "answer", "reply": _OUT_OF_SCOPE_REPLY,
                                        "fine_card": None, "scope": "out_of_scope"})
        if s.get("state_code"):
            seg = self.engine.detect_vehicle_segment(message) or (
                nlu.vehicle_from_violation(offence) if offence else None)
            if seg and not s.get("vehicle_segment"):
                _set_vehicle(s, seg)
                filled.append("vehicle_segment")
            if offence and not s.get("violation_code"):
                s["violation_code"] = offence
                filled.append("violation_code")
            if not s.get("road_bucket"):
                ml = f" {message.lower()} "
                for kw, bucket in ((" highway", "highway"), ("expressway", "highway"),
                                   (" main road", "main_road"), ("city street", "street"),
                                   (" street", "street"), ("residential", "street"),
                                   (" colony", "street")):
                    if kw in ml:
                        s["road_bucket"] = bucket
                        filled.append("road_bucket")
                        break
        # Only take over when the story gave us something beyond (or other
        # than) a plain answer to the pending chip question.
        if filled and (len(filled) > 1 or filled[0] != pending):
            s["messages"].append({"role": "user", "content": message})
            if offence:
                s["last_user_story"] = message
            s["pending_slot"] = None
            return self.next_turn(s)
        # Undo a lone fill of the pending slot — the normal slot parser below
        # handles that case with its own messages.
        for f in filled:
            if f == "vehicle_segment":
                s["vehicle_segment"] = s["vehicle_fine_class"] = s["vehicle_type"] = None
            else:
                s[f] = None
        return None

    def _set_state_with_capital(self, s: dict, state: str) -> None:
        """State named without a city → use its capital (shown in the answer),
        so chat-mode sessions don't stall waiting for a city."""
        s["state_code"], s["city_code"], s["city_name"] = state, None, None
        st = next((x for x in self.catalogs.states() if x["code"] == state), None)
        cap = (st or {}).get("capital")
        if cap:
            d = self.locator.disambiguate(cap, state)
            c = next((c for c in (d.get("candidates") or []) if c["state_code"] == state), None)
            if c:
                s["city_code"], s["city_name"] = c["code"], c["name"]

    def _apply_location_mention(self, s: dict, message: str) -> bool:
        """Switch location when the text names a place after in/at/near/from."""
        for m in re.finditer(r"\b(?:in|at|near|around|from)\s+([a-z][a-z .'-]{2,40})", message, re.I):
            phrase = re.split(r"\b(?:for|on|with|while|and|but|because|yesterday|today|last)\b|[,.?!]",
                              m.group(1), flags=re.I)[0].strip()
            if not phrase or len(phrase) < 3:
                continue
            d = self.locator.disambiguate(phrase)
            cands = d.get("candidates") or []
            state = None
            city = None
            if cands and len({c["state_code"] for c in cands}) == 1:
                state = cands[0]["state_code"]
                if not d.get("ambiguous"):
                    city = cands[0]
            else:
                state = self.locator.parse_state(phrase)
            if state and (state != s.get("state_code") or (city and city["code"] != s.get("city_code"))):
                if city:
                    s["state_code"], s["city_code"], s["city_name"] = state, city["code"], city["name"]
                else:
                    self._set_state_with_capital(s, state)
                return True
        # "drunk driving fine delhi" — a bare place name closing a fine question.
        if re.search(r"\b(fine|challan|penalty|charge)\b", message, re.I):
            words = re.findall(r"[a-z]+", message.lower())
            for n in (2, 1):
                if len(words) <= n:
                    continue
                tail = " ".join(words[-n:])
                d = self.locator.disambiguate(tail)
                cands = [c for c in (d.get("candidates") or [])
                         if c["name"].lower().startswith(tail.split()[0])]
                if cands and len({c["state_code"] for c in cands}) == 1:
                    c0 = cands[0]
                    if c0["state_code"] != s.get("state_code") or c0["code"] != s.get("city_code"):
                        s["state_code"] = c0["state_code"]
                        s["city_code"], s["city_name"] = c0["code"], c0["name"]
                        return True
                st = self.locator.parse_state(tail)
                if st and len(tail) > 3 and st != s.get("state_code"):
                    self._set_state_with_capital(s, st)
                    return True
        return False

    def _relocate_from_text(self, s: dict, message: str) -> Optional[dict]:
        """"What if I was in Bangalore?" → re-price the answered violation.

        Only fires once an answer exists and the text names a different
        state/city unambiguously; otherwise returns None."""
        if s.get("stage") != "answered" or not s.get("violation_code"):
            return None
        disamb = self.locator.disambiguate(message)
        cands  = disamb.get("candidates") or []
        new_state = new_city = new_city_name = None
        if cands and len({c["state_code"] for c in cands}) == 1:
            new_state = cands[0]["state_code"]
            if not disamb.get("ambiguous"):
                new_city, new_city_name = cands[0]["code"], cands[0]["name"]
        elif not cands:
            new_state = self.locator.parse_state(message)
        if not new_state or (new_state == s.get("state_code")
                             and (not new_city or new_city == s.get("city_code"))):
            return None
        s.setdefault("messages", []).append({"role": "user", "content": message})
        out = self._maybe_relocate(
            s, new_state=new_state, new_city=new_city, new_city_name=new_city_name,
        )
        if out is None:
            s["messages"].pop()
        return out

    def _after_answer(self, s: dict, message: str) -> dict:
        """Free text once the calculator has produced an answer.

        Deterministic paths first (all offline, instant, graph-grounded):
          1. safety guardrail (bribery / forgery / evasion)
          2. "what if in Bangalore?"  → smart relocation of the same violation
          3. a clearly different violation → re-answer with the same context
          4. "is it compoundable?" etc. → answer from the last fine card
        Everything else goes to the LLM (which itself falls back offline).
        """
        from dynamic_chatbot import guardrail_response
        from followups import followup_reply

        def _reply(out: dict) -> dict:
            s["messages"].append({"role": "user", "content": message})
            s["messages"].append({"role": "assistant", "content": out.get("reply") or ""})
            out["session_state"] = _summary(s)
            return out

        guard = guardrail_response(message)
        if guard is not None:
            return _reply({**guard, "intent": "answer"})

        # Shared understanding layer: small talk, FAQ, recall, vehicle
        # what-ifs, follow-ups, several offences in one message.
        from dynamic_chatbot import _pre_route
        pre = _pre_route(s, message, self.engine, calculator=True)
        if pre is not None:
            pre = dict(pre)
            if pre.get("intent") == "narrate":
                pre["intent"] = "answer"
            pre["session_state"] = _summary(s)
            return pre

        # Accident procedure / paperwork / licence how-to → canned, grounded.
        from clarification_engine import looks_like_incident
        from dynamic_chatbot import (
            classify_intent, _handle_documents, _handle_license_guidance,
            _handle_post_incident,
        )
        intent = classify_intent(message)
        handler = (
            _handle_post_incident if intent == "post_incident" or looks_like_incident(message)
            else _handle_documents if intent == "documents"
            else _handle_license_guidance if intent == "license_guidance"
            else None
        )
        if handler is not None:
            out = handler(s, message)
            return _reply({"intent": "answer", "reply": out.get("reply", ""),
                           "fine_card": None})

        # 2. Location change on an answered violation.
        relocate = self._relocate_from_text(s, message)
        if relocate is not None:
            return relocate

        # 3. A different violation named outright.
        ranked = self.violator.resolve(
            message,
            road_bucket=s.get("road_bucket"),
            vehicle_fine_class=s.get("vehicle_fine_class"),
        )
        if (self.violator.is_deterministic(ranked)
                and ranked[0][0] != s.get("violation_code")):
            s["messages"].append({"role": "user", "content": message})
            veh = self.engine.match_vehicle(message)
            seg = veh[0].get("segment") if veh else None
            if seg in SEGMENT_FINE_CLASS:
                s["vehicle_segment"]    = seg
                s["vehicle_fine_class"] = SEGMENT_FINE_CLASS[seg]
                s["vehicle_type"]       = SEGMENT_LABEL.get(seg, seg)
            return self._apply_slot(s, "violation_code", ranked[0][0])

        # 4. Follow-up about the current answer.
        reply = followup_reply(message, s.get("last_fine_card"))
        if reply:
            return _reply({"intent": "answer", "reply": reply,
                           "fine_card": s.get("last_fine_card")})

        # Nothing traffic-related at all → polite out-of-scope (no LLM tokens).
        from clarification_engine import is_traffic_related, _OUT_OF_SCOPE_REPLY
        referential = bool(re.search(r"\b(this|that|it|this one)\b", message, re.I)) and len(message.split()) <= 12
        if not is_traffic_related(message) and not ranked and not referential:
            return _reply({"intent": "answer", "reply": _OUT_OF_SCOPE_REPLY,
                           "fine_card": None, "scope": "out_of_scope"})

        return self._freeform(s, message)

    # ── Slot application (chip + explicit slot endpoint) ──────────────────────

    def _apply_slot(self, s: dict, slot: str, value: str) -> dict:
        if slot == "geo_mode":
            return self.set_geo_mode(s, value)

        if slot == "state_code":
            if value in self.engine.states_by_code():
                # Smart-relocation: only fires when the conversation is already
                # in "answered" stage and the state actually changes.
                new_state = value
                new_city  = s.get("city_code")
                new_city_name = s.get("city_name")
                if new_city:
                    cur_city = self.engine.cities_by_code().get(new_city)
                    if cur_city and cur_city.get("state_code") != new_state:
                        new_city = None
                        new_city_name = None
                relocate = self._maybe_relocate(
                    s, new_state=new_state,
                    new_city=new_city, new_city_name=new_city_name,
                )
                if relocate is not None:
                    return relocate
                s["state_code"] = new_state
                if s.get("city_code"):
                    city = self.engine.cities_by_code().get(s["city_code"])
                    if city and city.get("state_code") != new_state:
                        s["city_code"] = None
                        s["city_name"] = None
            s["pending_slot"] = None
            return self.next_turn(s)

        if slot == "city_code":
            city = self.engine.cities_by_code().get(value)
            if city:
                relocate = self._maybe_relocate(
                    s, new_state=city.get("state_code") or s.get("state_code"),
                    new_city=value, new_city_name=city.get("name"),
                )
                if relocate is not None:
                    return relocate
                s["city_code"] = value
                s["city_name"] = city.get("name")
                if not s.get("state_code"):
                    s["state_code"] = city.get("state_code")
            s["pending_slot"] = None
            return self.next_turn(s)

        if slot == "road_bucket":
            if value in ("highway", "main_road", "street"):
                s["road_bucket"] = value
                s["confirmed_road"] = None
                s["confirmed_road_class"] = None
            s["pending_slot"] = None
            return self.next_turn(s)

        if slot in ("vehicle", "vehicle_segment"):
            seg = value
            if seg in SEGMENT_FINE_CLASS:
                s["vehicle_segment"]    = seg
                s["vehicle_fine_class"] = SEGMENT_FINE_CLASS[seg]
                s["vehicle_type"]       = SEGMENT_LABEL.get(seg, seg)
            s["pending_slot"] = None
            return self.next_turn(s)

        if slot == "violation_category":
            groups = {g["id"] for g in self.catalogs.violation_categories()}
            if value in groups:
                s["violation_category"] = value
            s["pending_slot"] = "violation_code"
            return self.next_turn(s)

        if slot == "violation_code":
            if self.engine.get_violation(value):
                s["violation_code"] = value
                s["stage"]          = "open"  # will flip to "answered" by _answer
            s["pending_slot"] = None
            return self.next_turn(s)

        # Unknown slot — fall back to next ask.
        s["pending_slot"] = None
        return self.next_turn(s)

    # ── Smart-relocation (use case 1) ────────────────────────────────────────

    def _maybe_relocate(
        self,
        s: dict,
        *,
        new_state: Optional[str],
        new_city: Optional[str],
        new_city_name: Optional[str] = None,
    ) -> Optional[dict]:
        """If the user has already reached the answer state and *only*
        location changed, recompute the fine via `quick_fine` and return a
        new `answer_updated` turn. Otherwise return None (caller continues
        normal flow).
        """
        if s.get("stage") != "answered" or not s.get("violation_code"):
            return None
        prev_state     = s.get("state_code")
        prev_city      = s.get("city_code")
        prev_city_name = s.get("city_name")
        prev_card      = s.get("last_fine_card")

        if new_state is None and new_city is None:
            return None
        # Did anything actually change?
        if new_state == prev_state and new_city == prev_city:
            return None

        # Snapshot the old location so /revert_location can swap back.
        s["prev_location"] = {
            "state_code": prev_state,
            "city_code":  prev_city,
            "city_name":  prev_city_name,
            "fine_card":  prev_card,
        }

        # Apply the new location.
        if new_state is not None:
            s["state_code"] = new_state
        if new_city is not None:
            s["city_code"] = new_city
            s["city_name"] = new_city_name or self.engine.cities_by_code().get(
                new_city, {}
            ).get("name")
        else:
            # State changed but city wasn't provided → drop a mismatched city.
            if s.get("city_code"):
                cur = self.engine.cities_by_code().get(s["city_code"])
                if cur and cur.get("state_code") != s.get("state_code"):
                    s["city_code"] = None
                    s["city_name"] = None

        new_card = self.engine.quick_fine(
            violation_code     = s["violation_code"],
            state_code         = s.get("state_code"),
            city_code          = s.get("city_code"),
            vehicle_fine_class = s.get("vehicle_fine_class"),
        )
        s["last_fine_card"] = new_card
        s["pending_slot"]   = None
        s["stage"]          = "answered"

        diff = _diff_fine_cards(prev_card, new_card)
        loc  = s.get("city_name") or s.get("state_code") or "the new location"
        prev_loc = prev_city_name or prev_state or "before"
        def _rs(c):
            v = (c or {}).get("fine_first")
            return f"₹{int(v):,}" if isinstance(v, (int, float)) and v else "not specified"
        name = (new_card or prev_card or {}).get("violation_name", "this offence")
        now, before = _rs(new_card), _rs(prev_card)
        if now == before:
            reply = f"In **{loc}**, **{name}** is also **{now}** for a first offence — same as {prev_loc}."
        else:
            reason = _diff_reason(diff).rstrip(".")
            reply = (f"In **{loc}**, **{name}** is **{now}** for a first offence "
                     f"(vs {before} in {prev_loc}) — {reason}.")
        s["messages"].append({"role": "assistant", "content": reply})

        return {
            "intent":              "answer_updated",
            "reply":                reply,
            "fine_card":           new_card,
            "previous_fine_card":  prev_card,
            "diff":                diff,
            "session_state":       _summary(s),
        }

    def revert_location(self, s: dict) -> dict:
        """Swap back to the previous (state, city, fine_card) snapshot."""
        prev = s.get("prev_location") or {}
        if not prev:
            return {
                "intent":        "noop",
                "reply":         "Nothing to revert.",
                "session_state": _summary(s),
            }
        cur_state     = s.get("state_code")
        cur_city      = s.get("city_code")
        cur_city_name = s.get("city_name")
        cur_card      = s.get("last_fine_card")

        s["state_code"]     = prev.get("state_code")
        s["city_code"]      = prev.get("city_code")
        s["city_name"]      = prev.get("city_name")
        s["last_fine_card"] = prev.get("fine_card")
        # Swap the snapshot so the user can "un-revert" once.
        s["prev_location"]  = {
            "state_code": cur_state,
            "city_code":  cur_city,
            "city_name":  cur_city_name,
            "fine_card":  cur_card,
        }
        loc = s.get("city_name") or s.get("state_code") or "the previous location"
        reply = f"Reverted to {loc}."
        s["messages"].append({"role": "assistant", "content": reply})
        return {
            "intent":              "answer_updated",
            "reply":               reply,
            "fine_card":           s["last_fine_card"],
            "previous_fine_card":  cur_card,
            "diff":                _diff_fine_cards(cur_card, s["last_fine_card"]),
            "session_state":       _summary(s),
        }

    # ── Final answer assembly ─────────────────────────────────────────────────

    def _answer(self, s: dict) -> dict:
        from nlu import adjust_for_vehicle
        s["violation_code"] = adjust_for_vehicle(s["violation_code"], s.get("vehicle_segment"),
                                                 s.get("last_user_story") or "")
        card = self.engine.quick_fine(
            s["violation_code"],
            state_code=s.get("state_code"),
            city_code=s.get("city_code"),
            vehicle_fine_class=s.get("vehicle_fine_class"),
        )
        s["last_fine_card"] = card
        s["stage"] = "answered"
        s["pending_slot"] = None

        reply = self._format_reply(s, card)
        s["messages"].append({"role": "assistant", "content": reply})

        return {
            "intent":        "answer",
            "reply":         reply,
            "fine_card":     card,
            "session_state": _summary(s),
        }

    def _format_reply(self, s: dict, card: Optional[dict]) -> str:
        if not card:
            return (
                "I couldn't find a specific fine for that combination. "
                "Try a different violation or rephrase your question."
            )
        first  = f"₹{card['fine_first']:,}"  if card.get("fine_first")  else "varies"
        repeat = (f"₹{card['fine_repeat']:,}" if card.get("fine_repeat")
                  else "same as first (no separate repeat amount)")
        imp    = f" • Imprisonment: {card['imprisonment']}" if card.get("imprisonment") else ""
        sec    = f" (MV Act §{card['mv_section']})" if card.get("mv_section") else ""
        src    = card.get("fine_source", "central")
        head   = f"**{card.get('violation_name','')}**{sec}"
        body   = f"First offence: {first} · Repeat: {repeat}{imp}\nFine source: {src}"
        veh    = s.get("vehicle_type")
        if veh:
            body += f"\nApplies to: {veh}"
        return f"{head}\n{body}"

    # ── LLM freeform fallback (only place that calls Ollama) ──────────────────

    def _freeform(self, s: dict, message: str) -> dict:
        s["messages"].append({"role": "user", "content": message})
        if not self.freeform_fn:
            reply = ("I don't have a rule-based answer for that. "
                     "Try picking a violation chip above.")
            s["messages"].append({"role": "assistant", "content": reply})
            return {"intent": "answer", "reply": reply,
                    "fine_card": None, "session_state": _summary(s)}
        out = self.freeform_fn(s, message)
        reply     = out.get("reply", "")
        fine_card = out.get("fine_card")
        if fine_card:
            s["last_fine_card"] = fine_card
            if fine_card.get("violation_code"):
                s["violation_code"] = fine_card["violation_code"]
        s["messages"].append({"role": "assistant", "content": reply})
        s["stage"] = "answered"
        return {
            "intent":        "answer",
            "reply":         reply,
            "fine_card":     fine_card,
            "session_state": _summary(s),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _road_options(self, lat: float, lng: float,
                      state_code: Optional[str]) -> List[dict]:
        """Reuses `nearest_corridors` to provide up to 3 road chips (one per bucket)."""
        corridors = self.engine.nearest_corridors(lat, lng, n=20)
        best:  Dict[str, Optional[dict]] = {"highway": None, "main_road": None, "street": None}
        bestd: Dict[str, float]          = {"highway": 9999.0, "main_road": 9999.0, "street": 9999.0}
        for c in corridors:
            bucket = c.get("bucket", "street")
            d = c["distance_km"]
            if d < bestd.get(bucket, 9999.0):
                bestd[bucket] = d
                best[bucket]  = {
                    "name":        c["name"],
                    "road_class":  c["road_class"],
                    "group":       bucket,
                    "distance_km": round(d, 1),
                    "state_code":  state_code or "",
                }
        return [v for k in ("highway", "main_road", "street") if (v := best[k])]


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ask(
    slot: str,
    question: str,
    chips: List[dict],
    allow_text: bool,
    *,
    selection_mode: str = "single",
    allow_other: bool = False,
) -> dict:
    from clarification_engine import build_slot_clarification
    return build_slot_clarification(
        slot,
        question,
        chips,
        allow_text=allow_text,
        selection_mode=selection_mode,
        allow_other=allow_other,
    )


def _summary(s: dict) -> dict:
    return {
        "session_id":         s["session_id"],
        "mode":               s.get("mode", "static"),
        "geo_mode":           s.get("geo_mode"),
        "state_code":         s.get("state_code"),
        "city_code":          s.get("city_code"),
        "city_name":          s.get("city_name"),
        "road_bucket":        s.get("road_bucket"),
        "confirmed_road":     s.get("confirmed_road"),
        "vehicle_segment":    s.get("vehicle_segment"),
        "vehicle_fine_class": s.get("vehicle_fine_class"),
        "vehicle_type":       s.get("vehicle_type"),
        "violation_category": s.get("violation_category"),
        "violation_code":     s.get("violation_code"),
        "driver_age":         s.get("driver_age"),
        "has_licence":        s.get("has_licence"),
        "licence_type":       s.get("licence_type"),
        "repeat_offender":    s.get("repeat_offender"),
        "stage":              s.get("stage"),
        "pending_slot":       s.get("pending_slot"),
        "last_fine_card":     s.get("last_fine_card"),
        "has_prev_location":  bool(s.get("prev_location")),
        # Frontend uses this to decide when to switch the input placeholder
        # to "Tell me what happened…" and stop rendering chip rows.
        "narrate_started":    bool(s.get("narrate_started")),
    }


def _location_bootstrap_done(s: dict) -> bool:
    """True iff the dynamic-mode narrate phase should take over.
    - Map mode (or unset): state_code alone is sufficient (city is auto-set
      from the pin, but never strictly required).
    - Chat mode: both state and city must be set."""
    if not s.get("state_code"):
        return False
    if s.get("geo_mode") == "chat" and not s.get("city_code"):
        return False
    return True


# ── Smart-relocation helpers ─────────────────────────────────────────────────


def _diff_fine_cards(a: Optional[dict], b: Optional[dict]) -> dict:
    """Compact dict listing every field that differs between two fine cards.
    Empty dict means nothing meaningful changed."""
    a = a or {}
    b = b or {}
    diff: dict = {}
    for k in ("fine_first", "fine_repeat", "imprisonment",
              "fine_source", "state_code", "city_code", "violation_name"):
        av = a.get(k)
        bv = b.get(k)
        if av != bv:
            diff[k] = {"from": av, "to": bv}
    return diff


def _diff_reason(diff: dict) -> str:
    """Build the trailing reason clause for the answer_updated bubble.

    We bias toward `fine_source` (clearest delta) and fall back to the
    actual amount change."""
    src = diff.get("fine_source") or {}
    if src:
        to = (src.get("to") or "").lower()
        if to == "city":
            return "the city has its own enforcement schedule."
        if to == "state":
            return "the state applies its own multiplier over the central rates."
        if to == "central":
            return "no city- or state-specific override applies here — central rates kick in."
    first = diff.get("fine_first") or {}
    if first:
        f = first.get("from")
        t = first.get("to")
        if isinstance(f, (int, float)) and isinstance(t, (int, float)):
            if t > f:
                return "the new location's first-offence rate is higher."
            if t < f:
                return "the new location's first-offence rate is lower."
    if not diff:
        return "fine schedules are identical, so the card is reissued for the new location."
    return "the local fine schedule differs."


# ── Singletons used by api.py ────────────────────────────────────────────────

_store:      Optional[SessionStore]  = None
_dm:         Optional[DialogManager] = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def get_dialog_manager(
    freeform_fn: Optional[Callable[[dict, str], dict]] = None,
    dynamic_fn:  Optional[Callable[[dict, str], dict]] = None,
) -> DialogManager:
    global _dm
    if _dm is None:
        _dm = DialogManager(freeform_fn=freeform_fn, dynamic_fn=dynamic_fn)
    else:
        if freeform_fn is not None:
            _dm.freeform_fn = freeform_fn
        if dynamic_fn is not None:
            _dm.dynamic_fn = dynamic_fn
    return _dm


def session_state(s: dict) -> dict:
    return _summary(s)
