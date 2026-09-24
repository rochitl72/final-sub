#!/usr/bin/env python3
"""
graph_engine.py — DriveLegal In-Memory Graph Engine  (v3)
==========================================================
Loads drivelegal_graph.json (v3, fully enriched) and provides
a fast traversal API for the conversational chatbot.

New in v3:
  - 782 district nodes (RTO codes, helplines, state links)
  - Enriched city nodes (enforcement, city rules, 2025 fines, hotspots)
  - Enriched violation nodes (keywords, consequence, misconception)
  - 48 named road points in spatial index
  - Enriched road_class nodes (speed limits, key rules)

API:
  engine = get_graph_engine()

  engine.nearest_city(lat, lng, n=3)
  engine.nearest_corridors(lat, lng, n=5)
  engine.nearest_road_points(lat, lng, n=3)

  engine.get_violation_context(road_bucket, vehicle_codes, state_code, limit)
  engine.get_fine(violation_code, state_code, city_code, vehicle_class)
  engine.subgraph_for_llm(road_bucket, state_code, city_code, vehicle_codes, limit)

  engine.get_state(code) / get_city(code) / get_district(code)
  engine.get_violation(code) / get_vehicle(code) / get_road_class(code)

  engine.match_vehicle(user_text)
  engine.get_districts_for_state(state_code)
  engine.city_enforcement_summary(city_code)
"""

import json
import math
import re
import threading
from functools import lru_cache

from provenance import fine_amount, citation_for, fallback_note, is_sourced
from pathlib import Path
from typing import Optional

_HERE       = Path(__file__).parent
_SERVING_PATH = _HERE.parent / "data" / "compiled" / "drivelegal_graph.v5.serving.json"
_LEGACY_PATH = _HERE.parent / "data" / "compiled" / "drivelegal_graph.json"
# The v5 serving graph withholds every field that failed source verification.
# Falling back to the legacy v3 graph restores unsourced values, so it is only
# for local experiments — never for anything a member of the public can reach.
_GRAPH_PATH = _SERVING_PATH if _SERVING_PATH.exists() else _LEGACY_PATH

_engine_instance = None
_lock = threading.Lock()


def get_graph_engine() -> "GraphEngine":
    global _engine_instance
    if _engine_instance is None:
        with _lock:
            if _engine_instance is None:
                _engine_instance = GraphEngine()
    return _engine_instance


# ── Vehicle keyword → segment mapping ────────────────────────────────────────
VEHICLE_KEYWORDS = {
    "bike": "two_wheeler",       "motorcycle": "two_wheeler",
    "scooter": "two_wheeler",    "moped": "two_wheeler",
    "two wheeler": "two_wheeler","two-wheeler": "two_wheeler",
    "motorbike": "two_wheeler",  "electric bike": "two_wheeler",
    "ev bike": "two_wheeler",    "scooty": "two_wheeler",
    "auto": "three_wheeler",     "auto rickshaw": "three_wheeler",
    "rickshaw": "three_wheeler", "e-rickshaw": "three_wheeler",
    "e rickshaw": "three_wheeler","tuk tuk": "three_wheeler",
    "car": "four_wheeler",       "sedan": "four_wheeler",
    "suv": "four_wheeler",       "hatchback": "four_wheeler",
    "taxi": "four_wheeler",      "cab": "four_wheeler",
    "ev car": "four_wheeler",    "electric car": "four_wheeler",
    "jeep": "four_wheeler",      "mpv": "four_wheeler",
    "truck": "heavy_vehicle",    "lorry": "heavy_vehicle",
    "bus": "four_wheeler_plus",  "mini bus": "four_wheeler_plus",
    "minibus": "four_wheeler_plus",
    "tractor": "special",        "ambulance": "emergency",
}

SEGMENT_FINE_CLASS = {
    "two_wheeler":      "2W",
    "three_wheeler":    "3W_PASS",
    "four_wheeler":     "LMV",
    "four_wheeler_plus":"HPV",
    "heavy_vehicle":    "HGV",
    "special":          "TRACTOR",
    "emergency":        "LMV_TR",
}

