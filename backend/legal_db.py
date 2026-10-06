"""
legal_db.py — SQLite source of truth for DriveLegal's legal knowledge
=====================================================================
`data/drivelegal.db` holds everything the engines reason over:

  Legal layer   provision · violation · violation_provision · alias · example
                penalty (fine schedule) · liable_party · fact
                violation_condition · violation_relation · source
  Reference     vehicle_class · geo_node (state/city/district/road/corridor/
                road_point) · edge · meta

`export_graph()` rebuilds the v3 JSON graph shape (nodes / edges / spatial /
indexes / meta) that graph_engine, the resolver and the on-device bundle have
always consumed, plus a new top-level `law` block (liability, facts,
conditions, relations, provisions) for the scenario engine.

Build / refresh:
    python3 scripts/migrate_to_sqlite.py      # one-off: JSON → DB (done)
    python3 scripts/apply_law_enrichment.py   # upsert data/law/*.json into DB
    python3 scripts/export_graph.py           # DB → data/compiled/drivelegal_graph.json
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "drivelegal.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL                 -- JSON
);

CREATE TABLE IF NOT EXISTS source (
  id        INTEGER PRIMARY KEY,
  key       TEXT UNIQUE NOT NULL,     -- short handle, e.g. 'mva2019', 'bns2023'
  title     TEXT NOT NULL,
  url       TEXT,
  retrieved TEXT,                     -- ISO date the text was checked
  note      TEXT
);

CREATE TABLE IF NOT EXISTS provision (
  id         INTEGER PRIMARY KEY,
  act        TEXT NOT NULL,           -- 'MV Act 1988', 'BNS 2023', 'CMVR 1989'
  section    TEXT NOT NULL,           -- '199A', '106(1)'
  title      TEXT NOT NULL,
  summary    TEXT,
  max_fine   INTEGER,                 -- ₹, when the section itself caps it
  max_jail   TEXT,                    -- 'up to 3 years'
  bailable   INTEGER,                 -- 1/0/NULL (unknown)
  note       TEXT,
  source_id  INTEGER REFERENCES source(id),
  UNIQUE (act, section)
);

CREATE TABLE IF NOT EXISTS violation (
  code                  TEXT PRIMARY KEY,
  ord                   INTEGER NOT NULL,
  name                  TEXT NOT NULL,
  grp                   TEXT NOT NULL,
  mv_section            TEXT,
  cmvr_rule             TEXT,
  description           TEXT,
  compoundable          INTEGER,
  vehicle_applicability TEXT NOT NULL DEFAULT '["ALL"]',   -- JSON list
  what_to_do_next       TEXT,
  tips_to_avoid         TEXT,
  irc_sign_ref          TEXT,
  officer_action        TEXT,
  evidence_req          TEXT,
  consequence           TEXT,
  common_misconception  TEXT,
  severity              TEXT,         -- 'minor' | 'serious' | 'criminal'
  licence_action        TEXT,         -- structured: 'suspend_3m', 'disqualify', …
  road_buckets          TEXT          -- JSON list; NULL = from edges
);

CREATE TABLE IF NOT EXISTS violation_provision (
  violation_code TEXT NOT NULL REFERENCES violation(code),
  provision_id   INTEGER NOT NULL REFERENCES provision(id),
  kind           TEXT NOT NULL DEFAULT 'primary' CHECK (kind IN ('primary','related','aggravated')),
  PRIMARY KEY (violation_code, provision_id)
);

CREATE TABLE IF NOT EXISTS alias (
  violation_code TEXT NOT NULL REFERENCES violation(code),
  ord            INTEGER NOT NULL,
  phrase         TEXT NOT NULL,
  lang           TEXT NOT NULL DEFAULT 'en',
  origin         TEXT NOT NULL DEFAULT 'graph',
  PRIMARY KEY (violation_code, phrase)
);

CREATE TABLE IF NOT EXISTS example (
  violation_code TEXT NOT NULL REFERENCES violation(code),
  text           TEXT NOT NULL,
  lang           TEXT NOT NULL DEFAULT 'en',
  PRIMARY KEY (violation_code, text)
);

CREATE TABLE IF NOT EXISTS vehicle_class (
  code            TEXT PRIMARY KEY,
  ord             INTEGER NOT NULL,
  name            TEXT NOT NULL,
  segment         TEXT,
  subtype         TEXT,
  fine_class      TEXT,
  dl_class        TEXT,
  aliases         TEXT,               -- JSON list
  use             TEXT,
  permit_required INTEGER,
  min_age_years   INTEGER,
  engine_cc_max   INTEGER,
  gvw_kg_max      INTEGER
);

CREATE TABLE IF NOT EXISTS penalty (
  id                INTEGER PRIMARY KEY,   -- = legacy fine:<id>
  ord               INTEGER NOT NULL,
  violation_code    TEXT NOT NULL REFERENCES violation(code),
  scope             TEXT NOT NULL CHECK (scope IN ('central','state','city')),
  state_code        TEXT,
  city_code         TEXT,
  vehicle_class     TEXT,
  first_offence     INTEGER,
  repeat_offence    INTEGER,
  imprisonment      TEXT,
  note              TEXT,
  licence_action    TEXT,
  community_service TEXT,
  source_id         INTEGER REFERENCES source(id)
);
CREATE INDEX IF NOT EXISTS penalty_by_vio ON penalty(violation_code);

CREATE TABLE IF NOT EXISTS liable_party (
  violation_code TEXT NOT NULL REFERENCES violation(code),
  role           TEXT NOT NULL CHECK (role IN ('driver','rider','pillion','passenger',
                   'owner','guardian','conductor','operator','employer','pedestrian','any')),
  basis          TEXT,                -- section that makes this role liable
  certainty      TEXT NOT NULL DEFAULT 'liable' CHECK (certainty IN ('liable','may_be_liable')),
  note           TEXT,
  PRIMARY KEY (violation_code, role)
);

CREATE TABLE IF NOT EXISTS fact (
  name        TEXT PRIMARY KEY,       -- 'actor.age', 'vehicle.segment', 'outcome'
  type        TEXT NOT NULL CHECK (type IN ('int','bool','enum','text')),
  values_json TEXT,                   -- allowed values for enum
  question    TEXT,                   -- how to ask the user
  chips_json  TEXT,                   -- [{"label":..., "value":...}]
  note        TEXT
);

CREATE TABLE IF NOT EXISTS violation_condition (
  id             INTEGER PRIMARY KEY,
  violation_code TEXT NOT NULL REFERENCES violation(code),
  fact           TEXT NOT NULL REFERENCES fact(name),
  op             TEXT NOT NULL CHECK (op IN ('=','!=','<','<=','>','>=','in','not_in')),
  value_json     TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('required','excludes')),
  note           TEXT
);

CREATE TABLE IF NOT EXISTS violation_relation (
  id             INTEGER PRIMARY KEY,
  from_code      TEXT NOT NULL REFERENCES violation(code),
  to_code        TEXT NOT NULL REFERENCES violation(code),
  type           TEXT NOT NULL CHECK (type IN ('implies','subsumes','aggravates','co_charged','alternative')),
  target_role    TEXT,                -- role the implied offence lands on
  condition_json TEXT,                -- {"fact":..,"op":..,"value":..}
  note           TEXT
);

CREATE TABLE IF NOT EXISTS geo_node (
  id    TEXT PRIMARY KEY,             -- 'state:TN', 'city:CHN', …
  ord   INTEGER NOT NULL,
  type  TEXT NOT NULL,
  data  TEXT NOT NULL                 -- JSON (node body)
);

CREATE TABLE IF NOT EXISTS edge (
  ord  INTEGER NOT NULL,
  src  TEXT NOT NULL,
  rel  TEXT NOT NULL,
  dst  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS edge_src ON edge(src);
"""

