"""Scenario Engine tests — offline (rules extractor), no network. Run: python3 backend/tests/test_scenario_engine.py"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("SESSION_SECRET", "test")

from scenario.engine import analyse_text, maybe_handle          # noqa: E402
from scenario.compose import compose                            # noqa: E402


def findings(text):
    out = analyse_text(text, state_code="TN", city_code="CHN")
    rows = {}
    for p in out["result"]["persons"]:
        for f in p["findings"]:
            rows.setdefault(p["actor"].get("relation") or p["actor"].get("label"), []).append((f["code"], f["certainty"]))
    return out, rows


def test_juvenile_charges_guardian():
    out, rows = findings("My 16 year old son drove my car without a licence and jumped a red light")
    me = {c for c, _ in rows.get("self", [])}
    assert "SIGNAL_RED_LIGHT_JUMPING" in me or "DOC_UNDERAGE" in me, rows       # s.199A: guardian/owner is charged
    assert any(f.get("deemed") or f.get("juvenile") for p in out["result"]["persons"] for f in p["findings"])


def test_victim_not_charged():
    out, rows = findings("The share auto driver jumped the signal and hit my mother who was crossing the road, she broke her leg")
    assert "SIGNAL_RED_LIGHT_JUMPING" in {c for c, _ in rows.get("other", [])}, rows
    assert "mother" not in rows or not rows["mother"], rows          # the hit pedestrian carries no driver duties


def test_cycle_rider_is_not_underage_driver():
    out, rows = findings("My son is 13, he was riding his cycle and a car hit him and drove away")
    assert not any(c == "DOC_UNDERAGE" for v in rows.values() for c, _ in v), rows
    assert "ACC_HIT_AND_RUN" in {c for c, _ in rows.get("other", [])}, rows


def test_question_only_when_it_changes_outcome():
    out, _ = findings("My friend drove my bike and got caught")
    q = out["question"]
    assert q is None or q["fact"] in ("actor.licence", "actor.age", "owner.permitted", "event.outcome"), q


def test_grounded_amounts():
    out, _ = findings("I was riding without a helmet and my friend on the back also had no helmet")
    reply, payload = compose(out["result"], city_name="Chennai", question=out["question"], asks=[])
    assert payload["people"] and payload["head"] is not None and "tail" in payload
    import re
    allowed = {int(f["fine_first"]) for p in out["result"]["persons"] for f in p["findings"] if f.get("fine_first")}
    allowed |= {int(p["total_first"]) for p in out["result"]["persons"] if p.get("total_first")} | {200000, 50000}
    for m in re.findall(r"₹\s?([\d,]+)", reply):
        assert int(m.replace(",", "")) in allowed, (m, allowed)


def test_interactive_correction_and_answer():
    s = {"state_code": "TN", "city_code": "CHN", "city_name": "Chennai", "_force_rules": True, "messages": []}
    t1 = maybe_handle(s, "My son took my bike without asking and hit a car, he has no licence")
    assert t1 and t1["intent"] == "scenario" and t1["scenario"]["people"]
    t2 = maybe_handle(s, "actually he is 19")
    assert t2 and t2["intent"] == "scenario_update"
    assert "Updated" in t2["reply"]


def test_general_question_not_hijacked():
    s = {"state_code": "TN", "city_code": "CHN", "_force_rules": True, "messages": []}
    assert maybe_handle(s, "can the police check my phone?") is None


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:           # noqa: BLE001
                fails += 1
                print("FAIL", name, "->", repr(e)[:300])
    sys.exit(1 if fails else 0)
