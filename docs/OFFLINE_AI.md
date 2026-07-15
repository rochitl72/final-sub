# DriveLegal — Offline AI Architecture (v3.3)

## The problem this fixes

Before v3.3, "offline mode" ran the rule engine **on the server**. Every chat
turn still needed a live HTTP call to the backend, so with the phone in airplane
mode the app could not answer at all — even though the pill said "using
rule-based replies." True offline capability was missing.

## The 3-tier cascade (now)

The device decides which tier answers. **Fine amounts always come from the local
graph — the model only narrates.** A model cannot invent a ₹ value.

```
Tier 1  CLOUD LLM  (Sarvam-M / Llama via the backend)   best quality · needs network
   │  no network / server unreachable / request fails ↓
Tier 2  ON-DEVICE SLM + local RAG over the graph         good quality · zero network · OPTIONAL
   │  no model registered / low-end device / gen fails ↓
Tier 3  DETERMINISTIC resolver + graph                   always available · exact fines
```

- **Tier 1** — unchanged server path (`dialogApi.turn`).
- **Tier 3** — `apps/mobile/src/offline/` : a faithful TypeScript port of the
  server's `violation_resolver` + `graph_engine` fine cascade + `offline_engine`
  narration, driven by a 193 KB bundle (`drivelegal_offline.json`, all 127
  violations / 36 states / 702 fines). **Verified bit-for-bit identical to the
  Python server** (see `scripts/` parity harness). This ships today and makes
  airplane mode work.
- **Tier 2** — `apps/mobile/src/offline/slm.ts` : a pluggable seam. OFF until a
  runtime is registered, so the app ships fully working on Tier 3 and lights up
  Tier 2 the moment a model is wired in.

## How the tiers are wired

`apps/mobile/src/offline/index.ts` → `localTurn()` returns a server-shaped
`TurnResponse`, so the chat UI renders an offline reply identically to an online
one. `ChatScreen` calls it whenever connectivity ≠ `server_ok`, and also as a
graceful fallback if a live request throws mid-flight.

## Wiring a real on-device model (Tier 2)

The seam is a 2-method interface:

```ts
import { setOnDeviceLLM } from './src/offline';

setOnDeviceLLM({
  isReady: () => myRuntime.loaded,
  generate: (prompt, opts) => myRuntime.complete(prompt, opts),
});
```

`narrateWithSLM()` builds a **RAG context** from the local graph (top matched
violations + their exact fines) and asks the model to answer using ONLY those
facts. The returned `fine_card` is still taken from `quickFine()` — graph-exact —
so the model can rephrase but never misquote an amount.

### Recommended runtimes (mid-2026)

| Platform | Runtime | Model |
|---|---|---|
| Android | Google AI Edge / MediaPipe LLM Inference | Gemma 3n E2B or Gemma 3 1B (Q4) |
| iOS + Android | Meta ExecuTorch | Llama 3.2 1B (Q4) |
| Web (PWA) | WebLLM (WebGPU) | Qwen2.5-0.5B / Llama 3.2 1B (Q4) |

### Honest constraints

- These runtimes need a **native build** (EAS dev client / bare workflow) — they
  do **not** run in Expo Go, and cannot be exercised in a pure-JS/CI sandbox.
  That is why Tier 2 ships as a verified seam with a mock-tested contract rather
  than a bundled model.
- Model weights (0.5–3 B, Q4 ≈ 0.3–2 GB) should be downloaded on first launch and
  cached, not shipped in the binary.
- Keep Tier 3 as the guaranteed floor for low-end devices.

## Regenerating the offline bundle

```
python3 scripts/build_offline_bundle.py   # → apps/mobile/src/offline/drivelegal_offline.json
```

Run this whenever `data/compiled/drivelegal_graph.json` changes.

## PWA note

The resolver / graph / engine modules are platform-agnostic TypeScript and port
directly to the web client; precache `drivelegal_offline.json` in `sw.js` and
call the same `localTurn` from the web chat send handler to give the PWA the same
airplane-mode capability.
