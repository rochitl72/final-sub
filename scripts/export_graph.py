#!/usr/bin/env python3
"""
export_graph.py — DB → data/compiled/drivelegal_graph.json
==========================================================
data/drivelegal.db is the source of truth. This writes the compiled JSON graph
(v3 shape + the `law` block) consumed by the offline bundle builder, the 3D
viewer and any tool that still reads JSON. Run after changing the DB:

    python3 scripts/export_graph.py && python3 scripts/build_offline_bundle.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import legal_db  # noqa: E402

OUT = ROOT / "data" / "compiled" / "drivelegal_graph.json"

if __name__ == "__main__":
    g = legal_db.export_graph()
    OUT.write_text(json.dumps(g, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    c = g["meta"]["counts"]
    print(f"Exported {c['nodes']} nodes, {c['edges']} edges, {c['violations']} violations, "
          f"{sum(len(v) for v in g['law']['liable'].values())} liability rows → {OUT}")
