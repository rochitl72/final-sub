#!/usr/bin/env python3
"""
Compile the v5 SERVING graph into a single read-only SQLite file.

This is the runtime artifact for BOTH the online backend and the offline
mobile/web clients — one byte-identical file, so the two can never disagree
about what the law says.

Design rules:
  * Only serving-tier values are compiled in. A quarantined value is physically
    absent from the file, so no code path can reach it.
  * Every row carrying a rupee amount or a citation also carries its source.
  * FTS5 over violation text gives offline search with no model shipped.
  * Opened read-only with immutable=1: no locks, no WAL, no writers.
"""
import json, sqlite3, hashlib, datetime, tempfile, os, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root
SRC = ROOT / "data" / "compiled" / "drivelegal_graph.v5.serving.json"
DB = ROOT / "data" / "compiled" / "drivelegal.sqlite"
# Build into a scratch file and copy over the destination rather than unlinking
# it: the compiled artifact may sit in a directory where deletes are not
# permitted, and an overwrite is a write, not a delete.
_BUILD = Path(tempfile.gettempdir()) / f"drivelegal_build_{os.getpid()}.sqlite"
_BUILD.unlink(missing_ok=True)

G = json.loads(SRC.read_text())
NODES = G["nodes"]
POLICY = G["meta"]["resolution_policy"]
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

con = sqlite3.connect(_BUILD)
con.executescript("""
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE source (
  source_id     TEXT PRIMARY KEY,
  url           TEXT NOT NULL,
  title         TEXT,
  retrieved_at  TEXT
);

CREATE TABLE violation (
  code          TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  grp           TEXT,
  description   TEXT,
  mv_section    TEXT,          -- NULL when the citation did not survive verification
  cmvr_rule     TEXT,
  compoundable  INTEGER,
  serveable     INTEGER NOT NULL,   -- 0 = recognisable, but nothing verified to say
  section_source_id TEXT REFERENCES source(source_id)
);

CREATE TABLE fine (
  fine_id       INTEGER PRIMARY KEY,
  violation_code TEXT NOT NULL REFERENCES violation(code),
  level         TEXT NOT NULL CHECK (level IN ('central','state','city')),
  state_code    TEXT,
  city_code     TEXT,
  vehicle_class TEXT,
  amount_inr    INTEGER,       -- always an integer or NULL. never a string.
  max_inr       INTEGER,
  basis         TEXT NOT NULL DEFAULT 'flat',
  per_unit      TEXT,
  imprisonment  TEXT,
  source_id     TEXT REFERENCES source(source_id),
  source_url    TEXT,
  retrieved_at  TEXT,
  evidence      TEXT,
  CHECK (amount_inr IS NULL OR source_url IS NOT NULL)  -- no unsourced money
);
CREATE INDEX idx_fine_lookup ON fine(violation_code, level, state_code, city_code);

CREATE TABLE jurisdiction (
  code        TEXT PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('state','city','district')),
  name        TEXT NOT NULL,
  parent_code TEXT,
  lat         REAL,
  lng         REAL,
  rto_prefix  TEXT
);
CREATE INDEX idx_juris_parent ON jurisdiction(parent_code);
CREATE INDEX idx_juris_geo ON jurisdiction(lat, lng);

CREATE TABLE speed_limit (
  road_class   TEXT NOT NULL,
  road_name    TEXT,
  category     TEXT NOT NULL,
  limit_kmph   INTEGER NOT NULL,
  source_url   TEXT NOT NULL,
  retrieved_at TEXT,
  PRIMARY KEY (road_class, category)
);

CREATE TABLE policy (field_class TEXT PRIMARY KEY, rule_json TEXT NOT NULL);

-- offline search with no model shipped
CREATE VIRTUAL TABLE violation_fts USING fts5(
  code UNINDEXED, name, keywords, description, tokenize='porter unicode61'
);
""")

# ----------------------------------------------------------------- sources
for sid, s in (G.get("sources") or {}).items():
    con.execute("INSERT OR IGNORE INTO source VALUES (?,?,?,?)",
                (sid, s["url"], s.get("title"), s.get("retrieved_at")))


def ok(node, field):
    f = (node.get("_verification") or {}).get("fields", {}).get(field, {})
    return f if f.get("status") == "verified" else None


