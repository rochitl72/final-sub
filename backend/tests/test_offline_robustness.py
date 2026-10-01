#!/usr/bin/env python3
"""
test_offline_robustness.py — regressions for the "works without cloud AI" fixes
===============================================================================
Covers:
  R1  calculator free text never 500s when the cloud LLM is missing / rejects
      the key (falls back to the offline engine) and honours prefer_rules
  R2  follow-ups ("is it compoundable?", "will I go to jail?") answer from the
      last fine card in BOTH modes instead of opening the topic router
  R3  "what if in Bangalore instead?" re-prices the answer in BOTH modes
  R4  bribery guardrail + accident / documents routing in calculator mode
  R5  chatbot rules mode attaches a fine card without a map pin (no road type)
  R6  knowledge graph has no duplicate city per (state, name)

Hermetic: cloud calls are stubbed, so it never touches the network even if a
real .env with keys is present.

    python3 tests/test_offline_robustness.py
"""

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import llm_chatbot  # noqa: E402


def _offline(*_a, **_k):
    raise llm_chatbot.GroqOfflineError("stubbed offline for tests")


llm_chatbot._call_groq = _offline
llm_chatbot._available_models = lambda key: None   # no network in tests

from fastapi.testclient import TestClient  # noqa: E402
import api  # noqa: E402


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(api.app)
        cls.c = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def new_session(self, mode: str) -> str:
        sid = self.c.post("/api/sessions/new", json={"mode": mode}).json()["session_id"]
        self.c.put(f"/api/session/{sid}/geo_mode", json={"mode": "chat"})
        self.c.post(f"/api/session/{sid}/geo/manual",
                    json={"state_code": "TN", "city_code": "CHN"})
        return sid

    def turn(self, sid: str, msg: str, prefer_rules: bool = True) -> dict:
        r = self.c.post(f"/api/session/{sid}/turn",
                        json={"message": msg, "prefer_rules": prefer_rules})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def static_answered(self) -> str:
        sid = self.new_session("static")
        for slot, val in [("road_bucket", "street"),
                          ("vehicle_segment", "two_wheeler"),
                          ("violation_category", "safety_gear"),
                          ("violation_code", "SAFETY_NO_HELMET_RIDER")]:
            out = self.c.post(f"/api/session/{sid}/slot",
                              json={"slot": slot, "value": val}).json()
        self.assertEqual(out["intent"], "answer")
        return sid


class CalculatorMode(_Base):
    def test_r1_no_500_without_cloud(self):
        sid = self.static_answered()
        for pr in (True, False):
            out = self.turn(sid, "tell me something about road rules", prefer_rules=pr)
            self.assertTrue(out.get("reply"))

    def test_r1_bad_key_runtime_error_falls_back(self):
        def _bad_key(*_a, **_k):
            raise RuntimeError("Groq API error 401: invalid key")
        orig, llm_chatbot._call_groq = llm_chatbot._call_groq, _bad_key
        try:
            sid = self.static_answered()
            out = self.turn(sid, "tell me something about road rules", prefer_rules=False)
            self.assertTrue(out.get("reply"))
        finally:
            llm_chatbot._call_groq = orig

    def test_r2_followups(self):
        sid = self.static_answered()
        out = self.turn(sid, "is it compoundable?")
        self.assertIn("compoundable", out["reply"].lower())
        self.assertEqual(out["fine_card"]["violation_code"], "SAFETY_NO_HELMET_RIDER")
        out = self.turn(sid, "will I go to jail?")
        self.assertIn("imprisonment", out["reply"].lower())

    def test_r3_relocation_by_text(self):
        sid = self.static_answered()
        out = self.turn(sid, "what if in bangalore instead?")
        self.assertEqual(out["intent"], "answer_updated")
        self.assertEqual(out["fine_card"]["state_code"], "KA")
        self.assertEqual(out["previous_fine_card"]["state_code"], "TN")

    def test_r4_guardrail_and_routing(self):
        sid = self.static_answered()
        out = self.turn(sid, "how do I bribe the cop?")
        self.assertEqual(out.get("guardrail"), "unsafe_request")
        out = self.turn(sid, "accident happened what do I do")
        self.assertIn("112", out["reply"])
        out = self.turn(sid, "what documents do I need to carry")
        self.assertIn("DigiLocker", out["reply"])

    def test_new_violation_replaces_old(self):
        sid = self.static_answered()
        out = self.turn(sid, "drunk driving in a car")
        self.assertEqual(out["fine_card"]["violation_code"], "IMPAIRED_DRUNK")