SEGMENT_LABEL = {
    "two_wheeler":       "Bike / Scooter",
    "three_wheeler":     "Auto-rickshaw",
    "four_wheeler":      "Car / SUV",
    "four_wheeler_plus": "Bus / Minibus",
    "heavy_vehicle":     "Truck / LCV",
    "special":           "Tractor / Special",
    "emergency":         "Emergency",
}

# Curated state aliases (in addition to the auto-generated code/name lookups).
_STATE_ALIAS_SEED = {
    "TN": ("madras",),
    "KA": ("karnatak", "mysore state"),
    "MH": ("maharastra", "bombay state"),
    "WB": ("bengal", "west bengal"),
    "AP": ("andhra",),
    "TS": ("telangana", "telengana"),
    "OD": ("orissa",),
    "UK": ("uttaranchal",),
    "JK": ("kashmir", "jammu", "j&k"),
    "DL": ("delhi", "new delhi", "ncr"),
    "PB": ("punjab",),
    "KL": ("kerala", "keralam"),
}

# Curated city aliases (lower-cased, mapped to canonical city code).
_CITY_ALIAS_SEED = {
    "bangalore":     "BLR",
    "bengaluru":     "BLR",
    "bombay":        "MUM",
    "mumbai":        "MUM",
    "madras":        "CHN",
    "chennai":       "CHN",
    "calcutta":      "KOL",
    "kolkata":       "KOL",
    "trivandrum":    "TVM",
    "thiruvananthapuram": "TVM",
    "cochin":        "KOC",
    "kochi":         "KOC",
    "mysore":        "MYS",
    "mysuru":        "MYS",
    "benares":       "VNS",
    "varanasi":      "VNS",
    "poona":         "PUN",
    "pune":          "PUN",
}