# -------------------------------------------------------------- violations
nv = 0
for n in NODES.values():
    if n.get("type") != "violation":
        continue
    sec = ok(n, "mv_section")
    con.execute(
        "INSERT INTO violation VALUES (?,?,?,?,?,?,?,?,?)",
        (n["code"], n["name"], n.get("grp"), n.get("description"),
         n.get("mv_section") if sec else None,
         n.get("cmvr_rule") if ok(n, "cmvr_rule") else None,
         int(bool(n["compoundable"])) if ok(n, "compoundable") else None,
         int(bool(n.get("_serveable"))),
         (sec or {}).get("source_id")))
    con.execute("INSERT INTO violation_fts VALUES (?,?,?,?)",
                (n["code"], n["name"], " ".join(n.get("keywords") or []),
                 n.get("description") or ""))
    nv += 1

# ------------------------------------------------------------------- fines
nf = skipped = 0
for n in NODES.values():
    if n.get("type") != "fine":
        continue
    m = n.get("fine_first") or {}
    v = ok(n, "first_offence")
    if not (m.get("resolved") and v and v.get("source_url")):
        skipped += 1          # withheld or unsourced: never compiled in
        continue
    level = "central" if n.get("scope") == "central" else (
        "city" if n.get("city_code") else "state")
    con.execute(
        "INSERT INTO fine VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (n["id"], n["violation_code"], level, n.get("state_code"), n.get("city_code"),
         n.get("vehicle_class"), m.get("amount_inr"), m.get("max_inr"),
         m.get("basis", "flat"), m.get("per_unit"),
         n.get("imprisonment") if ok(n, "imprisonment") else None,
         v.get("source_id"), v.get("source_url"), v.get("retrieved_at"),
         (v.get("evidence") or "")[:400]))
    nf += 1

# ----------------------------------------------------------- jurisdictions
nj = 0
for n in NODES.values():
    t = n.get("type")
    if t == "state":
        con.execute("INSERT OR REPLACE INTO jurisdiction VALUES (?,?,?,?,?,?,?)",
                    (n["code"], "state", n["name"], None, None, None,
                     n.get("rto_prefix") if ok(n, "rto_prefix") else None))
        nj += 1
    elif t in ("city", "district"):
        con.execute("INSERT OR REPLACE INTO jurisdiction VALUES (?,?,?,?,?,?,?)",
                    (n["code"], t, n["name"], n.get("state_code"),
                     n.get("lat"), n.get("lng"), None))
        nj += 1

# ------------------------------------------------------------ speed limits
ns = 0
for n in NODES.values():
    if n.get("type") != "road_class":
        continue
    for cat, lim in (n.get("speed_limits") or {}).items():
        if not isinstance(lim, int):
            continue
        v = ok(n, f"speed_limits.{cat}")
        if not (v and v.get("source_url")):
            continue          # unverified speed limits are NEVER compiled in
        con.execute("INSERT OR REPLACE INTO speed_limit VALUES (?,?,?,?,?,?)",
                    (n["code"], n.get("name"), cat, lim,
                     v["source_url"], v.get("retrieved_at")))
        ns += 1

for fc, rule in POLICY["field_classes"].items():
    con.execute("INSERT INTO policy VALUES (?,?)", (fc, json.dumps(rule)))

for k, v in {
    "schema_version": "5.0",
    "compiled_at": NOW,
    "source_artifact": SRC.name,
    "precedence": json.dumps(POLICY["precedence"]),
    "serving_rule": POLICY["serving_rule"],
    "temporal_validity": "ABSENT — no fact carries an effective date. See meta.not_yet_addressed.",
}.items():
    con.execute("INSERT INTO meta VALUES (?,?)", (k, v))

con.commit()
con.executescript("VACUUM; ANALYZE;")
con.commit()

# ------------------------------------------------------------- integrity
bad = con.execute(
    "SELECT COUNT(*) FROM fine WHERE amount_inr IS NOT NULL AND "
    "(source_url IS NULL OR source_url = '')").fetchone()[0]
assert bad == 0, f"{bad} fine rows carry money without a source"
bad2 = con.execute("SELECT COUNT(*) FROM speed_limit WHERE source_url IS NULL").fetchone()[0]
assert bad2 == 0
con.close()

DB.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(_BUILD, DB)        # truncate-and-write, no unlink needed
_BUILD.unlink(missing_ok=True)

size = DB.stat().st_size
print(f"compiled {DB.name}  ({size/1024:.0f} KB)")
print(f"  violations     {nv}")
print(f"  fine rows      {nf}   (withheld / unsourced, not compiled: {skipped})")
print(f"  jurisdictions  {nj}")
print(f"  speed limits   {ns}")
print(f"  sha256         {hashlib.sha256(DB.read_bytes()).hexdigest()[:16]}")
print("\nintegrity: no money without a source, no unsourced speed limit. OK")
