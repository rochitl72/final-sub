# Scenario Engine — multi-person, multi-offence reasoning

Answers stories such as *"my 16-year-old son took my bike without asking, rode without a helmet, and hit a car"*
person by person, with every fine and section computed from the law database — never invented by an LLM.

```
text → extract (Groq JSON + evidence quotes in AI mode · rule extractor offline)
     → scenario graph (actors, vehicles, events, facts, asks)
     → candidate offences → required-fact checks (applies / conditional / excluded)
     → liability roles (driver, owner, guardian, passenger, conductor…)
     → s.199A juvenile rule → relations (implies / aggravates / subsumes / alternative)
     → fines (city → state → central)
     → question planner (≤1 question, only if the answer changes the outcome)
     → grounded composer (per-person cards, "May also apply", next steps, assumptions)
```

## Data
`data/drivelegal.db` (SQLite) is the source of truth. `scripts/export_graph.py` regenerates
`data/compiled/drivelegal_graph.json` (still used by the offline bundle and the 3D viewer).
`scripts/apply_law_enrichment.py` applies `data/law/enrichment.json` (provisions, liability, facts,
conditions, relations) — idempotent. **These rows were authored with LLM assistance: re-verify against
India Code before relying on them legally.**

## Evaluation (`scripts/eval_scenarios.py`)
Gold set `data/law/gold_scenarios.json`: G01–G60 (dev), H01–H30 (dev2), B01–B20 (blind when written).
Numbers on 2026-10-05 — only the first blind run of a set is an honest estimate:

| Engine | Set | Recall | Person attribution | Precision |
|---|---|---|---|---|
| rules (offline) | all 110 (tuned) | 0.91 | 0.88 | 0.98 |
| rules | B01–B20, first blind run | 0.64 | 0.49 | 0.77 |
| Groq | B01–B20, first blind run | 0.87 | 0.77 | 0.85 |
| Groq | B01–B20 after retrieval hints (tuned on) | 0.92 | 0.87 | 0.94 |
| Groq | C01–C20, fresh blind set | **0.91** | 0.88 | 0.89 |

Gates in `verify.sh` run the dev split offline. Write a fresh blind set before quoting new accuracy.

## Interaction
Answers to its own question, corrections ("actually he is 19", "the owner didn't know") and new stories all
go through `scenario.maybe_handle`; the API returns `scenario.people[]` (+ `head` / `tail` text) which the
web page and the mobile app render as per-person cards. The offline (rule-based) phone/PWA fallback does not include the scenario engine.
