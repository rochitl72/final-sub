#!/usr/bin/env python3
"""
test_v3_2_smoke.py — Cross-cutting smoke for the v3.2 narrate-phase rework
==========================================================================
Walks two end-to-end flows against the in-process FastAPI app via
`fastapi.testclient.TestClient` — no Ollama required.

  1. Static (calculator) mode  → chip → chip → … → `intent: "answer"` with
     a real `fine_card`. Confirms the existing slot-fill path still works.

  2. Dynamic (chatbot) mode → state chip → city chip → narrate welcome →
     a single user message ("no helmet on my bike") whose response we
     patch via `dynamic_chatbot._call_narrate_llm` so the LLM call is
     replaced by a canned reply containing the v3.2 protocol lines.
     We assert that slot extraction filled vehicle_segment=two_wheeler
     and violation_code=SAFETY_NO_HELMET_RIDER, the fine card was attached,
     and the protocol lines were stripped from the user-facing reply.

Run with:
    python3 backend/tests/test_v3_2_smoke.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

import dialog_manager   # noqa: E402  for the test-time wiring helper
import persistence      # noqa: E402  the persistence selftest is part of the bundle
from api import app     # noqa: E402


# ─── Helpers ────────────────────────────────────────────────────────────────

CANNED_NARRATE_REPLY = (
    "Got it — riding without a helmet attracts a ₹500 fine here under "
    "MV Act §194D. Strap on an ISI-marked helmet (IS 4151) whenever you "
    "ride, even for a 2-minute errand. Drive safe!\n"
    "<<SLOTS road_bucket=street vehicle_segment=two_wheeler "
    "violation_code=SAFETY_NO_HELMET_RIDER NEEDS=none>>"
)


def _post(client, sid, suffix, body=None):
    r = client.post(f"/api/session/{sid}{suffix}",
                    json=body if body is not None else {})
    r.raise_for_status()
    return r.json()


def _put(client, sid, suffix, body):
    r = client.put(f"/api/session/{sid}{suffix}", json=body)
    r.raise_for_status()
    return r.json()


def _ensure_callbacks_wired() -> None:
    """The FastAPI startup hook wires `freeform_fn` and `dynamic_fn` on the
    DialogManager singleton. When tests are run multiple times in the same
    process (or import order is unusual) the wiring may not have happened
    yet — make sure it does."""
    from llm_chatbot     import handle_freeform
    from dynamic_chatbot import extract_and_reply as _extract
    dialog_manager.get_dialog_manager(
        freeform_fn = lambda s, t: handle_freeform(s, t),
        dynamic_fn  = lambda s, t: _extract(s, t),
    )


# ─── Tests ──────────────────────────────────────────────────────────────────


class StaticChipFlow(unittest.TestCase):
    """Plain calculator-mode flow — chip after chip until we hit `answer`."""

    def setUp(self):
        self.client = TestClient(app)
        _ensure_callbacks_wired()

    def test_chip_walk_yields_answer(self):
        # Start in static mode.
        data = self.client.post("/api/sessions/new",
                                json={"mode": "static"}).json()
        sid = data["session_id"]
        self.assertEqual(data["mode"], "static")

        # Pin geo_mode = chat so we resolve state/city via chips.
        _put(self.client, sid, "/geo_mode", {"mode": "chat"})

        # State → city → road → vehicle → violation_category → violation
        _post(self.client, sid, "/slot",
              {"slot": "state_code", "value": "KA"})
        _post(self.client, sid, "/slot",
              {"slot": "city_code",  "value": "BLR"})
        _post(self.client, sid, "/slot",
              {"slot": "road_bucket", "value": "street"})
        _post(self.client, sid, "/slot",
              {"slot": "vehicle",     "value": "two_wheeler"})
        _post(self.client, sid, "/slot",
              {"slot": "violation_category", "value": "safety_gear"})
        # Free text — the resolver deterministically picks SAFETY_NO_HELMET_RIDER.
        final = _post(self.client, sid, "/turn",
                      {"message": "no helmet on my bike"})

        self.assertEqual(final["intent"], "answer",
                         f"unexpected intent: {final!r}")
        self.assertIsNotNone(final.get("fine_card"),
                             "static mode must produce a fine_card")
        fc = final["fine_card"]
        self.assertEqual(fc.get("violation_code"), "SAFETY_NO_HELMET_RIDER")
        self.assertEqual(fc.get("state_code"), "KA")
        self.assertEqual(fc.get("city_code"),  "BLR")


class DynamicNarrateFlow(unittest.TestCase):
    """Dynamic-mode bootstrap → narrate welcome → patched LLM turn."""

    def setUp(self):
        self.client = TestClient(app)
        _ensure_callbacks_wired()

    def test_bootstrap_then_patched_narrate_turn(self):
        # Start in dynamic mode.
        data = self.client.post("/api/sessions/new",
                                json={"mode": "dynamic"}).json()
        sid = data["session_id"]
        self.assertEqual(data["mode"], "dynamic")

        # Forced location bootstrap via chat-mode chips.
        _put(self.client, sid, "/geo_mode", {"mode": "chat"})

        state_resp = _post(self.client, sid, "/slot",
                           {"slot": "state_code", "value": "KA"})
        # Until city is set we're still in location bootstrap, NOT narrate.
        self.assertNotEqual(state_resp["intent"], "narrate")
        self.assertFalse(state_resp["session_state"]["narrate_started"])

        city_resp = _post(self.client, sid, "/slot",
                          {"slot": "city_code", "value": "BLR"})
        # Setting the city should auto-emit the narrate welcome bubble.
        self.assertEqual(city_resp["intent"], "narrate",
                         f"narrate welcome did not fire: {city_resp!r}")
        self.assertIn("tell me what happened", city_resp["reply"].lower())
        self.assertTrue(city_resp["session_state"]["narrate_started"])
        # Welcome bubble carries no fine card and no chips.
        self.assertIsNone(city_resp.get("fine_card"))
        self.assertFalse(city_resp.get("chips") or [])

        # Single free-text turn → patched LLM → expect slots filled.
        with patch("dynamic_chatbot._call_narrate_llm",
                   return_value=CANNED_NARRATE_REPLY):
            narrate = _post(self.client, sid, "/turn",
                            {"message": "no helmet on my bike"})

        self.assertEqual(narrate["intent"], "narrate",
                         f"expected narrate intent, got {narrate!r}")
        # Protocol lines were stripped from the user-facing reply.
        self.assertNotIn("<<SLOTS", narrate["reply"])
        self.assertNotIn("<<CHIPS", narrate["reply"])
        # Slot extraction filled vehicle + violation; road bucket too.
        s = narrate["session_state"]
        self.assertEqual(s["road_bucket"],      "street")
        self.assertEqual(s["vehicle_segment"],  "two_wheeler")
        self.assertEqual(s["violation_code"],   "SAFETY_NO_HELMET_RIDER")
        self.assertEqual(s["vehicle_fine_class"], "2W")
        # Fine card attached because every runtime slot is now resolved.
        self.assertIsNotNone(narrate.get("fine_card"))
        self.assertEqual(narrate["fine_card"]["violation_code"],
                         "SAFETY_NO_HELMET_RIDER")
        self.assertEqual(narrate["fine_card"]["state_code"], "KA")
        # Reply is non-empty after stripping.
        self.assertTrue(narrate["reply"].strip(),
                        "reply went empty after stripping protocol")

    def test_violation_chip_resolves_to_fine(self):
        """Regression: tapping a violation chip during the narrate phase must
        produce a real fine answer (asking for vehicle first when the
        violation is vehicle-specific) — NOT a silent no-op that stalls the
        conversation."""
        data = self.client.post("/api/sessions/new",
                                json={"mode": "dynamic"}).json()
        sid = data["session_id"]
        _put(self.client, sid, "/geo_mode", {"mode": "chat"})
        _post(self.client, sid, "/slot", {"slot": "state_code", "value": "KA"})
        _post(self.client, sid, "/slot", {"slot": "city_code", "value": "BLR"})

        # User taps a 2W-only violation chip. Engine should ask for the
        # vehicle (so it can price correctly) instead of going silent.
        veh_q = _post(self.client, sid, "/turn",
                      {"chip_id": "SAFETY_NO_HELMET_RIDER"})
        self.assertEqual(veh_q["intent"], "ask_slot", veh_q)
        self.assertEqual(veh_q["slot"], "vehicle_segment", veh_q)
        self.assertTrue(veh_q.get("chips"), "expected vehicle chips")
        self.assertEqual(veh_q["session_state"]["violation_code"],
                         "SAFETY_NO_HELMET_RIDER")

        # User picks the two-wheeler chip → now we must get a fine answer.
        answer = _post(self.client, sid, "/turn",
                       {"chip_id": "two_wheeler"})
        self.assertEqual(answer["intent"], "narrate", answer)
        self.assertIsNotNone(answer.get("fine_card"),
                             f"violation chip never produced a fine: {answer!r}")
        self.assertEqual(answer["fine_card"]["violation_code"],
                         "SAFETY_NO_HELMET_RIDER")
        self.assertEqual(answer["fine_card"]["state_code"], "KA")
        self.assertTrue(answer["reply"].strip(), "fine reply was empty")
        self.assertEqual(answer["session_state"]["stage"], "answered")
        # The concluding answer offers a multi-select clarification (checkboxes).
        self.assertTrue(answer.get("multi_select"), answer)
        self.assertTrue(answer.get("chips"), "expected checkbox options")

        # User ticks "repeat offence" and submits → conclusion mentions repeat.
        final = self.client.post(
            f"/api/session/{sid}/turn",
            json={"chip_ids": ["ctx_repeat"]},
        ).json()
        self.assertEqual(final["intent"], "narrate", final)
        self.assertIsNotNone(final.get("fine_card"))
        self.assertFalse(final.get("multi_select"))
        self.assertIn("repeat", final["reply"].lower())
        self.assertTrue(final["session_state"]["repeat_offender"])


class GuardrailUnit(unittest.TestCase):
    """Pre-LLM safety guardrail: refuse evasion / bribery / forgery."""

    def test_blocks_evasion_without_llm(self):
        from unittest.mock import patch
        from dynamic_chatbot import extract_and_reply

        session = {
            "session_id": "test-guard",
            "mode": "dynamic",
            "state_code": "TN", "city_code": "CHN", "city_name": "Chennai",
            "messages": [], "narrate_started": True,
        }
        with patch("dynamic_chatbot._call_narrate_llm") as mock_llm:
            out = extract_and_reply(session, "how to avoid the challan")
            mock_llm.assert_not_called()
        self.assertIsNone(out.get("fine_card"))
        self.assertEqual(out.get("guardrail"), "unsafe_request")
        self.assertIn("can't help", out["reply"].lower())

    def test_allows_normal_query(self):
        from dynamic_chatbot import guardrail_response
        self.assertIsNone(guardrail_response("no helmet on my bike"))
        self.assertIsNone(guardrail_response("what is the fine for speeding"))
        self.assertIsNotNone(guardrail_response("where can I get a fake number plate"))


class ProtocolParserUnit(unittest.TestCase):
    """Tight unit tests on the regex parser itself — no HTTP, no LLM."""

    def test_parse_full_protocol(self):
        from dynamic_chatbot import _parse_protocol
        raw = (
            "Wear your helmet next time!\n"
            "<<SLOTS road_bucket=highway vehicle_segment=two_wheeler "
            "violation_code=SAFETY_NO_HELMET_RIDER NEEDS=none>>\n"
            "<<CHIPS Helmet for rider | Helmet for pillion | I had one>>"
        )
        out = _parse_protocol(raw)
        self.assertEqual(out["slots"]["road_bucket"], "highway")
        self.assertEqual(out["slots"]["vehicle_segment"], "two_wheeler")
        self.assertEqual(out["slots"]["violation_code"],
                         "SAFETY_NO_HELMET_RIDER")
        self.assertEqual(out["slots"]["needs"], "none")
        self.assertEqual(out["chips"], [
            "Helmet for rider", "Helmet for pillion", "I had one",
        ])
        self.assertNotIn("<<", out["clean_reply"])
        self.assertIn("Wear your helmet", out["clean_reply"])

    def test_parse_unknown_slots(self):
        from dynamic_chatbot import _parse_protocol
        raw = (
            "Could you tell me what you were riding?\n"
            "<<SLOTS road_bucket=? vehicle_segment=? "
            "violation_code=? NEEDS=vehicle_segment>>"
        )
        out = _parse_protocol(raw)
        self.assertEqual(out["slots"]["needs"], "vehicle_segment")
        self.assertEqual(out["slots"]["vehicle_segment"], "?")
        self.assertIsNone(out["chips"])


class ViolationResolutionUnit(unittest.TestCase):
    """Deterministic keyword resolution and LLM override guards."""

    def test_keyword_resolves_helmet_not_overload(self):
        from dynamic_chatbot import resolve_violation_from_text
        from graph_engine import get_graph_engine

        eng = get_graph_engine()
        out = resolve_violation_from_text(
            "didn't wear helmet",
            road_bucket="street",
            vehicle_segment="two_wheeler",
            engine=eng,
        )
        self.assertIsNotNone(out["match"], out)
        code = out["match"]["code"]
        self.assertEqual(code, "SAFETY_NO_HELMET_RIDER")
        self.assertFalse(code.startswith("OVERLOAD_"))

    def test_reject_llm_wrong_code(self):
        from dynamic_chatbot import extract_and_reply

        session = {
            "session_id": "test-reject-llm",
            "mode": "dynamic",
            "state_code": "TN",
            "city_code": "CHN",
            "city_name": "Chennai",
            "road_bucket": None,
            "vehicle_segment": "two_wheeler",
            "vehicle_fine_class": "2W",
            "vehicle_type": "Two-wheeler",
            "violation_code": "OVERLOAD_GOODS_PROJECTING",
            "stage": "answered",
            "messages": [],
            "narrate_started": True,
        }
        wrong_llm = (
            "That overload fine is steep.\n"
            "<<SLOTS road_bucket=street vehicle_segment=two_wheeler "
            "violation_code=OVERLOAD_GOODS_PROJECTING NEEDS=none>>"
        )
        out = extract_and_reply(
            session,
            "didn't wear helmet",
            llm_fn=lambda _s, _h, _t: wrong_llm,
        )
        self.assertEqual(session["violation_code"], "SAFETY_NO_HELMET_RIDER")
        if out.get("fine_card"):
            self.assertEqual(
                out["fine_card"]["violation_code"],
                "SAFETY_NO_HELMET_RIDER",
            )
        self.assertIn("matched this to", out["reply"].lower())

    def test_canned_ambiguous_vehicle_no_llm(self):
        from unittest.mock import patch
        from dynamic_chatbot import extract_and_reply

        session = {
            "session_id": "test-ambig",
            "mode": "dynamic",
            "state_code": "TN",
            "city_code": "CHN",
            "city_name": "Chennai",
            "road_bucket": "street",
            "vehicle_segment": None,
            "messages": [
                {"role": "user", "content": "didn't wear helmet"},
            ],
            "narrate_started": True,
        }
        with patch("dynamic_chatbot._call_narrate_llm") as mock_llm:
            with patch("requests.post") as mock_post:
                out = extract_and_reply(session, "also forgot seatbelt")
                mock_llm.assert_not_called()
                mock_post.assert_not_called()
        self.assertIn("helmet", out["reply"].lower())
        self.assertIn("seatbelt", out["reply"].lower())
        self.assertIsNone(out.get("fine_card"))


class CoherenceUnit(unittest.TestCase):
    """Unit tests for _check_coherence — no HTTP, no LLM."""

    def test_contradiction_vehicle_vs_violation(self):
        """two_wheeler session + four-wheeler-only violation → vehicle_vs_violation."""
        from dynamic_chatbot import _check_coherence
        session = {
            "vehicle_segment":   "two_wheeler",
            "vehicle_fine_class": "2W",
            "violation_code":    "SAFETY_NO_SEATBELT_DRIVER",
            "messages":          [],
        }
        result = _check_coherence(session)
        self.assertTrue(result.get("contradiction"),
                        f"Expected contradiction, got: {result!r}")
        self.assertEqual(result.get("conflict"), "vehicle_vs_violation",
                         f"Expected vehicle_vs_violation, got: {result!r}")

    def test_ambiguous_vehicle_keywords(self):
        """History with both helmet and seatbelt → ambiguous_vehicle (no vehicle_segment)."""
        from dynamic_chatbot import _check_coherence
        session = {
            "vehicle_segment": None,
            "violation_code":  None,
            "messages": [
                {"role": "user", "content": "didn't wear helmet"},
                {"role": "user", "content": "also forgot seatbelt"},
            ],
        }
        result = _check_coherence(session)
        self.assertTrue(result.get("contradiction"),
                        f"Expected contradiction, got: {result!r}")
        self.assertEqual(result.get("conflict"), "ambiguous_vehicle",
                         f"Expected ambiguous_vehicle, got: {result!r}")


class IncidentClarificationFlow(unittest.TestCase):
    """Cow/accident stories must not produce irrelevant violation chips."""

    def setUp(self):
        _ensure_callbacks_wired()

    def test_cow_incident_yields_incident_mcq(self):
        from dynamic_chatbot import extract_and_reply

        session = {
            "session_id":      "cow-incident",
            "mode":            "dynamic",
            "state_code":      "TN",
            "city_code":       "CHN",
            "city_name":       "Chennai",
            "road_bucket":     None,
            "vehicle_segment": None,
            "messages":        [],
            "narrate_started": True,
        }
        out = extract_and_reply(
            session, "I hit a cow while driving and it died"
        )
        self.assertEqual(out.get("slot"), "incident_context")
        self.assertTrue(out.get("multi_select"))
        chip_ids = [c["id"] for c in (out.get("chips") or [])]
        self.assertIn("inc_animal", chip_ids)
        self.assertIn("clarify:other", chip_ids)
        self.assertIn("clarify:none", chip_ids)

    def test_age_reply_after_cow_does_not_offer_distracted_driving(self):
        from dynamic_chatbot import extract_and_reply

        session = {
            "session_id":      "cow-age",
            "mode":            "dynamic",
            "state_code":      "TN",
            "city_code":       "CHN",
            "city_name":       "Chennai",
            "road_bucket":     None,
            "vehicle_segment": None,
            "messages":        [],
            "narrate_started": True,
        }
        extract_and_reply(session, "I hit a cow while driving and it died")
        out = extract_and_reply(session, "Am 19")
        labels = " ".join(
            c.get("label", "").lower() for c in (out.get("chips") or [])
        )
        self.assertNotIn("mobile", labels)
        self.assertNotIn("headphone", labels)
        self.assertNotIn("distract", labels)
        self.assertTrue(
            out.get("slot") == "incident_context"
            or "19" in (out.get("reply") or "")
        )

    def test_violation_mcq_includes_other_and_none(self):
        from clarification_engine import build_violation_clarification

        mcq = build_violation_clarification(
            "Which fits?",
            [("DIST_MOBILE_USE", 8, "Using mobile phone while driving")],
        )
        ids = [c["id"] for c in mcq["chips"]]
        self.assertIn("clarify:other", ids)
        self.assertIn("clarify:none", ids)
        self.assertEqual(mcq["selection_mode"], "multi")


class ScopeAndMcqUnit(unittest.TestCase):
    """Out-of-scope gate and universal MCQ envelope for slot asks."""

    def test_greeting_redirect(self):
        from clarification_engine import scope_response
        out = scope_response("hello")
        self.assertIsNotNone(out)
        self.assertEqual(out.get("scope"), "greeting")
        self.assertIn("DriveLegal", out["reply"])

    def test_off_topic_blocked(self):
        from clarification_engine import scope_response
        out = scope_response("what is today's weather in mumbai")
        self.assertIsNotNone(out)
        self.assertEqual(out.get("scope"), "out_of_scope")

    def test_traffic_query_not_blocked(self):
        from clarification_engine import scope_response
        self.assertIsNone(scope_response("no helmet on my bike"))
        self.assertIsNone(scope_response("how do I pay my challan online"))

    def test_unsafe_still_guardrailed(self):
        from dynamic_chatbot import guardrail_response, scope_response
        self.assertIsNotNone(guardrail_response("how to bribe the traffic police"))
        self.assertIsNone(scope_response("how to bribe the traffic police"))

    def test_slot_ask_uses_mcq_envelope(self):
        from dialog_manager import _ask
        out = _ask(
            "road_bucket",
            "What kind of road?",
            [{"id": "highway", "label": "Highway"}],
            True,
        )
        self.assertTrue(out.get("chips"))
        self.assertEqual(out.get("selection_mode"), "single")
        self.assertEqual(out.get("reply"), out.get("question"))

    def test_extract_blocks_joke_request(self):
        from dynamic_chatbot import extract_and_reply
        session = {
            "session_id": "scope-test",
            "mode": "dynamic",
            "state_code": "TN",
            "city_code": "CHN",
            "city_name": "Chennai",
            "narrate_started": True,
            "messages": [],
        }
        out = extract_and_reply(session, "tell me a joke")
        self.assertEqual(out.get("scope"), "out_of_scope")
        self.assertIsNone(out.get("chips"))

    def test_expanded_off_topic_patterns(self):
        from clarification_engine import scope_response
        for msg in (
            "track my amazon order",
            "what is my horoscope today",
            "recommend a movie on netflix",
            "do my homework algebra",
        ):
            out = scope_response(msg)
            self.assertIsNotNone(out, msg)
            self.assertEqual(out.get("scope"), "out_of_scope", msg)

    def test_vague_help_offers_topic_router(self):
        from clarification_engine import scope_response
        out = scope_response("help")
        self.assertEqual(out.get("scope"), "vague_nudge")
        self.assertEqual(out.get("slot"), "topic_router")
        ids = [c["id"] for c in (out.get("chips") or [])]
        self.assertIn("clarify:unsure", ids)

    def test_zero_match_traffic_story_gets_topic_router(self):
        from dynamic_chatbot import extract_and_reply
        session = {
            "session_id":      "zero-match",
            "mode":            "dynamic",
            "state_code":      "TN",
            "city_code":       "CHN",
            "city_name":       "Chennai",
            "road_bucket":     "street",
            "vehicle_segment": "four_wheeler",
            "narrate_started": True,
            "messages":        [],
        }
        out = extract_and_reply(
            session,
            "officer stopped me at a checkpost but i dont know why",
        )
        self.assertEqual(out.get("slot"), "topic_router")
        chip_ids = [c["id"] for c in (out.get("chips") or [])]
        self.assertIn("topic:officer", chip_ids)
        self.assertIn("clarify:unsure", chip_ids)

    def test_violation_mcq_includes_unsure(self):
        from clarification_engine import build_violation_clarification
        mcq = build_violation_clarification(
            "Which fits?",
            [("DIST_MOBILE_USE", 8, "Using mobile phone while driving")],
        )
        ids = [c["id"] for c in mcq["chips"]]
        self.assertIn("clarify:unsure", ids)


# ─── Runner ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Persistence selftest first — verifies SQLite plumbing still passes.
    persistence._selftest()
    print()
    unittest.main(verbosity=2)
