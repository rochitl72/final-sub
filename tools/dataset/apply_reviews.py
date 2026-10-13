#!/usr/bin/env python3
"""
Apply completed human reviews back into the dataset.

This is the one path by which a value becomes servable again, so it is strict on
purpose. A reviewer's verdict is rejected unless it carries a source URL and
names the instrument — the failure this project is recovering from is a dataset
of confident numbers with no provenance, and a review process that can restore
one is not a fix.

  confirm        the stored value is right      -> needs source_url + instrument
  correct        the stored value is wrong      -> needs corrected_value too
  reject         no such rule / not supportable -> stays withheld, marked settled
  cannot_source  looked, found nothing          -> stays withheld, marked settled

Dry-run by default. Pass --write to modify the serving graph.
"""
import json, csv, sys, argparse, datetime, re, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVING = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"
QUEUE = ROOT / "data" / "audit" / "review_queue.csv"
LOG = ROOT / "data" / "audit" / "review_log.jsonl"

VERDICTS = {"confirm", "correct", "reject", "cannot_source"}
NEEDS_SOURCE = {"confirm", "correct"}
URL_RE = re.compile(r"^https?://[^\s]+$")

ap = argparse.ArgumentParser()
ap.add_argument("--write", action="store_true", help="apply changes (default: dry run)")
ap.add_argument("--queue", default=str(QUEUE))
args = ap.parse_args()

rows = list(csv.DictReader(Path(args.queue).open(encoding="utf-8")))
filled = [r for r in rows if (r.get("verdict") or "").strip()]
if not filled:
    print(f"No verdicts filled in {args.queue}. Nothing to apply.")
    sys.exit(0)

G = json.loads(SERVING.read_text())
NODES = G["nodes"]
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

accepted, rejected, applied = [], [], collections.Counter()

for r in filled:
    v = r["verdict"].strip().lower()
    nid, field = r["node_id"], r["field"]
    def bad(why):
        rejected.append((r.get("priority"), nid, field, why))

    if v not in VERDICTS:
        bad(f"unknown verdict {v!r}; expected one of {sorted(VERDICTS)}")
        continue
    if nid not in NODES:
        bad("node no longer exists in the serving graph")
        continue
    if not (r.get("reviewer") or "").strip():
        bad("no reviewer recorded — every verdict must be attributable")
        continue

    if v in NEEDS_SOURCE:
        url = (r.get("source_url") or "").strip()
        inst = (r.get("instrument") or "").strip()
        if not URL_RE.match(url):
            bad("source_url missing or not a URL — a value cannot be served "
                "without something a citizen can check")
            continue
        if not inst:
            bad("instrument not named (e.g. 'MV Act 1988 s.183' or "
                "'Karnataka Gazette notification No. ...')")
            continue
        if v == "correct" and not (r.get("corrected_value") or "").strip():
            bad("verdict 'correct' needs corrected_value")
            continue
    accepted.append((r, v))

print(f"reviews found: {len(filled)}   accepted: {len(accepted)}   "
      f"rejected: {len(rejected)}\n")
for pr, nid, field, why in rejected[:25]:
    print(f"  REJECT #{pr} {nid}.{field}: {why}")
if len(rejected) > 25:
    print(f"  ... and {len(rejected)-25} more")

if not args.write:
    print(f"\nDRY RUN — nothing written. Re-run with --write to apply "
          f"{len(accepted)} review(s).")
    sys.exit(1 if rejected else 0)

log = LOG.open("a", encoding="utf-8")
for r, v in accepted:
    nid, field = r["node_id"], r["field"]
    node = NODES[nid]
    fields = node.setdefault("_verification", {}).setdefault("fields", {})
    entry = fields.setdefault(field, {})
    prev = dict(entry)

    if v in NEEDS_SOURCE:
        if v == "correct":
            raw = r["corrected_value"].strip()
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = int(raw) if raw.isdigit() else raw
            withheld = (node.get("_withheld") or {}).pop(field, None)
            node[field] = value
            # keep the normalised copy in step, or the gate is bypassed again
            if field in ("first_offence", "repeat_offence") and isinstance(value, int):
                key = "fine_first" if field == "first_offence" else "fine_repeat"
                node[key] = {"amount_inr": value, "max_inr": None, "basis": "flat",
                             "per_unit": None, "currency": "INR", "raw": value,
                             "resolved": True, "note": f"corrected by review {NOW}"}
            entry["previous_value"] = (withheld or {}).get("value")
        else:  # confirm — restore the withheld value unchanged
            withheld = (node.get("_withheld") or {}).pop(field, None)
            if withheld is not None:
                node[field] = withheld["value"]
                if field in ("first_offence", "repeat_offence") and isinstance(
                        withheld["value"], int):
                    key = "fine_first" if field == "first_offence" else "fine_repeat"
                    node[key] = {"amount_inr": withheld["value"], "max_inr": None,
                                 "basis": "flat", "per_unit": None, "currency": "INR",
                                 "raw": withheld["value"], "resolved": True,
                                 "note": f"confirmed by review {NOW}"}
        entry.update(status="verified", confidence=0.95,
                     source_url=r["source_url"].strip(),
                     source_title=r["instrument"].strip(),
                     retrieved_at=r.get("reviewed_at") or NOW,
                     note=(f"Human review by {r['reviewer'].strip()}: {v}. "
                           f"Instrument: {r['instrument'].strip()}. "
                           f"{r.get('notes','').strip()}").strip(),
                     reviewed_by=r["reviewer"].strip(),
                     effective_from=(r.get("notification_date") or "").strip() or None)
        applied[v] += 1
    else:
        entry.update(status=("rejected" if v == "reject" else "cannot_source"),
                     note=(f"Human review by {r['reviewer'].strip()}: {v}. "
                           f"{r.get('notes','').strip()}").strip(),
                     reviewed_by=r["reviewer"].strip(), settled_at=NOW)
        applied[v] += 1

    log.write(json.dumps({"at": NOW, "node_id": nid, "field": field, "verdict": v,
                          "reviewer": r["reviewer"].strip(),
                          "source_url": r.get("source_url"),
                          "instrument": r.get("instrument"),
                          "before": prev, "after": dict(entry)}) + "\n")
log.close()

# refresh node-level rollups
for n in NODES.values():
    f = (n.get("_verification") or {}).get("fields") or {}
    if not f:
        continue
    counts = collections.Counter(x.get("status") for x in f.values())
    n["_verification"]["breakdown"] = dict(counts)
    n["_verification"]["verified_fraction"] = round(
        counts.get("verified", 0) / len(f), 3)
    n["_serveable"] = counts.get("verified", 0) > 0
    n["_withheld_count"] = len(n.get("_withheld") or {})

G["meta"]["verification"]["last_review_applied_at"] = NOW
SERVING.write_text(json.dumps(G, ensure_ascii=False, indent=1))

print(f"\napplied: {dict(applied)}")
print(f"log appended to {LOG.relative_to(ROOT)}")
print("\nNow re-run, in order:")
print("  python3 tools/dataset/compile_sqlite.py")
print("  python3 scripts/build_offline_bundle.py")
print("  python3 tools/dataset/ci_gate.py")
sys.exit(1 if rejected else 0)
