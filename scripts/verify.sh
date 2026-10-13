#!/bin/bash
# Run offline checks (no Ollama, no server required).
set -eo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "▶ Smoke tests (dialog + protocol parser)…"
python3 backend/tests/test_v3_2_smoke.py

echo "▶ Device auth tests…"
python3 backend/tests/test_device_auth.py
python3 backend/tests/test_sarvam_tts.py

echo "▶ v3.3 regression + structured-output tests…"
python3 backend/tests/test_bugfixes_v33.py
python3 backend/tests/test_structured_output_v33.py

echo "▶ Offline robustness (no cloud AI) tests…"
python3 backend/tests/test_offline_robustness.py

echo "▶ Conversation eval — every scenario, offline rules engine (both modes)…"
python3 scripts/eval_conversations.py --engines rules --strict --out "${TMPDIR:-/tmp}/drivelegal_eval" \
  2>&1 | grep -E "FAIL|by_config|passed" | tail -12

echo "▶ Scenario engine tests + gold-set gates (offline rules engine)…"
# Hermetic: the test fakes the LLM, so a configured live server must not leak in.
env -u OLLAMA_URL -u LLM_PROVIDER python3 backend/tests/test_scenario_engine.py
python3 scripts/eval_scenarios.py --engines rules --split dev --strict --out "${TMPDIR:-/tmp}/drivelegal_scn" | tail -9

echo "▶ LLM provider switch (offline unit tests)…"
python3 backend/tests/test_llm_provider.py

echo "▶ Multi-turn conversation suite (rules, core checks must pass)…"
env -u OLLAMA_URL -u LLM_PROVIDER python3 scripts/conversation_suite.py --strict 2>&1 | grep -E "FAIL|^core" | tail -12

echo "▶ SQLite persistence selftest…"
python3 backend/persistence.py selftest

if [ -d apps/pwa/node_modules ]; then
  echo "▶ PWA type check + production build…"
  (cd apps/pwa && npm run build >/tmp/drivelegal-pwa-verify.log 2>&1) || { tail -30 /tmp/drivelegal-pwa-verify.log; exit 1; }
  test -f apps/pwa/dist/sw.js
else
  echo "▶ PWA build skipped (run: cd apps/pwa && npm install)"
fi

echo ""
echo "✓ All checks passed"
