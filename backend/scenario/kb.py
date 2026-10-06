"""Read-only view of the law layer (liability, conditions, relations, facts)."""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional

from graph_engine import SEGMENT_FINE_CLASS, get_graph_engine

SEVERITY_RANK = {"criminal": 3, "serious": 2, "minor": 1, None: 0}


class LawKB:
    def __init__(self) -> None:
        eng = get_graph_engine()
        self.eng = eng
        law = eng.law or {}
        self.liable: Dict[str, List[dict]] = law.get("liable", {})
        self.conditions: Dict[str, List[dict]] = law.get("conditions", {})
        self.facts: Dict[str, dict] = law.get("facts", {})
        self.provisions: Dict[str, dict] = law.get("provisions", {})
        self.vprov: Dict[str, List[dict]] = law.get("violation_provisions", {})
        self.examples: Dict[str, List[str]] = law.get("examples", {})
        self.severity: Dict[str, str] = law.get("severity", {})
        self.relations = law.get("relations", [])
        self.rel_from: Dict[str, List[dict]] = {}
        self.alternatives: Dict[str, List[str]] = {}
        for r in self.relations:
            self.rel_from.setdefault(r["from_code"], []).append(r)
            if r["type"] == "alternative":
                self.alternatives.setdefault(r["from_code"], []).append(r["to_code"])
                self.alternatives.setdefault(r["to_code"], []).append(r["from_code"])

    # ── catalogue ──────────────────────────────────────────────────────────
    def exists(self, code: str) -> bool:
        return bool(self.eng.get_violation(code))

    def violation(self, code: str) -> dict:
        return self.eng.get_violation(code) or {}

    def name(self, code: str) -> str:
        return self.violation(code).get("name", code)

    def roles(self, code: str) -> List[dict]:
        return self.liable.get(code) or [{"role": "driver", "certainty": "liable", "basis": None, "note": None}]

    def sev(self, code: str) -> str:
        return self.severity.get(code) or "minor"

    def catalogue_lines(self) -> List[str]:
        """Compact `CODE: name` lines for the LLM prompt."""
        import re as _re
        out = []
        for nid, n in self.eng.nodes.items():
            if n.get("type") == "violation" and n["code"] != "MISC_GENERAL":
                name = _re.sub(r"\s*\([^)]*\)", "", n["name"])          # drop parentheticals
                name = " ".join(name.replace("/", " / ").split()[:7])
                out.append(f"{n['code']}={name}")
        return out

    # ── retrieval: which offences does this story sound like? ──────────────
    _STOP = frozenset("the and was were with that this have has had for you your not but are from they them then when "
                      "what who his her him our its into onto been being would could should there their about after "
                      "before while also very just only over under than too any all one two three four".split())

    @staticmethod
    def _stem(w: str) -> str:
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                return w[: -len(suf)]
        return w

    def _tokens(self, text: str) -> set:
        import re as _re
        return {self._stem(w) for w in _re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in self._STOP}

    def _index(self):
        if getattr(self, "_idx", None) is None:
            import math
            docs = {}
            for n in self.eng.nodes.values():
                if n.get("type") != "violation" or n["code"] == "MISC_GENERAL":
                    continue
                kw = " ".join(n.get("keywords") or []) if isinstance(n.get("keywords"), list) else str(n.get("keywords") or "")
                text = " ".join([n["name"], n["name"], n.get("description") or "", kw,
                                 " ".join(self.examples.get(n["code"], []))])
                docs[n["code"]] = self._tokens(text)
            df = {}
            for toks in docs.values():
                for t in toks:
                    df[t] = df.get(t, 0) + 1
            N = len(docs)
            self._idx = (docs, {t: math.log(1 + N / c) for t, c in df.items()})
        return self._idx

    def candidates(self, story: str, k: int = 12) -> List[str]:
        """Top-k violation codes whose name / description / examples overlap the story (IDF-weighted)."""
        docs, idf = self._index()
        q = self._tokens(story)
        scored = []
        for code, toks in docs.items():
            hit = q & toks
            if hit:
                scored.append((sum(idf.get(t, 0) for t in hit), code))
        scored.sort(reverse=True)
        return [c for _, c in scored[:k]]

    def hint_lines(self, story: str, k: int = 10) -> List[str]:
        return [f"{c}={self.name(c)}: {(self.violation(c).get('description') or '')[:70]}" for c in self.candidates(story, k)]

    def fine_class(self, segment: Optional[str]) -> Optional[str]:
        return SEGMENT_FINE_CLASS.get(segment) if segment else None


@lru_cache(maxsize=1)
def get_kb() -> LawKB:
    return LawKB()
