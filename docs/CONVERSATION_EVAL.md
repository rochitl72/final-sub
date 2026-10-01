# Conversation quality — evaluation & fixes

DriveLegal is evaluated end-to-end with `scripts/eval_conversations.py`: scripted
multi-turn conversations run against the real FastAPI app in every combination of

| | Rules engine (offline, `prefer_rules`) | Groq (cloud LLM) |
|---|---|---|
| **Chatbot** (`dynamic`) | ✅ | ✅ |
| **Calculator** (`static`) | ✅ | ✅ |

Each turn is checked for the expected fine card / vehicle / state, menus where an
answer was expected, guardrail hits **and** false positives, ₹ amounts grounded in the
knowledge graph, protocol/internal-code leakage, and LLM calls in rules mode.

## Results

| Config | Before | After |
|---|---|---|
| Chatbot · Rules | 14 / 45 scenarios | **100 / 100** |
| Chatbot · Groq | 19 / 45 | **100 / 100** |
| Calculator · Rules | 6 / 31 | **35 / 35** |
| Calculator · Groq | 6 / 31 | **35 / 35** |

(The suite grew from 45 to 100 Chatbot scenarios during the work, including a
46-message one-shot sweep across the violation catalogue.)

**Efficiency (Groq free tier: 8k tokens/min, 1k requests/day):** a typical Cloud
turn went from a ~1.7k-token narration prompt (+ reasoning) to a ~150-token lead-in
call, or no LLM call at all for follow-ups / recall / what-ifs / FAQ. Average
turn latency in the final run: **0.38 s**, zero rate-limit retries across 210 turns.

## What was wrong (root causes)

1. **Topic menu overuse** — small talk, info questions, recall ("what was the fine?"),
   "why is it so high?" all got a "which area is closest?" menu; it fired before the
   LLM, so Cloud mode failed them too.
2. **Narrow resolver vocabulary** — "red signal", "wrong side", "three of us on one bike",
   "black film", "had been drinking", Hinglish and typos weren't recognised.
3. **Scoring noise** — keywords matched inside words ("bac" in "back" → drunk driving);
   context bonuses turned 1-point coincidences into confident matches ("how much" ~
   "honking too much"); misconception sentences were indexed as keywords ("even at a
   red signal phone use counts" → red light = phone use); token overlap was summed over
   every synonym.
4. **Info routes swallowed offences** — "driving without a licence" / "insurance expired
   and police stopped me" got a document explainer instead of the fine.
5. **LLM hallucinations** — invented ₹ amounts (₹350 towing), wrong sections (red light
   cited as §183, phone use as "Section 2025"), leaked internal codes, assumed "car"
   when no vehicle was given, redundant questions.
6. **Missing conversation features** — follow-ups switching offence ("can they suspend my
   licence?" → no-licence fine), no vehicle what-ifs, no multi-offence handling,
   location in the message ignored, victims treated as offenders.
7. **Calculator ignored typed stories** — every message during slot-filling re-asked
   the road type.
8. **Data inconsistencies** — advice text quoting central amounts next to a city fine
   ("Pay ₹5,000" under a ₹1,000 card), "Repeat: varies" when the MV Act specifies it,
   36 duplicated cities.

## What changed

- **`backend/nlu.py`** — one deterministic understanding layer used by both modes and
  both engines: guardrail, small talk, grounded FAQ, recall, vehicle what-ifs,
  follow-up masking, multi-offence, LLM output validation.
- **Grounded composition** — when a violation is known, facts always come from the
  graph template; the LLM only adds a one-sentence, fact-free acknowledgement in the
  user's language. Open-question answers have ungrounded sentences removed. The
  vehicle is never taken from the LLM unless the user said it.
- **Model routing** — `openai/gpt-oss-20b` for lead-ins, `openai/gpt-oss-120b` for open
  questions (separate rate budget), automatic fallback if Groq retires a model.
- **Resolver** — +350 everyday/Hinglish synonyms, typo repair, distinct-token scoring,
  generic-phrase filter, rider/pillion · driver/passenger · refuse-test · stated-age
  disambiguation, vehicle-family switching (car → heavy-vehicle overspeeding).
- **Conversation** — follow-ups (compoundable, jail, repeat, licence, section, pay, bail,
  tips) about the current *or an earlier* offence, comparison ("difference between this
  and drug driving"), recap with totals, city/vehicle what-ifs, location in the message,
  victim vs offender, one-time driver-context checkboxes.
- **Calculator** — harvests vehicle/violation/road from typed stories, answers info
  questions then re-asks the pending chip question.
- **Data** — repeat fines/imprisonment filled from the broader schedule, advice clauses
  with contradicting amounts dropped (both ends of "₹1,000–5,000" ranges checked).
- **On-device parity** — the phone/PWA offline engine got the same resolver scoring,
  synonyms, typo repair, disambiguation, grounding, follow-ups, FAQ and guardrail
  (FAQ + guardrail are generated from `nlu.py` by `scripts/sync_offline_nlu.py`).

## Known limitations

- Rules mode can't reason about genuinely novel questions ("is it legal to drive
  barefoot?") — it offers the topic menu; Cloud mode answers them.
- Fine amounts are only as current as `data/compiled/drivelegal_graph.json` (+ patches).
- FAQ answers state national rules; state-specific procedures can differ.

## Running it

```bash
python3 scripts/eval_conversations.py                        # rules engine, ~2 s
python3 scripts/eval_conversations.py --engines rules groq   # + Groq (needs GROQ_CHAT_API_KEY)
python3 scripts/eval_conversations.py --only helmet          # filter scenarios
```

Full transcripts: `eval_out/eval_report.md`. `bash scripts/verify.sh` runs the rules
suite in strict mode, so conversation regressions fail the build.
