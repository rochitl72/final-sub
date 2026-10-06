#!/usr/bin/env python3
"""
eval_scenarios.py — score the Scenario Engine against the labelled gold set
===========================================================================
Metrics per engine (rules = offline extractor, groq = LLM extractor + rules):

  offence recall      gold 'must' offences found (any certainty, incl. pending a question)
  person attribution  …and found on the right person
  precision           shown 'liable' offences that the gold marks as must / ok
  useful questions    asked question is one the gold accepts (or none, when none needed)
  grounded            every ₹ amount in the reply comes from the computed result

    python3 scripts/eval_scenarios.py                          # rules
    python3 scripts/eval_scenarios.py --engines rules groq     # + Groq (needs GROQ_CHAT_API_KEY)
    python3 scripts/eval_scenarios.py --only G01 --verbose
    python3 scripts/eval_scenarios.py --strict                 # exit 1 below the phase gates
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
GOLD = ROOT / "data" / "law" / "gold_scenarios.json"

GATES = {"rules": {"recall": 0.75, "precision": 0.90, "attribution": 0.75, "questions": 0.80, "grounded": 1.0},
         "groq": {"recall": 0.85, "precision": 0.90, "attribution": 0.85, "questions": 0.80, "grounded": 1.0}}

_RUPEE = re.compile(r"₹\s?(\d[\d,]*)")


def _matches(key: str, actor: dict, vehicle_words: str = "") -> bool:
    if key == "self":
        return actor.get("relation") == "self"
    rel = (actor.get("relation") or "").lower()
    if key == rel:
        return True
    label = f"{actor.get('label', '')} {actor.get('ref', '')} {actor.get('_vw', '')}".lower()
    roles = [r.lower() for r in actor.get("roles") or []]
    words = key.split()
    if all(re.search(rf"\b{re.escape(w)}s?\b", label) for w in words):
        return True
    return len(words) == 1 and key in roles


def score_case(case: dict, out: dict, reply: str) -> dict:
    res = out["result"]
    actors = res["actors"]
    _SEGW = {"heavy_vehicle": "truck lorry tempo", "three_wheeler": "auto share", "four_wheeler_plus": "bus",
             "four_wheeler": "car cab taxi ola uber", "two_wheeler": "bike scooter scooty"}
    for a in actors.values():
        v = res["vehicles"].get(a.get("vehicle") or "") or {}
        a["_vw"] = f"{v.get('label') or ''} {_SEGW.get(v.get('segment') or '', '')}"
    pred = []      # (actor, code, kind)
    for p in res["persons"]:
        for f in p["findings"]:
            kind = "juvenile" if f.get("juvenile") else ("liable" if f["certainty"] == "liable" else "may")
            pred.append((p["actor"], f["code"], kind))
    for f in res.get("conditional", []):
        pred.append((actors.get(f["actor"], {}), f["code"], "conditional"))

    must = [(k, c) for k, codes in case["must"].items() for c in codes]
    ok = {(k, c) for k, codes in case.get("ok", {}).items() for c in codes} | set(must)
    found, found_code = [], []
    for k, c in must:
        on_person = any(code == c and _matches(k, a) for a, code, _ in pred)
        anywhere = any(code == c for _, code, _ in pred)
        found.append(on_person)
        found_code.append(anywhere)
    shown = [(a, c) for a, c, kind in pred if kind == "liable"]
    correct = [any((k, c) in ok and _matches(k, a) for k in {k for k, _ in ok}) for a, c in shown]
    q = (out.get("question") or {}).get("fact")
    accepted = case.get("questions", [])
    if q:
        q_ok = q in accepted
    else:
        q_ok = (not accepted) or ("" in accepted)
    allowed = set()
    for p in res["persons"]:
        for f in p["findings"]:
            for k in ("fine_first", "fine_repeat"):
                if f.get(k):
                    allowed.add(int(f[k]))
            for m in _RUPEE.findall(f.get("imprisonment") or ""):
                allowed.add(int(m.replace(",", "")))
            if p.get("total_first"):
                allowed.add(int(p["total_first"]))
    for f in res.get("conditional", []):
        if f.get("fine_first"):
            allowed.add(int(f["fine_first"]))
    allowed |= {200000, 50000}
    amounts = {int(m.replace(",", "")) for m in _RUPEE.findall(reply)}
    ungrounded = sorted(amounts - allowed)
    return {"must": len(must), "found": sum(found), "found_code": sum(found_code),
            "shown": len(shown), "correct": sum(correct), "q_ok": q_ok, "question": q,
            "ungrounded": ungrounded,
            "missed": [f"{k}:{c}" for (k, c), f_ in zip(must, found) if not f_],
            "wrong": [f"{a.get('label')}:{c}" for (a, c), ok_ in zip(shown, correct) if not ok_]}


def run(engines, only, verbose, pace, out_dir, split="all"):
    from scenario.engine import analyse_text
    from scenario.compose import compose
    import llm_chatbot
    gold = json.loads(GOLD.read_text(encoding="utf-8"))["cases"]
    summary = {}
    report = ["# Scenario Engine — gold-set evaluation", ""]
    for engine in engines:
        if engine == "groq" and not os.environ.get("GROQ_CHAT_API_KEY"):
            print("!! groq requested but GROQ_CHAT_API_KEY missing — skipping")
            continue
        calls = {"n": 0}
        orig = llm_chatbot._call_groq

        def counted(*a, **k):
            calls["n"] += 1
            return orig(*a, **k)
        tot = {"must": 0, "found": 0, "found_code": 0, "shown": 0, "correct": 0, "q_ok": 0, "cases": 0,
               "ungrounded_cases": 0, "latency": 0.0, "llm_used": 0}
        report += [f"## Engine: {engine}", ""]
        for case in gold:
            if only and only not in case["id"]:
                continue
            if split != "all" and case.get("split", "dev") != split:
                continue
            t0 = time.time()
            out = analyse_text(case["text"], state_code="TN", city_code="CHN", use_llm=(engine == "groq"),
                               llm_call=counted if engine == "groq" else None)
            dt = time.time() - t0
            reply, _ = compose(out["result"], city_name="Chennai", question=out["question"],
                               asks=out["scenario"].get("asks") or [])
            s = score_case(case, out, reply)
            tot["cases"] += 1
            for k in ("must", "found", "found_code", "shown", "correct"):
                tot[k] += s[k]
            tot["q_ok"] += int(s["q_ok"])
            tot["ungrounded_cases"] += int(bool(s["ungrounded"]))
            tot["latency"] += dt
            tot["llm_used"] += int(out["scenario"].get("source") == "llm")
            flag = "PASS" if (s["found"] == s["must"] and s["correct"] == s["shown"] and s["q_ok"]
                              and not s["ungrounded"]) else "FAIL"
            line = (f"[{engine:5}] {flag} {case['id']}  found {s['found']}/{s['must']}  "
                    f"precision {s['correct']}/{s['shown']}  q={s['question']}{'' if s['q_ok'] else ' (unexpected)'}")
            if s["missed"]:
                line += f"  missed={s['missed']}"
            if s["wrong"]:
                line += f"  extra={s['wrong']}"
            if s["ungrounded"]:
                line += f"  UNGROUNDED={s['ungrounded']}"
            print(line)
            report.append(f"- {line}")
            if verbose:
                print(reply, "\n")
            if engine == "groq" and pace:
                time.sleep(pace)
        m = {
            "recall": round(tot["found_code"] / max(tot["must"], 1), 3),
            "attribution": round(tot["found"] / max(tot["must"], 1), 3),
            "precision": round(tot["correct"] / max(tot["shown"], 1), 3),
            "questions": round(tot["q_ok"] / max(tot["cases"], 1), 3),
            "grounded": round(1 - tot["ungrounded_cases"] / max(tot["cases"], 1), 3),
            "avg_latency_s": round(tot["latency"] / max(tot["cases"], 1), 2),
            "cases": tot["cases"],
            "llm_extractions": tot["llm_used"], "llm_calls": calls["n"],
        }
        summary[engine] = m
        report += ["", f"**{engine}**: " + json.dumps(m), ""]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scenario_report.md").write_text("\n".join(report), encoding="utf-8")
    (out_dir / "scenario_results.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", nargs="+", default=["rules"])
    ap.add_argument("--only")
    ap.add_argument("--split", choices=["dev", "dev2", "held_out", "held_out2", "all"], default="all")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--pace", type=float, default=12.0, help="seconds between Groq cases (free tier: 8k tokens/min)")
    ap.add_argument("--out", default=str(ROOT / "eval_out"))
    a = ap.parse_args()
    summ = run(a.engines, a.only, a.verbose, a.pace, Path(a.out), a.split)
    if a.strict:
        bad = [(e, k, v, GATES[e][k]) for e, m in summ.items() for k, v in m.items()
               if k in GATES.get(e, {}) and v < GATES[e][k]]
        if bad:
            print("Below gate:", bad)
            sys.exit(1)