class ChatbotMode(_Base):
    def test_r5_card_and_r2_followups(self):
        sid = self.new_session("dynamic")
        out = self.turn(sid, "riding bike without helmet")
        self.assertIsNotNone(out.get("fine_card"), "rules mode must attach a card")
        self.assertEqual(out["fine_card"]["violation_code"], "SAFETY_NO_HELMET_RIDER")
        out = self.turn(sid, "is it compoundable?")
        self.assertNotEqual(out.get("slot"), "topic_router")
        self.assertIn("compoundable", out["reply"].lower())

    def test_r3_relocation_by_text(self):
        sid = self.new_session("dynamic")
        self.turn(sid, "riding bike without helmet")
        out = self.turn(sid, "what if in bangalore instead?")
        self.assertEqual(out["intent"], "answer_updated")
        self.assertEqual(out["fine_card"]["state_code"], "KA")

    def test_followup_skipped_for_accident(self):
        sid = self.new_session("dynamic")
        self.turn(sid, "riding bike without helmet")
        out = self.turn(sid, "accident happened what do I do")
        self.assertEqual(out.get("slot"), "incident_context")


class ResolverAndGates(unittest.TestCase):
    def test_pillion_vs_rider(self):
        from violation_resolver import get_violation_resolver
        r = get_violation_resolver()
        for text, want in [
            ("my friend on the back had no helmet", "SAFETY_NO_HELMET_PILLION"),
            ("pillion had no helmet", "SAFETY_NO_HELMET_PILLION"),
            ("riding without helmet", "SAFETY_NO_HELMET_RIDER"),
        ]:
            ranked = r.resolve(text)
            self.assertEqual(ranked[0][0], want, text)
            self.assertTrue(r.is_deterministic(ranked), text)

    def test_guardrail_and_scope(self):
        from dynamic_chatbot import guardrail_response
        from clarification_engine import scope_response
        self.assertIsNotNone(guardrail_response("how can I get out of paying this challan?"))
        self.assertIsNone(guardrail_response("how to avoid getting a fine next time"))
        self.assertIsNotNone(scope_response("who won the cricket match yesterday"))


class GroqModelSelection(unittest.TestCase):
    def test_skips_retired_and_honours_override(self):
        import os
        orig = llm_chatbot._available_models
        llm_chatbot._available_models = lambda key: ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
        try:
            os.environ.pop("GROQ_CHAT_MODEL", None)
            self.assertEqual(llm_chatbot._resolve_model("k", refresh=True), "openai/gpt-oss-20b")
            os.environ["GROQ_CHAT_MODEL"] = "qwen/qwen3.8-27b"
            self.assertEqual(llm_chatbot._resolve_model("k", refresh=True), "qwen/qwen3.8-27b")
            os.environ["GROQ_CHAT_MODEL"] = "llama-3.1-8b-instant"   # retired
            self.assertEqual(llm_chatbot._resolve_model("k", refresh=True), "openai/gpt-oss-20b")
        finally:
            os.environ.pop("GROQ_CHAT_MODEL", None)
            llm_chatbot._available_models = orig
            llm_chatbot._active_model = None

    def test_reasoning_budget(self):
        p = llm_chatbot._model_params("openai/gpt-oss-20b", 300)
        self.assertEqual(p["reasoning_effort"], "low")
        self.assertGreater(p["max_tokens"], 300)


