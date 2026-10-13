#!/usr/bin/env python3
"""
Cross-language parity: the TypeScript offline client must give the same answer
as the Python backend for every (violation x state) cell.

The two implementations are separate ports of the same cascade. Nothing else in
the build would notice if they drifted — and a citizen offline in a tunnel would
be told a different law from one online at the same spot. This closes that.

Runs graph.ts through the project's own tsc, then drives it from node.
"""
import json, subprocess, sys, tempfile, shutil, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOBILE = ROOT / "apps" / "mobile"
TSC = MOBILE / "node_modules" / ".bin" / "tsc"
SERVING = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"

sys.path.insert(0, str(ROOT / "tools" / "dataset"))
from resolver import DriveLegal                                    # noqa: E402

if not TSC.exists():
    print("SKIP: apps/mobile/node_modules not installed; cannot typecheck the "
          "offline client. Run `npm ci` in apps/mobile to include this check.")
    sys.exit(0)

js = DriveLegal()
VIO = sorted(n["code"] for n in js.nodes.values() if n.get("type") == "violation")
ST = sorted(n["code"] for n in js.nodes.values() if n.get("type") == "state")

build = Path(tempfile.mkdtemp(prefix="dl_parity_"))
try:
    # Transpile the offline module (and its JSON bundle) to plain CommonJS.
    src = build / "src"
    shutil.copytree(MOBILE / "src" / "offline", src)
    r = subprocess.run(
        [str(TSC), str(src / "graph.ts"), "--outDir", str(build / "js"),
         "--module", "commonjs", "--target", "es2020", "--resolveJsonModule",
         "--esModuleInterop", "--skipLibCheck"],
        capture_output=True, text=True)
    out_js = next((build / "js").rglob("graph.js"), None)
    if out_js is None:
        print("FAIL: tsc produced no graph.js")
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit(1)

    driver = build / "drive.js"
    driver.write_text(f"""
const g = require({json.dumps(str(out_js))});
const cases = JSON.parse(require('fs').readFileSync({json.dumps(str(build / 'cases.json'))}, 'utf8'));
const out = {{}};
for (const [vc, st] of cases) {{
  const card = g.quickFine(vc, st, null, null);
  out[vc + '|' + st] = card ? {{
    amount: card.fine_first, level: card.answered_at_level,
    state: card.state_code,
    sourced: !!(card.citation && card.citation.source_url),
  }} : null;
}}
require('fs').writeFileSync({json.dumps(str(build / 'ts_out.json'))}, JSON.stringify(out));
""")
    cases = [[vc, st] for vc in VIO for st in ST]
    (build / "cases.json").write_text(json.dumps(cases))
    r2 = subprocess.run(["node", str(driver)], capture_output=True, text=True,
                        cwd=str(build))
    if r2.returncode != 0:
        print("FAIL: node driver errored\n", r2.stderr[-2000:])
        sys.exit(1)
    ts = json.loads((build / "ts_out.json").read_text())
finally:
    shutil.rmtree(build, ignore_errors=True)

mismatch, unsourced, checked = [], [], 0
for vc in VIO:
    for st in ST:
        checked += 1
        py = js.fine(vc, state=st)
        pv = py.get("amount_inr") if py.ok else None
        t = ts.get(f"{vc}|{st}")
        tv = t["amount"] if t else None
        if pv != tv:
            mismatch.append((vc, st, pv, tv))
        if t and not t["sourced"]:
            unsourced.append((vc, st))
        # the TS card must echo the queried state, as the Python one does
        if t and t["state"] != st:
            mismatch.append((vc, st, f"state echo {st}", t["state"]))

print(f"cross-language parity: {checked} cells")
print(f"  python vs typescript mismatches : {len(mismatch)}")
print(f"  offline answers with no source  : {len(unsourced)}")
for m in mismatch[:10]:
    print("   ", m)
for u in unsourced[:5]:
    print("    unsourced:", u)

if mismatch or unsourced:
    sys.exit(1)
print("\nPASS: the offline client and the backend state the same law, and every "
      "offline answer carries a source.")
