#!/usr/bin/env python3
"""
1. Parity: the SQLite resolver must agree with the JSON resolver on every
   (violation x state) cell. Offline and online must never disagree about law.
2. Latency: measure the data layer against the LLM call it sits behind.
"""
import json, sqlite3, time, statistics, collections
from pathlib import Path
from resolver import DriveLegal

ROOT = Path(__file__).resolve().parents[2]   # repo root
DB = ROOT / "data" / "compiled" / "drivelegal.sqlite"


class SqliteResolver:
    """Read-only, immutable: no locks, no WAL, safe to share across threads."""

    def __init__(self, path=DB):
        self.con = sqlite3.connect(f"file:{path}?immutable=1", uri=True,
                                   check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.precedence = json.loads(
            self.con.execute("SELECT value FROM meta WHERE key='precedence'")
            .fetchone()[0])

    def fine(self, violation_code, city=None, state=None, vehicle_class=None):
        for level, col, val in (("city", "city_code", city),
                                ("state", "state_code", state),
                                ("central", None, None)):
            if level != "central" and not val:
                continue
            # NOTE: `vehicle_class = ?` with a NULL parameter evaluates to NULL, not
            # true, so a bare equality test silently hides every row that IS scoped
            # to a vehicle class. When the caller supplies no vehicle class we must
            # not filter on it at all. ORDER BY keeps the pick deterministic.
            if level == "central":
                q = ("SELECT * FROM fine WHERE violation_code=? AND level='central' "
                     "AND (?1b IS NULL OR vehicle_class IS NULL OR vehicle_class=?1b) "
                     "ORDER BY fine_id LIMIT 1").replace("?1b", "?")
                r = self.con.execute(
                    q, (violation_code, vehicle_class, vehicle_class)).fetchone()
            else:
                q = (f"SELECT * FROM fine WHERE violation_code=? AND level=? AND {col}=? "
                     "AND (? IS NULL OR vehicle_class IS NULL OR vehicle_class=?) "
                     "ORDER BY fine_id LIMIT 1")
                r = self.con.execute(
                    q, (violation_code, level, val, vehicle_class,
                        vehicle_class)).fetchone()
            if r:
                return {"status": "answer", "amount_inr": r["amount_inr"],
                        "level": r["level"], "source_url": r["source_url"],
                        "basis": r["basis"]}
        return {"status": "no_verified_amount", "amount_inr": None}

    def search(self, text, k=5):
        """Deterministic offline routing — no model, no embeddings."""
        q = " OR ".join(f'"{t}"' for t in text.lower().split() if len(t) > 2)
        if not q:
            return []
        return [dict(r) for r in self.con.execute(
            "SELECT code, name, rank FROM violation_fts WHERE violation_fts MATCH ? "
            "ORDER BY rank LIMIT ?", (q, k))]


js = DriveLegal()
sq = SqliteResolver()

VIO = [n["code"] for n in js.nodes.values() if n.get("type") == "violation"]
ST = [n["code"] for n in js.nodes.values() if n.get("type") == "state"]

# ─────────────────────────────────────────────────────────────────── parity
mismatch, cells = [], 0
for vc in VIO:
    for st in ST:
        cells += 1
        a = js.fine(vc, state=st)
        b = sq.fine(vc, state=st)
        av = a.get("amount_inr") if a.ok else None
        bv = b.get("amount_inr")
        if av != bv:
            mismatch.append((vc, st, av, bv))
print(f"parity: {cells} cells compared, {len(mismatch)} mismatches")
for m in mismatch[:10]:
    print("   ", m)
assert not mismatch, "SQLite and JSON resolvers disagree — offline would differ from online"

# ────────────────────────────────────────────────────────────── latency
def bench(fn, n=2000):
    t = []
    for _ in range(n):
        s = time.perf_counter()
        fn()
        t.append((time.perf_counter() - s) * 1e6)      # microseconds
    t.sort()
    return {"p50": t[n // 2], "p95": t[int(n * .95)], "p99": t[int(n * .99)]}


vc, st = VIO[0], "KA"
r_sq = bench(lambda: sq.fine(vc, state=st))
r_js = bench(lambda: js.fine(vc, state=st))
r_ft = bench(lambda: sq.search("helmet not wearing two wheeler"), 1000)

t0 = time.perf_counter()
SqliteResolver()
open_us = (time.perf_counter() - t0) * 1e6
t0 = time.perf_counter()
json.loads((ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json").read_text())
json_ms = (time.perf_counter() - t0) * 1e3

print(f"\nlookup latency (microseconds)")
print(f"  {'':<26}{'p50':>9}{'p95':>9}{'p99':>9}")
for label, r in (("SQLite fine lookup", r_sq), ("in-memory JSON resolver", r_js),
                 ("SQLite FTS5 search", r_ft)):
    print(f"  {label:<26}{r['p50']:9.1f}{r['p95']:9.1f}{r['p99']:9.1f}")

print(f"\ncold start")
print(f"  open SQLite (immutable)    {open_us:9.1f} us")
print(f"  parse serving JSON         {json_ms*1000:9.1f} us   ({json_ms:.0f} ms)")
print(f"  -> SQLite opens {json_ms*1000/open_us:.0f}x faster on a cold process")

budget = 800_000        # a conservative 800 ms LLM call, in microseconds
print(f"\nshare of an 800 ms LLM response spent in the data layer:")
print(f"  SQLite lookup   {100*r_sq['p99']/budget:.4f} %  (p99)")
print(f"  FTS5 search     {100*r_ft['p99']/budget:.4f} %  (p99)")
print("  -> the data layer is not the latency problem, and cannot be made into one.")

print("\nFTS5 offline routing sample — 'my bike got towed near the hospital':")
for h in sq.search("bike towed parking hospital", 4):
    print(f"   {h['code']:<28} {h['name'][:50]}")