class UnderstandingUnits(unittest.TestCase):
    """nlu.py / resolver / graph building blocks found by the conversation eval."""

    def test_guardrail_precision(self):
        import nlu
        for t in ["how do I bribe the traffic cop", "where can I get a fake driving licence",
                  "how to avoid getting caught by speed cameras", "how to pass a breathalyser test"]:
            self.assertTrue(nlu.is_unsafe(t), t)
        for t in ["how do I avoid getting a helmet fine next time?", "what happens if I refuse a breath test",
                  "how to avoid accidents"]:
            self.assertFalse(nlu.is_unsafe(t), t)

    def test_age_parsing_is_explicit(self):
        from clarification_engine import parse_age
        self.assertEqual(parse_age("I am 17"), 17)
        self.assertEqual(parse_age("Am 19"), 19)
        self.assertEqual(parse_age("my 16 year old son was driving"), 16)
        self.assertIsNone(parse_age("what is 25 times 4"))
        self.assertIsNone(parse_age("I was going 80 in a 50 zone"))

    def test_vehicle_detection(self):
        from graph_engine import get_graph_engine
        e = get_graph_engine()
        self.assertEqual(e.detect_vehicle_segment("I didn't give way to an ambulance on my bike"), "two_wheeler")
        self.assertEqual(e.detect_vehicle_segment("parked my car at a bus stop"), "four_wheeler")
        self.assertIsNone(e.detect_vehicle_segment("my card got declined"))
        self.assertEqual(e.detect_vehicle_segment("a car hit my bike"), "two_wheeler")

    def test_resolver_typos_and_noise(self):
        from violation_resolver import get_violation_resolver
        r = get_violation_resolver()
        self.assertEqual(r.resolve("no helmt on my scooty")[0][0], "SAFETY_NO_HELMET_RIDER")
        self.assertFalse(r.is_deterministic(r.resolve("so yesterday night I was coming back from a party")))
        self.assertFalse(r.is_deterministic(r.resolve("how much is the fine again?", "street", "2W")))

    def test_followup_masking(self):
        import nlu
        self.assertNotIn("licence", nlu.mask_followup_terms("can they suspend my licence?"))
        self.assertIn("licence", nlu.mask_followup_terms("fine for driving without a licence"))
        self.assertIn("licence", nlu.mask_followup_terms("is the licence one compoundable?"))

    def test_card_text_grounded_and_repeat_filled(self):
        from graph_engine import get_graph_engine, card_amounts, ground_amounts
        e = get_graph_engine()
        card = e.quick_fine("DIST_MOBILE_USE", "TN", "CHN", "LMV")
        self.assertNotIn("5,000", card["what_to_do_next"] or "")      # central amount dropped
        drunk = e.quick_fine("IMPAIRED_DRUNK", "TN", "CHN", "LMV")
        self.assertEqual(drunk["fine_repeat"], 15000)                  # filled from central
        self.assertEqual(drunk["fine_repeat_source"], "central")
        self.assertEqual(ground_amounts("Pay ₹500; then ₹9,999 more.", {500}), "Pay ₹500.")

    def test_llm_lead_sanitised(self):
        import nlu
        lead = nlu.sanitize_lead("Sounds like you ran a red light. The fine is ₹1 000 under §183. "
                                 "What's your age?")
        self.assertEqual(lead, "Sounds like you ran a red light.")
        self.assertTrue(nlu.llm_reply_problems("fine ₹350 under §183(1)(i) PARK_NO_PARKING",
                                               allowed_amounts={300, 500}, allowed_sections=["177"],
                                               session={}))


class GraphData(unittest.TestCase):
    def test_r6_no_duplicate_cities(self):
        g = json.loads((BACKEND.parent / "data" / "compiled" /
                        "drivelegal_graph.json").read_text(encoding="utf-8"))
        cities = [n for n in g["nodes"].values() if n.get("type") == "city"]
        dups = [k for k, v in Counter(
            (c["state_code"], c["name"].strip().lower()) for c in cities).items() if v > 1]
        self.assertEqual(dups, [])
        ids = {f"city:{c['code']}" for c in cities}
        for e in g["edges"]:
            for side in ("src", "dst"):
                if e[side].startswith("city:"):
                    self.assertIn(e[side], ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
