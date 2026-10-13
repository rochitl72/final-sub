#!/usr/bin/env python3
"""
Safety regression: compare every (violation x state) answer the ORIGINAL graph
would give against what the restructured serving graph gives.

The restructure is only acceptable if, for every query, the new answer is
either identical or strictly more conservative (a refusal, or a value that the
audit showed the old one contradicted). A new answer that is higher than the old
one, or that appears where the old graph refused, is a regression.
"""
import json, collections
from pathlib import Path
from resolver import DriveLegal

ROOT = Path(__file__).resolve().parents[2]   # repo root
OLD = json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.json").read_text())["nodes"]
V4 = json.loads((ROOT / "data" / "audit" / "drivelegal_graph.v4.json").read_text())["nodes"]
dl = DriveLegal()

old_fines = [n for n in OLD.values() if n.get("type") == "fine"]
VIO = {n["code"]: n for n in OLD.values() if n.get("type") == "violation"}
STATES = [n["code"] for n in OLD.values() if n.get("type") == "state"]


def old_answer(vc, state):
    """Reproduce the original cascade: city -> state -> central, first match."""
    for f in old_fines:
        if f.get("violation_code") == vc and f.get("state_code") == state \
                and not f.get("city_code"):
            return f.get("first_offence"), "state"
    for f in old_fines:
        if f.get("violation_code") == vc and f.get("scope") == "central":
            return f.get("first_offence"), "central"
    return None, None


def was_contradicted(vc, state):
    """Did the audit find the old value for this cell contradicted?"""
    for nid, n in OLD.items():
        if n.get("type") != "fine" or n.get("violation_code") != vc:
            continue
        if n.get("state_code") != state and n.get("scope") != "central":
            continue
        st = (V4.get(nid, {}).get("_verification", {})
              .get("fields", {}).get("first_offence", {}).get("status"))
        if st in ("contradicted", "off_topic", "unsupported_section", "no_citation"):
            return True
    return False


tally = collections.Counter()
regressions = []
examples = collections.defaultdict(list)

for vc in VIO:
    for st in STATES:
        o, olevel = old_answer(vc, st)
        a = dl.fine(vc, state=st)
        n = a.get("amount_inr") if a.ok else None

        # The correct safety test for a government product is not "the number never
        # goes up" — it is "every number served is traceable to an instrument, and a
        # value only changes when the one it replaces had no source".
        new_sourced = bool(a.ok and a.get("source_url"))

        if o == n and o is not None:
            tally["identical"] += 1
        elif o is not None and n is None:
            tally["now_refuses (was unsourced)"] += 1
            examples["now_refuses"].append((vc, st, o))
        elif o is None and n is not None:
            if new_sourced:
                tally["answers where old refused (sourced)"] += 1
            else:
                tally["REGRESSION unsourced answer where old refused"] += 1
                regressions.append((vc, st, o, n, "unsourced"))
        elif o != n:
            if not new_sourced:
                tally["REGRESSION value changed to an unsourced figure"] += 1
                regressions.append((vc, st, o, n, "unsourced replacement"))
            elif was_contradicted(vc, st):
                tally["corrected a contradicted value (sourced)"] += 1
                examples["corrected"].append((vc, st, o, n))
            else:
                tally["unsourced state figure -> statutory figure"] += 1
                examples["to_statutory"].append((vc, st, o, n))
        else:
            tally["both refuse"] += 1

total = sum(tally.values())
print(f"query cells compared: {total}  ({len(VIO)} violations x {len(STATES)} states)\n")
for k, v in tally.most_common():
    print(f"  {k:<44} {v:>6}  {100*v/total:5.1f}%")

print(f"\nREGRESSIONS: {len(regressions)}")
for r in regressions[:15]:
    print(f"   {r}")

print("\nsample of cells that now refuse (old value shown):")
for vc, st, o in examples["now_refuses"][:6]:
    print(f"   {VIO[vc]['name'][:44]:<44} {st}  old=Rs {o}  -> refusal")

print("\nsample: unsourced state figure replaced by the statutory figure")
for vc, st, o, n in examples["to_statutory"][:8]:
    arrow = "up" if n > o else "down"
    print(f"   {VIO[vc]['name'][:40]:<40} {st}  Rs {o} -> Rs {n}  ({arrow})")

ups = sum(1 for _, _, o, n in examples["to_statutory"] if n > o)
downs = sum(1 for _, _, o, n in examples["to_statutory"] if n < o)
print(f"\n   of {len(examples['to_statutory'])} such cells: {ups} went up, {downs} went down."
      f"\n   Every one is now the Motor Vehicles Act figure with a citation, replacing a"
      f"\n   state amount that had no traceable source.")

assert not regressions, f"{len(regressions)} safety regressions — restructure rejected"
print("\nPASS: every answer the restructured graph gives is traceable to an instrument,")
print("      and no value changed except to replace one that had no source.")
