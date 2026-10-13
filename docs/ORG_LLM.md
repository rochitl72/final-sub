# Running DriveLegal on the org's Ollama server

Target: `http://10.42.235.232:11434/api/generate` (self-hosted Ollama, org network only).

## 1. Where the LLM is used today

| Call | File | What it does | Fallback if the LLM fails |
|---|---|---|---|
| Story understanding | `scenario/extract_llm.py` | story → people / vehicles / offences / facts JSON | rules extractor (`extract_rules.py`) |
| Lead sentence | `dynamic_chatbot._llm_lead` | one friendly line above a fine card, no numbers | no lead |
| Open questions | `llm_chatbot.handle_freeform` / `call_groq_narrate` | traffic questions with no single matching rule | offline engine / topic menu |
| Translation | `llm_chatbot.groq_translate` (only when Sarvam fails) | translate an answer | English answer stays |
| Law-update scraper | `update_orchestrator._call_groq` | read gov web pages into fine changes | skip that source |

Fines, sections, liability and totals never come from the LLM. That is what makes swapping the model safe.

## 2. What changed

* `backend/llm_provider.py` — one provider layer. `LLM_PROVIDER=ollama|groq` (unset → ollama when `OLLAMA_URL` is set). Every call above goes through it.
* Ollama client: `/api/generate` (or `/api/chat` with `OLLAMA_API=chat`), `format:"json"` for extraction, `keep_alive` so the model stays loaded, `num_ctx` large enough for the extraction prompt (~3k tokens), thinking output discarded.
* Production safety: bounded concurrency per backend (`OLLAMA_MAX_PARALLEL`), circuit breaker (3 failures → rules only for 30 s), per-call timeouts (25 s for short calls, `OLLAMA_TIMEOUT` otherwise), health from `/api/tags` (`/api/health` now also reports `llm_provider`, `llm_ok`).
* Model-agnostic guards (on by default with Ollama):
  * **Evidence guard (strict):** an offence the LLM claims must also be supported by the law-DB retrieval or the rules reader. Stops a weaker model from adding offences that never happened.
  * **Schema-echo guard:** a label copied from the prompt's example is discarded.
  * **Translation gate:** output must be mostly in the target script and keep every number and ₹ amount, else the English answer is kept.
* Groq-only rate-limit retries are skipped for Ollama (no token quota).
* `scripts/conversation_suite.py` — 21 multi-turn conversations with per-turn checks (offence codes shown, answer replaced in place, location, refusals). Rules mode runs in `verify.sh`.
* `scripts/llm_bench.py` — benchmark for the org server (see 3).

## 3. Benchmark the server (run on a machine on the org network)

```bash
cd ~/drivelegal/backend/final-sub/final-sub
python3 scripts/llm_bench.py                     # auto-picks up to 3 models, ~30–60 min depending on GPU
python3 scripts/llm_bench.py --quick             # ~10 min smoke run
python3 scripts/llm_bench.py --models qwen2.5:14b,llama3.1:8b
```

Writes `~/Downloads/drivelegal_llm_bench_<date>.json` and `.md`.

### Go / no-go thresholds for production

| Metric | Minimum | Why |
|---|---|---|
| Story recall (strict guard) | ≥ rules-only baseline + 0.05 | the LLM must add value over rules |
| Precision (strict guard) | ≥ 0.90 | wrong offences are worse than missed ones |
| JSON ok rate | ≥ 0.95 | below this most stories fall back to rules |
| p95 extraction latency | ≤ 8 s | longer and users wait too long for the first answer |
| p50 one-line answer | ≤ 2 s | lead sentences are on every fine card |
| Conversation suite core | all pass | no regressions vs rules |
| Translation pass rate | ≥ 0.9 (else keep Sarvam-only) | broken translations are rejected anyway |
| Injection resisted | yes | |

## 4. Rollout

