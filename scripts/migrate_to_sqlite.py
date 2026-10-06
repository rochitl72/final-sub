#!/usr/bin/env python3
"""
migrate_to_sqlite.py — one-off move of the v3 JSON graph into data/drivelegal.db
================================================================================
Reads data/compiled/drivelegal_graph.json, writes every node / edge into the
SQLite schema in backend/legal_db.py, then proves the round trip: the graph
exported back from the DB must equal the input (same nodes, edges, spatial,
indexes, meta). Refuses to overwrite an existing DB unless --force.

    python3 scripts/migrate_to_sqlite.py [--force]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import legal_db  # noqa: E402

SRC = ROOT / "data" / "compiled" / "drivelegal_graph.json"
GEO_TYPES = {"state", "city", "district", "road_class", "corridor", "road_point"}


def migrate(src: Path, dst: Path) -> None:
    g = json.loads(src.read_text(encoding="utf-8"))
    if dst.exists():
        dst.unlink()
    conn = legal_db.connect(dst)
    legal_db.create_schema(conn)
    cur = conn.cursor()

    cur.execute("INSERT INTO source(key,title,url,retrieved,note) VALUES(?,?,?,?,?)",
                ("graph_v3", "DriveLegal Graph v3 (compiled MV Act 2019 + state schedules)",
                 None, None, "Migrated from data/compiled/drivelegal_graph.json"))
    graph_src = cur.lastrowid

    for i, (nid, n) in enumerate(g["nodes"].items()):
        t = n.get("type")
        if t in GEO_TYPES:
            cur.execute("INSERT INTO geo_node(id,ord,type,data) VALUES(?,?,?,?)",
                        (nid, i, t, json.dumps(n, ensure_ascii=False)))
        elif t == "violation":
            cur.execute(f"""INSERT INTO violation(code,ord,{','.join(legal_db._VIO_COLS)})
                            VALUES(?,?,{','.join('?' * len(legal_db._VIO_COLS))})""",
                        [n["code"], i] + [
                            json.dumps(n[c]) if c == "vehicle_applicability"
                            else (None if n[c] is None else int(n[c])) if c == "compoundable"
                            else n[c] for c in legal_db._VIO_COLS])
            for j, kw in enumerate(n.get("keywords") or []):
                cur.execute("INSERT OR IGNORE INTO alias(violation_code,ord,phrase,lang,origin) VALUES(?,?,?,?,?)",
                            (n["code"], j, kw, "en", "graph"))
        elif t == "vehicle":
            cur.execute(f"""INSERT INTO vehicle_class(code,ord,{','.join(legal_db._VEH_COLS)})
                            VALUES(?,?,{','.join('?' * len(legal_db._VEH_COLS))})""",
                        [n["code"], i] + [
                            json.dumps(n[c]) if c == "aliases"
                            else (None if n[c] is None else int(n[c])) if c == "permit_required"
                            else n[c] for c in legal_db._VEH_COLS])
        elif t == "fine":
            cur.execute(f"""INSERT INTO penalty(id,ord,{','.join(legal_db._FINE_COLS)},source_id)
                            VALUES(?,?,{','.join('?' * len(legal_db._FINE_COLS))},?)""",
                        [n["id"], i] + [n[c] for c in legal_db._FINE_COLS] + [graph_src])
        else:
            raise SystemExit(f"unknown node type {t} for {nid}")

    for i, e in enumerate(g["edges"]):
        cur.execute("INSERT INTO edge(ord,src,rel,dst) VALUES(?,?,?,?)", (i, e["src"], e["rel"], e["dst"]))

    legal_db.set_meta(conn, "node_order", list(g["nodes"].keys()))
    legal_db.set_meta(conn, "graph_meta", g["meta"])
    legal_db.set_meta(conn, "spatial", g["spatial"])
    legal_db.set_meta(conn, "veh_aliases", g["indexes"]["veh_aliases"])
    legal_db.set_meta(conn, "keyword_to_vio", g["indexes"]["keyword_to_vio"])
    legal_db.set_meta(conn, "bucket_order", list(g["indexes"]["vio_by_bucket"].keys()))
    legal_db.set_meta(conn, "law_version", 1)
    conn.commit()

    # ── Round trip ─────────────────────────────────────────────────────────
    out = legal_db.export_graph(conn)
    out.pop("law", None)
    problems = []
    for key in ("nodes", "edges", "spatial", "meta"):
        if out[key] != g[key]:
            problems.append(key)
    if list(out["nodes"]) != list(g["nodes"]):
        problems.append("node order")
    for k, v in g["indexes"].items():
        if out["indexes"].get(k) != v:
            problems.append(f"indexes.{k}")
    conn.close()
    if problems:
        raise SystemExit(f"Round-trip mismatch: {problems}")
    print(f"Migrated {len(g['nodes'])} nodes, {len(g['edges'])} edges → {dst} (round trip identical)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if legal_db.DB_PATH.exists() and not a.force:
        raise SystemExit(f"{legal_db.DB_PATH} exists — pass --force to rebuild from JSON")
    migrate(SRC, legal_db.DB_PATH)
