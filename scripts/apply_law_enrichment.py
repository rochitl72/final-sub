#!/usr/bin/env python3
"""
apply_law_enrichment.py — upsert data/law/enrichment.json into data/drivelegal.db
=================================================================================
Idempotent: re-running replaces the reasoning-layer tables (provision,
liable_party, fact, violation_condition, violation_relation, example,
violation_provision) and upserts the new violations / penalty corrections.
Then run scripts/export_graph.py to refresh the compiled JSON + phone bundle.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import legal_db  # noqa: E402

SRC = ROOT / "data" / "law" / "enrichment.json"


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    conn = legal_db.connect()
    legal_db.create_schema(conn)
    c = conn.cursor()

    for t in ("violation_provision", "liable_party", "violation_condition", "violation_relation",
              "example", "fact", "provision"):
        c.execute(f"DELETE FROM {t}")
    src_id = {}
    for s in d["sources"]:
        c.execute("""INSERT INTO source(key,title,url,retrieved,note) VALUES(?,?,?,?,?)
                     ON CONFLICT(key) DO UPDATE SET title=excluded.title, url=excluded.url, note=excluded.note""",
                  (s["key"], s["title"], s.get("url"), s.get("retrieved"), s.get("note")))
        src_id[s["key"]] = c.execute("SELECT id FROM source WHERE key=?", (s["key"],)).fetchone()[0]

    prov_id = {}
    for p in d["provisions"]:
        c.execute("""INSERT INTO provision(act,section,title,summary,max_fine,max_jail,bailable,note,source_id)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  (p["act"], p["section"], p["title"], p.get("summary"), p.get("max_fine"),
                   p.get("max_jail"), p.get("bailable"), p.get("note"), src_id[p["source"]]))
        prov_id[(p["act"], p["section"])] = c.lastrowid

    # ── New violations (+ their central penalty row) ──────────────────────
    max_ord = c.execute("SELECT COALESCE(MAX(ord),0) FROM violation").fetchone()[0]
    max_pen = c.execute("SELECT COALESCE(MAX(id),0), COALESCE(MAX(ord),0) FROM penalty").fetchone()
    pen_id, pen_ord = max_pen
    for v in d["new_violations"]:
        exists = c.execute("SELECT ord FROM violation WHERE code=?", (v["code"],)).fetchone()
        ordv = exists[0] if exists else (max_ord := max_ord + 1)
        c.execute("""INSERT INTO violation(code,ord,name,grp,mv_section,cmvr_rule,description,compoundable,
                       vehicle_applicability,what_to_do_next,tips_to_avoid,irc_sign_ref,officer_action,
                       evidence_req,consequence,common_misconception,severity,road_buckets)
                     VALUES(?,?,?,?,?,NULL,?,?,'["ALL"]',?,?,NULL,?,?,NULL,NULL,?,?)
                     ON CONFLICT(code) DO UPDATE SET name=excluded.name, grp=excluded.grp,
                       mv_section=excluded.mv_section, description=excluded.description,
                       compoundable=excluded.compoundable, what_to_do_next=excluded.what_to_do_next,
                       tips_to_avoid=excluded.tips_to_avoid, officer_action=excluded.officer_action,
                       evidence_req=excluded.evidence_req, severity=excluded.severity,
                       road_buckets=excluded.road_buckets""",
                  (v["code"], ordv, v["name"], v["grp"], v["mv_section"], v["description"],
                   int(v["compoundable"]), v["what_to_do_next"], v["tips_to_avoid"], v["officer_action"],
                   v["evidence_req"], v.get("severity"), json.dumps(v.get("road_buckets"))))
        c.execute("DELETE FROM alias WHERE violation_code=? AND origin='enrichment'", (v["code"],))
        for j, kw in enumerate(v.get("keywords") or []):
            c.execute("INSERT OR IGNORE INTO alias(violation_code,ord,phrase,lang,origin) VALUES(?,?,?,?,?)",
                      (v["code"], j, kw, "en", "enrichment"))
        pen = v["penalty"]
        row = c.execute("SELECT id FROM penalty WHERE violation_code=? AND scope='central'", (v["code"],)).fetchone()
        if row:
            c.execute("""UPDATE penalty SET first_offence=?, repeat_offence=?, imprisonment=?, note=?, source_id=?
                         WHERE id=?""", (pen.get("first_offence"), pen.get("repeat_offence"),
                                          pen.get("imprisonment"), pen.get("note"),
                                          src_id["bns" if v["provisions"][0][0].startswith("BNS") else "mva"],
                                          row[0]))
        else:
            pen_id += 1
            pen_ord += 1
            c.execute("""INSERT INTO penalty(id,ord,violation_code,scope,first_offence,repeat_offence,
                           imprisonment,note,source_id) VALUES(?,?,?,'central',?,?,?,?,?)""",
                      (pen_id, pen_ord, v["code"], pen.get("first_offence"), pen.get("repeat_offence"),
                       pen.get("imprisonment"), pen.get("note"),
                       src_id["bns" if v["provisions"][0][0].startswith("BNS") else "mva"]))
        for act, sec, kind in v.get("provisions", []):
            c.execute("INSERT OR IGNORE INTO violation_provision VALUES(?,?,?)", (v["code"], prov_id[(act, sec)], kind))

    for fix in d.get("penalty_corrections", []):
        n = c.execute("UPDATE penalty SET imprisonment=?, source_id=? WHERE violation_code=? AND scope=?",
                      (fix["imprisonment"], src_id["bns"], fix["violation_code"], fix["scope"])).rowcount
        if not n:
            raise SystemExit(f"penalty correction matched nothing: {fix}")

    codes = {r[0]: r[1] for r in c.execute("SELECT code, grp FROM violation")}

    def need(code):
        if code not in codes:
            raise SystemExit(f"unknown violation code {code}")
        return code

    for code, act, sec, kind in d["violation_provisions"]:
        c.execute("INSERT OR IGNORE INTO violation_provision VALUES(?,?,?)", (need(code), prov_id[(act, sec)], kind))

    # ── Liability: group default, then explicit override ──────────────────
    lia = d["liability"]
    n_lia = 0
    for code, grp in codes.items():
        rows = lia["overrides"].get(code)
        if rows is None:
            rows = []
            for role in lia["group_defaults"].get(grp, ["driver"]):
                maybe = role.endswith("?")
                rows.append([role.rstrip("?"), None, "may_be_liable" if maybe else "liable"])
        for r in rows:
            role, basis, certainty = r[0], r[1], r[2]
            note = r[3] if len(r) > 3 else None
            c.execute("INSERT INTO liable_party(violation_code,role,basis,certainty,note) VALUES(?,?,?,?,?)",
                      (code, role, basis, certainty, note))
            n_lia += 1

    for f in d["facts"]:
        c.execute("INSERT INTO fact(name,type,values_json,question,chips_json) VALUES(?,?,?,?,?)",
                  (f["name"], f["type"], json.dumps(f.get("values")) if f.get("values") else None,
                   f.get("question"), json.dumps(f.get("chips")) if f.get("chips") else None))
    for code, fact, op, value, kind, note in d["conditions"]:
        c.execute("INSERT INTO violation_condition(violation_code,fact,op,value_json,kind,note) VALUES(?,?,?,?,?,?)",
                  (need(code), fact, op, json.dumps(value), kind, note))
    for frm, to, typ, role, cond, note in d["relations"]:
        c.execute("""INSERT INTO violation_relation(from_code,to_code,type,target_role,condition_json,note)
                     VALUES(?,?,?,?,?,?)""", (need(frm), need(to), typ, role,
                                              json.dumps(cond) if cond else None, note))
    hurt_cond = {"fact": "event.outcome", "op": "in", "value": ["injury", "grievous_injury"]}
    death_cond = {"fact": "event.outcome", "op": "=", "value": "death"}
    for code in d["fault_offences_for_injury"]:
        c.execute("""INSERT INTO violation_relation(from_code,to_code,type,target_role,condition_json,note)
                     VALUES(?,?,'aggravates','driver',?,?)""",
                  (need(code), "CRIM_HURT_RASH_NEGLIGENT", json.dumps(hurt_cond), "Injury caused while committing this offence."))
        c.execute("""INSERT INTO violation_relation(from_code,to_code,type,target_role,condition_json,note)
                     VALUES(?,?,'aggravates','driver',?,?)""",
                  (code, "ACC_CAUSING_DEATH", json.dumps(death_cond), "Death caused while committing this offence."))

    for code, upd in d.get("violation_updates", {}).items():
        for col, val in upd.items():
            if col not in ("mv_section", "name", "description", "compoundable"):
                raise SystemExit(f"violation_updates: column {col} not allowed")
            c.execute(f"UPDATE violation SET {col}=? WHERE code=?", (val, need(code)))

    c.execute("UPDATE violation SET licence_action=NULL")
    for code, text in d["licence_actions"].items():
        c.execute("UPDATE violation SET licence_action=? WHERE code=?", (text, need(code)))

    # Severity: explicit for new rows; derive the rest from structured data.
    for code in codes:
        sev = c.execute("SELECT severity FROM violation WHERE code=?", (code,)).fetchone()[0]
        if sev:
            continue
        pens = c.execute("SELECT first_offence, imprisonment FROM penalty WHERE violation_code=? AND scope='central'",
                         (code,)).fetchall()
        comp = c.execute("SELECT compoundable FROM violation WHERE code=?", (code,)).fetchone()[0]
        jail = any(p[1] for p in pens)
        top = max([p[0] or 0 for p in pens] or [0])
        sev = "serious" if (jail or comp == 0 or top >= 5000) else "minor"
        c.execute("UPDATE violation SET severity=? WHERE code=?", (sev, code))

    for code, texts in d["examples"].items():
        for t in texts:
            c.execute("INSERT OR IGNORE INTO example(violation_code,text,lang) VALUES(?,?,?)", (need(code), t, "en"))

    legal_db.set_meta(conn, "law_version", d["version"])
    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
              ("violation", "penalty", "provision", "liable_party", "fact", "violation_condition",
               "violation_relation", "example", "violation_provision")}
    conn.close()
    print("Applied law enrichment:", counts, f"(liability rows written: {n_lia})")


if __name__ == "__main__":
    main()