1. **Benchmark** the server's models; pick the best by the table above. If one model is accurate but slow, set it as `OLLAMA_EXTRACT_MODEL` and a smaller one as `OLLAMA_MODEL`.
2. **Configure** `.env`: `LLM_PROVIDER=ollama`, `OLLAMA_URL=…`, `OLLAMA_MODEL=…`. Restart; `/api/health` shows `llm_provider: ollama, llm_ok: true`.
3. **Shadow week (recommended):** keep answering from the current provider, log the org model's extraction for the same stories, compare offline (`llm_bench` scoring code).
4. **Switch**, watch: breaker openings, p95 latency, JSON failures, rule fallbacks per day.
5. Retire the Groq key from production config.

## 5. Benchmark of the org server (2026-10-08, `--quick`, 10 held-out stories)

| | recall | precision | attribution | JSON ok | p50 one-liner | p50 story read | tok/s | conv core | translation |
|---|---|---|---|---|---|---|---|---|---|
| rules only | 0.842 | 1.00 | 0.789 | – | – | – | – | 22/22 | – |
| **llama3.1:8b** | **0.947** | 0.87 | **0.947** | 0.90 | 1.6 s | 35.8 s (p95 60 s) | 16 | 20/22 | 2/4 |
| llama3.2:1b | 0.842 | 0.83 | 0.789 | 0.40 | 0.4 s | 14.8 s | 69 | 21/22 | 0/4 |

Reading: llama3.1:8b genuinely helps story understanding (+0.10 recall, +0.16 attribution: it caught the expired
insurance, the drunk auto driver and the 140-in-80 speeding that rules missed) but adds wrong offences (precision 0.87)
and takes 36–60 s per story — the server generates ~16 tokens/s (CPU or a small/shared GPU). llama3.2:1b adds nothing
over rules and fails JSON 60% of the time. Translation from both models is unreliable; the gate rejected 2/4 and 4/4.
One 8B open answer was wrong on procedure ("police can take your keys … one-time penalty").

### Decisions taken from these numbers
1. **Model: llama3.1:8b** for everything; llama3.2:1b not used.
2. **Never make a user wait for the 8B model.** Stories are answered instantly from rules; the model reads the story in
   the background and, if it finds more, the same answer is replaced in place (`LLM_ASYNC_EXTRACT`, default on for
   Ollama; app polls `/api/session/{id}/updates`; a newer user turn always wins over a stale refinement).
3. **Smaller prompt for self-hosted models:** the story-specific catalogue (retrieval + rules + everyday offences)
   halves the prompt (≈3,000 → ≈1,600 tokens), which roughly halves prompt-reading time and gives the model fewer
   wrong codes to choose from.
4. **Precision fixes from the observed errors:** phone use needs a phone/call cue ("stopped and called 108" no longer
   counts), no-permit needs the word permit, hazardous goods need a hazardous cue, seat-belt offences need a seat-belt cue.
5. **Translation stays on Sarvam** (it has credits); the model is only a gated fallback.
6. **Open answers written by the model** carry a visible note that they're general AI guidance, not from the law
   database.

### Production `.env`
```
LLM_PROVIDER=ollama
OLLAMA_URL=http://10.42.235.232:11434/api/generate
OLLAMA_MODEL=llama3.1:8b
OLLAMA_TIMEOUT=90          # background story reading; short calls are capped at 25 s
OLLAMA_MAX_PARALLEL=2
```

### Ask the org infra team
* Is the model on a GPU? 16 tok/s for an 8B Q4 model suggests CPU. On any modern GPU it would be 60–120 tok/s and
  story reading would drop to ~5 s, making the background step near-instant.
* If possible, add `qwen2.5:7b` or `qwen2.5:14b` (stronger at JSON / instruction following) and rerun `llm_bench.py`.
* Pin the model tag and keep `OLLAMA_KEEP_ALIVE` so it isn't unloaded between users (cold load was 7.8 s).

## 6. Improvements this enables

* No rate limits → the LLM can also classify every follow-up (new story / correction / what-if / explain / total / location change), which was the biggest weakness in the multi-turn audit.
* Data never leaves the org network (good for privacy review).
* Pin the model version on the server (`ollama pull` a fixed tag) so behaviour doesn't drift; rerun `llm_bench.py` before any model upgrade.
