#!/usr/bin/env python3
"""
test_structured_output_v33.py — dual-mode narrate parser (F1)
=============================================================
Verifies _parse_protocol accepts BOTH a JSON object (structured output) and the
legacy <<SLOTS>> text protocol, and that malformed input degrades safely.
Pure functions — no DB, no network.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dynamic_chatbot import _parse_protocol, _try_parse_json_reply  # noqa: E402


def test_json_object_parsed():
    raw = ('{"reply": "The fine is Rs 500.", '
           '"violation_code": "SAFETY_NO_HELMET_RIDER", '
           '"road_bucket": "main_road", "needs": "none", '
           '"chips": ["Bike", "Car"]}')
    p = _parse_protocol(raw)
    assert p["clean_reply"] == "The fine is Rs 500."
    assert p["slots"]["violation_code"] == "SAFETY_NO_HELMET_RIDER"
    assert p["slots"]["road_bucket"] == "main_road"
    assert p["slots"]["needs"] == "none"
    assert p["chips"] == ["Bike", "Car"]


def test_json_fenced_and_nested_slots():
    raw = ('```json\n{"reply": "Hi", "slots": {"needs": "state_code", '
           '"vehicle_segment": "TWO_WHEELER"}}\n```')
    p = _parse_protocol(raw)
    assert p["clean_reply"] == "Hi"
    assert p["slots"]["needs"] == "state_code"
    # non-violation fields are lower-cased, mirroring the regex path
    assert p["slots"]["vehicle_segment"] == "two_wheeler"


def test_legacy_slots_protocol_still_works():
    raw = ("Here's the info you asked for.\n"
           "<<SLOTS road_bucket=highway vehicle_segment=? violation_code=? "
           "driver_age=? has_licence=? licence_type=? repeat_offender=? "
           "NEEDS=state_code>>")
    p = _parse_protocol(raw)
    assert p["clean_reply"] == "Here's the info you asked for."
    assert p["slots"]["road_bucket"] == "highway"
    assert p["slots"]["needs"] == "state_code"


def test_plain_prose_and_malformed_json_degrade_safely():
    # No JSON object, no protocol → returned verbatim, default slots.
    p = _parse_protocol("Just drive safe today.")
    assert p["clean_reply"] == "Just drive safe today."
    assert p["slots"]["needs"] == "none"
    # Broken JSON (no valid object / no reply key) must fall back, not crash.
    assert _try_parse_json_reply('{reply: not valid json') is None
    assert _try_parse_json_reply('{"foo": 1}') is None
    assert _try_parse_json_reply("no braces here") is None


if __name__ == "__main__":
    test_json_object_parsed()
    test_json_fenced_and_nested_slots()
    test_legacy_slots_protocol_still_works()
    test_plain_prose_and_malformed_json_degrade_safely()
    print("structured-output parser: OK")
