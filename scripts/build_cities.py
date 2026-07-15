#!/usr/bin/env python3
"""
build_cities.py — compact location dataset for the on-device AI (PWA) page.
Lets slm.html do GPS / map-pin / state→city selection fully offline.

Run:  python3 scripts/build_cities.py
Out:  apps/web/drivelegal_cities.json
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC  = ROOT / "data" / "compiled" / "drivelegal_graph.json"
OUT  = ROOT / "apps" / "web" / "drivelegal_cities.json"


def main() -> None:
    g = json.loads(SRC.read_text(encoding="utf-8"))
    nodes = g["nodes"]

    states = []
    for n in nodes.values():
        if n.get("type") == "state":
            states.append([n["code"], n.get("name", n["code"])])
    states.sort(key=lambda s: s[1])

    cities = []
    for n in nodes.values():
        if n.get("type") == "city" and n.get("lat") and n.get("lng"):
            c = {
                "c": n["code"],
                "n": n.get("name", n["code"]),
                "s": n.get("state_code", ""),
                "lat": round(float(n["lat"]), 4),
                "lng": round(float(n["lng"]), 4),
            }
            h = n.get("traffic_helpline")
            if h:
                c["h"] = h
            cities.append(c)
    cities.sort(key=lambda c: c["n"])

    bundle = {"states": states, "cities": cities}
    OUT.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.name} ({kb:.0f} KB) — {len(states)} states, {len(cities)} cities")


if __name__ == "__main__":
    main()
