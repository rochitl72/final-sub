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
python3 backend/tests/test_scenario_engine.py
python3 scripts/eval_scenarios.py --engines rules --split dev --strict --out "${TMPDIR:-/tmp}/drivelegal_scn" | tail -9

echo "▶ SQLite persistence selftest…"
python3 backend/persistence.py selftest

echo "▶ Service worker + offline bundle syntax…"
node --check apps/web/sw.js
node --check apps/web/offline.bundle.js

if [ -d apps/mobile/node_modules ]; then
  echo "▶ Mobile TypeScript check…"
  (cd apps/mobile && npx tsc --noEmit -p .)
else
  echo "▶ Mobile TypeScript check skipped (run npm ci in apps/mobile first)"
fi

echo ""
echo "✓ All checks passed"