_VIO_COLS = ["name", "grp", "mv_section", "cmvr_rule", "description", "compoundable",
             "vehicle_applicability", "what_to_do_next", "tips_to_avoid", "irc_sign_ref",
             "officer_action", "evidence_req", "consequence", "common_misconception"]
_VEH_COLS = ["name", "segment", "subtype", "fine_class", "dl_class", "aliases", "use",
             "permit_required", "min_age_years", "engine_cc_max", "gvw_kg_max"]
_FINE_COLS = ["violation_code", "scope", "state_code", "city_code", "vehicle_class",
              "first_offence", "repeat_offence", "imprisonment", "note"]


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(conn, key: str, value) -> None:
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value, ensure_ascii=False)))


def _bool(v):
    return None if v is None else bool(v)


# ── Export: DB → v3 graph dict ───────────────────────────────────────────────

def export_graph(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    own = conn is None
    conn = conn or connect()
    try:
        return _export(conn)
    finally:
        if own:
            conn.close()


def _export(conn: sqlite3.Connection) -> Dict[str, Any]:
    order = _meta(conn, "node_order", [])
    nodes: Dict[str, dict] = {}

    geo = {r["id"]: json.loads(r["data"]) for r in conn.execute("SELECT id,data FROM geo_node ORDER BY ord")}

    vios: Dict[str, dict] = {}
    aliases: Dict[str, List[str]] = {}
    for r in conn.execute("SELECT violation_code, phrase FROM alias ORDER BY violation_code, ord"):
        aliases.setdefault(r["violation_code"], []).append(r["phrase"])
    for r in conn.execute("SELECT * FROM violation ORDER BY ord"):
        body = {"type": "violation", "code": r["code"]}
        for c in _VIO_COLS:
            v = r[c]
            if c == "compoundable":
                v = _bool(v)
            elif c == "vehicle_applicability":
                v = json.loads(v) if v else ["ALL"]
            body[c] = v
        body["keywords"] = aliases.get(r["code"], [])
        # New structured fields only when set (keeps legacy shape byte-identical).
        for c in ("severity", "licence_action"):
            if r[c]:
                body[c] = r[c]
        vios[f"vio:{r['code']}"] = body

    vehs: Dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM vehicle_class ORDER BY ord"):
        body = {"type": "vehicle", "code": r["code"]}
        for c in _VEH_COLS:
            v = r[c]
            if c == "aliases":
                v = json.loads(v) if v else []
            elif c == "permit_required":
                v = _bool(v)
            body[c] = v
        vehs[f"veh:{r['code']}"] = body

    fines: Dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM penalty ORDER BY ord"):
        body = {"type": "fine", "id": r["id"]}
        for c in _FINE_COLS:
            body[c] = r[c]
        fines[f"fine:{r['id']}"] = body

    pool = {**geo, **vios, **vehs, **fines}
    for nid in order:                       # legacy order first (stable iteration)
        if nid in pool:
            nodes[nid] = pool.pop(nid)
    nodes.update(pool)                      # new nodes appended

    edges = [{"src": r["src"], "rel": r["rel"], "dst": r["dst"]}
             for r in conn.execute("SELECT src,rel,dst FROM edge ORDER BY ord")]
    # Edges implied by new rows (penalties / violations added after migration).
    have = {(e["src"], e["rel"], e["dst"]) for e in edges}

    def _add(src, rel, dst):
        if (src, rel, dst) not in have and dst in nodes:
            have.add((src, rel, dst))
            edges.append({"src": src, "rel": rel, "dst": dst})

    for fid, f in fines.items():
        _add(fid, "FOR_VIOLATION", f"vio:{f['violation_code']}")
        if f["state_code"]:
            _add(fid, "IN_STATE", f"state:{f['state_code']}")
        if f["city_code"]:
            _add(fid, "IN_CITY", f"city:{f['city_code']}")
    roads_by_bucket: Dict[str, List[str]] = {}
    for nid, n in nodes.items():
        if n.get("type") == "road_class":
            roads_by_bucket.setdefault(n.get("bucket"), []).append(nid)
    for r in conn.execute("SELECT code, road_buckets FROM violation WHERE road_buckets IS NOT NULL ORDER BY ord"):
        for b in json.loads(r["road_buckets"]):
            for road in roads_by_bucket.get(b, []):
                _add(f"vio:{r['code']}", "APPLIES_ON", road)

    meta = _meta(conn, "graph_meta", {}) or {}
    counts = dict(meta.get("counts", {}))
    counts.update({
        "nodes": len(nodes), "edges": len(edges),
        "violations": len(vios), "fines": len(fines),
    })
    meta["counts"] = counts

    indexes = _build_indexes(conn, nodes, edges)
    graph = {"meta": meta, "nodes": nodes, "edges": edges,
             "spatial": _meta(conn, "spatial", {}), "indexes": indexes}
    graph["law"] = export_law(conn)
    return graph


def _build_indexes(conn, nodes: dict, edges: list) -> dict:
    vio_by_group: Dict[str, List[str]] = {}
    for nid, n in nodes.items():
        if n.get("type") == "violation":
            vio_by_group.setdefault(n["grp"], []).append(nid)

    road_bucket = {nid: n.get("bucket") for nid, n in nodes.items() if n.get("type") == "road_class"}
    vio_by_bucket: Dict[str, List[str]] = {}
    seen = set()
    for e in edges:
        if e["rel"] == "APPLIES_ON" and e["dst"] in road_bucket:
            b = road_bucket[e["dst"]]
            if (b, e["src"]) not in seen:
                seen.add((b, e["src"]))
                vio_by_bucket.setdefault(b, []).append(e["src"])
    bucket_order = _meta(conn, "bucket_order")
    if bucket_order:
        vio_by_bucket = {b: vio_by_bucket.get(b, []) for b in bucket_order} | \
                        {b: v for b, v in vio_by_bucket.items() if b not in bucket_order}

    fbv: Dict[str, dict] = {}
    for nid, n in nodes.items():
        if n.get("type") != "violation":
            continue
        fbv[n["code"]] = {"central": [], "state": {}, "city": {}}
    for nid, f in nodes.items():
        if f.get("type") != "fine":
            continue
        slot = fbv.setdefault(f["violation_code"], {"central": [], "state": {}, "city": {}})
        if f["scope"] == "central":
            slot["central"].append(nid)
        elif f["scope"] == "state":
            slot["state"].setdefault(f["state_code"], []).append(nid)
        else:
            slot["city"].setdefault(f["city_code"], []).append(nid)

    dbs: Dict[str, List[dict]] = {}
    for nid, n in nodes.items():
        if n.get("type") == "district":
            dbs.setdefault(n["state_code"], []).append({
                "id": nid, "code": n["code"], "name": n["name"], "rto_codes": n.get("rto_codes"),
                "traffic_helpline": n.get("traffic_helpline"), "city_override": n.get("city_override")})

    kw = dict(_meta(conn, "keyword_to_vio", {}) or {})
    return {
        "vio_by_group": vio_by_group,
        "vio_by_bucket": vio_by_bucket,
        "fine_by_violation": fbv,
        "veh_aliases": _meta(conn, "veh_aliases", {}),
        "keyword_to_vio": kw,
        "districts_by_state": dbs,
    }


def export_law(conn: sqlite3.Connection) -> Dict[str, Any]:
    """The reasoning layer: who is liable, which facts gate an offence, relations."""
    q = lambda sql: [dict(r) for r in conn.execute(sql)]
    liable: Dict[str, List[dict]] = {}
    for r in q("SELECT * FROM liable_party ORDER BY violation_code, rowid"):
        liable.setdefault(r.pop("violation_code"), []).append(r)
    conds: Dict[str, List[dict]] = {}
    for r in q("SELECT violation_code, fact, op, value_json, kind, note FROM violation_condition ORDER BY id"):
        r["value"] = json.loads(r.pop("value_json"))
        conds.setdefault(r.pop("violation_code"), []).append(r)
    rels = []
    for r in q("SELECT from_code, to_code, type, target_role, condition_json, note FROM violation_relation ORDER BY id"):
        r["condition"] = json.loads(r.pop("condition_json")) if r["condition_json"] else None
        rels.append(r)
    facts = {}
    for r in q("SELECT * FROM fact ORDER BY name"):
        facts[r["name"]] = {"type": r["type"],
                            "values": json.loads(r["values_json"]) if r["values_json"] else None,
                            "question": r["question"],
                            "chips": json.loads(r["chips_json"]) if r["chips_json"] else None}
    provs = {}
    for r in q("""SELECT p.*, s.title AS source_title, s.url AS source_url FROM provision p
                  LEFT JOIN source s ON s.id = p.source_id ORDER BY p.id"""):
        provs[f"{r['act']} §{r['section']}"] = {k: r[k] for k in (
            "act", "section", "title", "summary", "max_fine", "max_jail", "bailable", "note",
            "source_title", "source_url")}
    vprov: Dict[str, List[dict]] = {}
    for r in q("""SELECT vp.violation_code, vp.kind, p.act, p.section FROM violation_provision vp
                  JOIN provision p ON p.id = vp.provision_id ORDER BY vp.rowid"""):
        vprov.setdefault(r["violation_code"], []).append(
            {"ref": f"{r['act']} §{r['section']}", "kind": r["kind"]})
    examples: Dict[str, List[str]] = {}
    for r in q("SELECT violation_code, text FROM example ORDER BY rowid"):
        examples.setdefault(r["violation_code"], []).append(r["text"])
    severity = {r["code"]: r["severity"] for r in q("SELECT code, severity FROM violation WHERE severity IS NOT NULL")}
    return {"version": _meta(conn, "law_version", 1), "liable": liable, "conditions": conds,
            "relations": rels, "facts": facts, "provisions": provs,
            "violation_provisions": vprov, "examples": examples, "severity": severity}