def _norm(text: str) -> str:
    """Lower-case + collapse whitespace + strip punctuation for alias matching."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class GraphEngine:
    """
    Loads graph JSON once (~1.2 MB) and provides <3 ms traversal queries.
    """

    def __init__(self, graph_path: Optional[Path] = None):
        path = graph_path or _GRAPH_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"Graph not found: {path}\n"
                "The compiled graph is shipped with this repo at "
                "drivelegal/data/compiled/drivelegal_graph.json — re-clone or restore it."
            )
        print(f"[graph_engine] Loading {path.name} …", flush=True)
        g = json.loads(path.read_text(encoding="utf-8"))

        self.nodes:   dict = g["nodes"]
        self.edges:   list = g["edges"]
        self.spatial        = g["spatial"]
        self.indexes        = g["indexes"]
        self.meta           = g["meta"]

        # Adjacency lists
        self.adj: dict = {}
        self.rev: dict = {}
        for e in self.edges:
            self.adj.setdefault(e["src"], []).append((e["rel"], e["dst"]))
            self.rev.setdefault(e["dst"], []).append((e["rel"], e["src"]))

        c = self.meta["counts"]
        print(
            f"[graph_engine] Loaded v{self.meta.get('version','?')} — "
            f"{c['nodes']} nodes  {c['edges']} edges  "
            f"{c['districts']} districts  {c['cities']} cities",
            flush=True,
        )

        self._build_name_indexes()
        self._build_speed_table()

    # ── Lookup indexes (built once at startup) ───────────────────────────────

    def _build_name_indexes(self) -> None:
        """One-time scan of self.nodes → alias dicts used by location_resolver.

        - self._states_by_code:  {"TN": state_node, ...}
        - self._state_by_alias:  {"tamil nadu": "TN", "tn": "TN", "madras": "TN", ...}
        - self._cities_by_code:  {"BLR": city_node, ...}
        - self._city_by_alias:   {"bengaluru": [("BLR","KA")], "hyderabad": [("HYD","TS")], ...}
        - self._violations_by_keyword: {"no helmet": ["SAFETY_NO_HELMET_RIDER", ...], ...}
        """
        self._states_by_code: dict = {}
        self._state_by_alias: dict = {}
        for nid, node in self.nodes.items():
            if node.get("type") != "state":
                continue
            code = node["code"]
            self._states_by_code[code] = node
            for alias in (code, code.lower(), node.get("name", ""), node.get("capital", "")):
                a = _norm(alias)
                if a:
                    self._state_by_alias.setdefault(a, code)
            for extra in _STATE_ALIAS_SEED.get(code, ()):
                a = _norm(extra)
                if a:
                    self._state_by_alias.setdefault(a, code)

        self._cities_by_code: dict = {}
        self._city_by_alias: dict = {}

        def _add_city_alias(alias: str, code: str, state_code: str) -> None:
            a = _norm(alias)
            if not a:
                return
            bucket = self._city_by_alias.setdefault(a, [])
            entry = (code, state_code)
            if entry not in bucket:
                bucket.append(entry)

        for nid, node in self.nodes.items():
            if node.get("type") != "city":
                continue
            code = node["code"]
            state_code = node.get("state_code", "")
            self._cities_by_code[code] = node
            name = node.get("name", "")
            _add_city_alias(name, code, state_code)
            _add_city_alias(code, code, state_code)
            # "Bengaluru (Bruhat Bengaluru)" → also add "Bengaluru" alone.
            base = re.split(r"[(/]", name, maxsplit=1)[0].strip()
            if base and base != name:
                _add_city_alias(base, code, state_code)

        for alias, code in _CITY_ALIAS_SEED.items():
            node = self._cities_by_code.get(code)
            if node:
                _add_city_alias(alias, code, node.get("state_code", ""))

        # Keyword → violation inverted index (from precomputed graph index plus
        # extra single-token expansions of violation `keywords` lists).
        kw_index: dict = {
            _norm(k): list(v)
            for k, v in self.indexes.get("keyword_to_vio", {}).items()
        }
        for nid, node in self.nodes.items():
            if node.get("type") != "violation":
                continue
            vcode = node["code"]
            extras = []
            extras.extend(node.get("keywords") or [])
            if node.get("name"):
                extras.append(node["name"])
            if node.get("common_misconception"):
                extras.append(node["common_misconception"])
            for raw in extras:
                key = _norm(raw)
                if not key:
                    continue
                bucket = kw_index.setdefault(key, [])
                if vcode not in bucket:
                    bucket.append(vcode)
        self._violations_by_keyword: dict = kw_index

    def _build_speed_table(self) -> None:
        """Precompute speed-limit table per road bucket (was rebuilt every turn)."""
        speeds_by_bucket: dict = {"highway": {}, "main_road": {}, "street": {}}
        for nid, node in self.nodes.items():
            if node.get("type") != "road_class":
                continue
            bucket = node.get("bucket")
            spd = node.get("default_speed")
            if bucket in speeds_by_bucket and spd:
                speeds_by_bucket[bucket][node["name"]] = spd
        self._speeds_by_bucket: dict = speeds_by_bucket

    # ── Public accessors for the lookup indexes ──────────────────────────────

    def states_by_code(self) -> dict:
        return self._states_by_code

    def cities_by_code(self) -> dict:
        return self._cities_by_code

    def state_by_alias(self) -> dict:
        return self._state_by_alias

    def city_by_alias(self) -> dict:
        return self._city_by_alias

    def violations_by_keyword(self) -> dict:
        return self._violations_by_keyword

    def speeds_by_bucket(self, bucket: str) -> dict:
        return self._speeds_by_bucket.get(bucket, {})

    # ── Distance helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _haversine(lat1, lng1, lat2, lng2) -> float:
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (math.sin(dlat/2)**2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlng/2)**2)
        return R * 2 * math.asin(math.sqrt(min(1.0, a)))

    @staticmethod
    def _seg_dist(lat, lng, lat1, lng1, lat2, lng2) -> float:
        """Approx distance from point to segment (km)."""
        dx, dy = lat2-lat1, lng2-lng1
        if dx == 0 and dy == 0:
            return math.hypot((lat-lat1)*111, (lng-lng1)*111)
        t = max(0.0, min(1.0, ((lat-lat1)*dx + (lng-lng1)*dy) / (dx*dx + dy*dy)))
        return math.hypot((lat - lat1 - t*dx)*111, (lng - lng1 - t*dy)*111)

    # ── Spatial queries ───────────────────────────────────────────────────────

    def nearest_city(self, lat: float, lng: float, n: int = 3) -> list:
        """n closest cities (with lat/lng)."""
        results = []
        for c in self.spatial["cities"]:
            d = self._haversine(lat, lng, c["lat"], c["lng"])
            results.append({**c, "distance_km": round(d, 2)})
        results.sort(key=lambda x: x["distance_km"])
        return results[:n]

    def nearest_road_points(self, lat: float, lng: float, n: int = 3) -> list:
        """n closest named road points (toll plazas, junctions, etc.)."""
        results = []
        for rp in self.spatial.get("road_points", []):
            d = self._haversine(lat, lng, rp["lat"], rp["lng"])
            results.append({**rp, "distance_km": round(d, 2)})
        results.sort(key=lambda x: x["distance_km"])
        return results[:n]

    def nearest_corridors(self, lat: float, lng: float, n: int = 5) -> list:
        """n closest road corridors (polyline distance)."""
        results = []
        for corr in self.spatial["corridors"]:
            bb = corr.get("bbox", [])
            if bb:
                mid_lat = (bb[0]+bb[2])/2
                mid_lng = (bb[1]+bb[3])/2
                if self._haversine(lat, lng, mid_lat, mid_lng) > 500:
                    continue
            wps = corr.get("waypoints", [])
            if len(wps) < 2:
                if wps:
                    d = self._haversine(lat, lng, wps[0][0], wps[0][1])
                    results.append({**corr, "distance_km": round(d, 2)})
                continue
            min_d = min(
                self._seg_dist(lat, lng, wps[i][0], wps[i][1], wps[i+1][0], wps[i+1][1])
                for i in range(len(wps)-1)
            )
            results.append({
                "id": corr["id"], "code": corr["code"], "name": corr["name"],
                "road_class": corr["road_class"], "bucket": corr["bucket"],
                "distance_km": round(min_d, 2),
            })
        results.sort(key=lambda x: x["distance_km"])
        return results[:n]

    # ── Node getters ──────────────────────────────────────────────────────────

    def get_state(self, code: str)      -> Optional[dict]: return self.nodes.get(f"state:{code}")
    def get_city(self, code: str)       -> Optional[dict]: return self.nodes.get(f"city:{code}")
    def get_district(self, code: str)   -> Optional[dict]: return self.nodes.get(f"district:{code}")
    def get_violation(self, code: str)  -> Optional[dict]: return self.nodes.get(f"vio:{code}")
    def get_vehicle(self, code: str)    -> Optional[dict]: return self.nodes.get(f"veh:{code}")
    def get_road_class(self, code: str) -> Optional[dict]: return self.nodes.get(f"road:{code}")

    def get_districts_for_state(self, state_code: str) -> list:
        """All district nodes for a state."""
        return self.indexes.get("districts_by_state", {}).get(state_code, [])

    # ── Violation context ────────────────────────────────────────────────────

    def get_violation_context(
        self,
        road_bucket:   str,
        vehicle_codes: Optional[list] = None,
        state_code:    Optional[str]  = None,
        limit:         int = 20,
    ) -> list:
        """
        Returns up to `limit` violation nodes relevant to the road + vehicle context.
        Scored: +2 if vehicle-specific match, +1 base relevance.
        """
        bucket_vios = self.indexes["vio_by_bucket"].get(road_bucket, [])
        scored = []
        for vio_nid in bucket_vios:
            node = self.nodes.get(vio_nid)
            if not node:
                continue
            score = 1
            va = node.get("vehicle_applicability", ["ALL"])
            if vehicle_codes and va != ["ALL"]:
                if any(vc in va for vc in vehicle_codes):
                    score += 2
            elif va == ["ALL"]:
                score += 1
            scored.append((score, node))
        scored.sort(key=lambda x: -x[0])
        return [n for _, n in scored[:limit]]

    # ── Fine lookup (cascade) ─────────────────────────────────────────────────

    def get_fine(
        self,
        violation_code: str,
        state_code:     Optional[str] = None,
        city_code:      Optional[str] = None,
        vehicle_class:  Optional[str] = None,
    ) -> Optional[dict]:
        """Fine cascade: city → state → central. Returns most specific match."""
        fbi = self.indexes["fine_by_violation"].get(violation_code)
        if not fbi:
            return None

        def _pick(nids, vc=None):
            """Choose a fine row, skipping any whose amount is not sourced.

            Skipping rather than returning is what makes the cascade honest: an
            unsourced state row falls through to the central figure, which IS
            the operative law where a state has notified nothing (s.200). If no
            level has a sourced amount, the caller gets None and must refuse.
            """
            if not nids:
                return None
            if vc:
                for nid in nids:
                    n = self.nodes.get(nid, {})
                    if n.get("vehicle_class") == vc and fine_amount(n) is not None:
                        return n
            for nid in nids:
                n = self.nodes.get(nid, {})
                if not n.get("vehicle_class") and fine_amount(n) is not None:
                    return n
            for nid in nids:
                n = self.nodes.get(nid, {})
                if fine_amount(n) is not None:
                    return n
            return None

        if city_code:
            r = _pick(fbi.get("city",{}).get(city_code,[]), vehicle_class)
            if r: return {**r, "fine_source": "city"}

        if state_code:
            r = _pick(fbi.get("state",{}).get(state_code,[]), vehicle_class)
            if r: return {**r, "fine_source": "state"}

        r = _pick(fbi.get("central",[]), vehicle_class)
        if r: return {**r, "fine_source": "central"}
        return None

    # ── Vehicle matching ──────────────────────────────────────────────────────

    def match_vehicle(self, text: str) -> list:
        """Match free text → vehicle nodes."""
        text_l = text.lower().strip()
        for kw, segment in VEHICLE_KEYWORDS.items():
            if kw in text_l:
                results = [n for nid, n in self.nodes.items()
                           if n.get("type") == "vehicle" and n.get("segment") == segment]
                return results[:5]
        # Fuzzy: check vehicle names
        results = []
        for nid, node in self.nodes.items():
            if node.get("type") == "vehicle":
                if any(w in text_l for w in node["name"].lower().split() if len(w) > 3):
                    results.append(node)
        return results[:5]

    # ── City enforcement summary ──────────────────────────────────────────────

    def city_enforcement_summary(self, city_code: str) -> str:
        """Short text about a city's enforcement and specific rules (for LLM context)."""
        ct = self.get_city(city_code)
        if not ct:
            return ""
        parts = []
        if ct.get("traffic_police"):
            parts.append(f"Traffic: {ct['traffic_police']}")
        if ct.get("has_ai_cameras") or ct.get("has_anpr"):
            cams = []
            if ct.get("has_ai_cameras"): cams.append("AI cameras")
            if ct.get("has_anpr"):       cams.append("ANPR cameras")
            parts.append("Enforcement: " + ", ".join(cams))
        if ct.get("enforcement_notes"):
            parts.append(ct["enforcement_notes"][:120])
        csr = ct.get("city_specific_rules") or {}
        if csr:
            for k, v in list(csr.items())[:3]:
                parts.append(f"City rule — {k.replace('_',' ')}: {str(v)[:80]}")
        kfe = ct.get("key_fines_2025") or {}
        if kfe:
            fine_lines = [f"{k.replace('_',' ')}=₹{v}" for k,v in list(kfe.items())[:6]]
            parts.append("City fines (2025): " + ", ".join(fine_lines))
        if ct.get("traffic_helpline"):
            parts.append(f"Traffic helpline: {ct['traffic_helpline']}")
        return "\n  ".join(parts)

    # ── LLM subgraph builder ──────────────────────────────────────────────────

    def subgraph_for_llm(
        self,
        road_bucket:   str,
        state_code:    Optional[str],
        city_code:     Optional[str],
        vehicle_codes: Optional[list] = None,
        limit:         int = 20,
    ) -> str:
        """
        Build a compact context block for the LLM system prompt.
        Includes: state, city enforcement, road type, relevant violations + fines.
        """
        parts = []

        # State
        if state_code:
            st = self.get_state(state_code)
            if st:
                # The old graph carried a per-state `multiplier` used to scale central
                # fines. It has no basis in law — s.200 lets a State Government
                # notify amounts, not scale them — and it did not even reproduce the
                # stored state fines. It was removed in v5; do not reintroduce it.
                parts.append(
                    f"STATE: {st['name']} ({st['code']}). State compounding amounts are "
                    f"fixed by state notification under s.200 MV Act. Where no verified "
                    f"state amount exists, the Motor Vehicles Act figure applies and the "
                    f"answer must say so."
                )

        # City (with enforcement summary)
        if city_code:
            ct = self.get_city(city_code)
            if ct:
                parts.append(f"CITY: {ct['name']} ({ct['code']})")
                enf = self.city_enforcement_summary(city_code)
                if enf:
                    parts.append(f"  {enf}")

        # Road type
        bucket_desc = {
            "highway":   "Highway / Expressway",
            "main_road": "State/District Road",
            "street":    "Urban Street / City Road",
        }
        parts.append(f"ROAD TYPE: {bucket_desc.get(road_bucket, road_bucket)}")

        # Road class speed info (precomputed once at startup)
        rc_speeds = self._speeds_by_bucket.get(road_bucket, {})
        if rc_speeds:
            speed_line = ", ".join(f"{n}: {s} km/h" for n, s in list(rc_speeds.items())[:4])
            parts.append(f"Speed limits: {speed_line}")

        # Violations
        violations = self.get_violation_context(road_bucket, vehicle_codes, state_code, limit=limit)
        if violations:
            parts.append(f"\nRELEVANT VIOLATIONS ({road_bucket} — {len(violations)} loaded):")
            for v in violations:
                code     = v["code"]
                name     = v["name"]
                section  = v.get("mv_section") or "–"
                compound = "compoundable" if v.get("compoundable") else "NOT compoundable"
                line = f"  [{code}] {name} — §{section} — {compound}"

                if v.get("what_to_do_next"):
                    line += f"\n    → Do: {v['what_to_do_next']}"
                if v.get("consequence"):
                    line += f"\n    → Consequence: {v['consequence']}"
                if v.get("tips_to_avoid"):
                    line += f"\n    → Tip: {v['tips_to_avoid']}"
                if v.get("common_misconception"):
                    line += f"\n    → Misconception: {v['common_misconception']}"

                # Fine (cascade)
                fine = self.get_fine(code, state_code, city_code,
                                     vehicle_codes[0] if vehicle_codes else None)
                if fine:
                    first  = f"₹{fine['first_offence']}"  if fine.get("first_offence")  else "–"
                    repeat = f"₹{fine['repeat_offence']}" if fine.get("repeat_offence") else "–"
                    imp    = f" + jail: {fine['imprisonment']}" if fine.get("imprisonment") else ""
                    src    = fine.get("fine_source","central")
                    line  += f"\n    Fine ({src}): 1st {first} / repeat {repeat}{imp}"

                parts.append(line)

        return "\n".join(parts)

    def describe_corridor(self, code: str) -> Optional[str]:
        nid  = f"corridor:{code}"
        node = self.nodes.get(nid)
        if not node: return None
        rc = self.get_road_class(node["road_class"])
        rc_name = rc["name"] if rc else node["road_class"]
        spd = rc.get("default_speed","?") if rc else "?"
        return f"{node['name']} ({rc_name}, ~{spd} km/h limit)"

    # ── Cached subgraph text + quick fine card ────────────────────────────────

    @lru_cache(maxsize=256)
    def subgraph_text(
        self,
        bucket: str,
        state_code: Optional[str],
        city_code: Optional[str],
        vehicle_fine_class: Optional[str],
    ) -> str:
        """Cached, hashable wrapper around `subgraph_for_llm` keyed on the
        (bucket, state, city, vehicle_class) tuple — repeat turns reuse it."""
        veh_codes = [vehicle_fine_class] if vehicle_fine_class else None
        return self.subgraph_for_llm(
            road_bucket   = bucket,
            state_code    = state_code,
            city_code     = city_code,
            vehicle_codes = veh_codes,
            limit         = 20,
        )

    def quick_fine(
        self,
        violation_code: str,
        state_code:     Optional[str] = None,
        city_code:      Optional[str] = None,
        vehicle_fine_class: Optional[str] = None,
    ) -> Optional[dict]:
        """Build the `fine_card` dict directly (no LLM round-trip).

        Mirrors the structure produced by the old `_get_fine_card` so the UI
        contract is unchanged.  Patches from patch_engine are layered on top
        of the base graph result — the base graph is NEVER modified.
        """
        fine = self.get_fine(
            violation_code = violation_code,
            state_code     = state_code,
            city_code      = city_code,
            vehicle_class  = vehicle_fine_class,
        )
        if not fine:
            return None

        vio = self.get_violation(violation_code)
        card = {
            "violation_code":       violation_code,
            "violation_name":       vio["name"]                  if vio else violation_code,
            "mv_section":           (vio.get("mv_section")
                                     if vio and is_sourced(vio, "mv_section") else None),
            "compoundable":         vio.get("compoundable")      if vio else False,
            "what_to_do_next":      vio.get("what_to_do_next")   if vio else None,
            "tips_to_avoid":        vio.get("tips_to_avoid")     if vio else None,
            "irc_sign_ref":         vio.get("irc_sign_ref")      if vio else None,
            "licence_consequence":  (vio.get("dl_consequence")
                                     or vio.get("consequence"))  if vio else None,
            "fine_first":           fine.get("first_offence"),
            "fine_repeat":          fine.get("repeat_offence"),
            "imprisonment":         fine.get("imprisonment"),
            "fine_source":          fine.get("fine_source", "central"),
            "state_code":           state_code or fine.get("state_code"),
            "city_code":            city_code  or fine.get("city_code"),
            "answered_at_level":    fine.get("fine_source", "central"),
            "violation_group":      vio.get("grp")               if vio else None,
            # ── Provenance. Every figure shown to the public carries its source.
            # `citation` is not decoration: if it is absent the UI must not
            # render the amount as law. See backend/provenance.py.
            "citation":             citation_for(fine, vio),
            "fallback_note":        fallback_note(
                                        fine.get("fine_source", "central"),
                                        (self.get_state(state_code) or {}).get("name")
                                        if state_code else None),
            "section_verified":     bool(vio and is_sourced(vio, "mv_section")),
            # Patch fields — populated below if updates exist, None otherwise
            "patch_fine_first":     None,
            "patch_fine_repeat":    None,
            "patch_effective_date": None,
            "patch_timeline":       None,   # list of {date, fine_first, source}
        }

        # ── Overlay patches (separate table, base graph untouched) ────────────
        try:
            from patch_engine import get_patches
            patches = get_patches(violation_code, state_code, confidence_min=0.75)
            if patches:
                latest = patches[0]   # most recent patch first
                card["patch_fine_first"]     = latest.get("new_fine_first")
                card["patch_fine_repeat"]    = latest.get("new_fine_repeat")
                card["patch_effective_date"] = latest.get("effective_date")

                # Build a human-readable timeline list for the UI
                timeline = []
                base_date = "Before updates"
                if fine.get("first_offence"):
                    timeline.append({
                        "label":      base_date,
                        "fine_first": fine["first_offence"],
                        "fine_repeat": fine.get("repeat_offence"),
                        "source":     "Base dataset",
                    })
                for p in reversed(patches):   # chronological order
                    timeline.append({
                        "label":      f"From {p['effective_date'] or 'recent update'}",
                        "fine_first": p.get("new_fine_first"),
                        "fine_repeat": p.get("new_fine_repeat"),
                        "source":     p.get("source_domain", "gov.in"),
                        "summary":    p.get("rule_summary", ""),
                    })
                card["patch_timeline"] = timeline
        except Exception:
            pass   # Patch engine unavailable — silently fall back to base data

        return card
