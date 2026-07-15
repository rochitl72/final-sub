#!/bin/bash
# Run offline checks (no Ollama, no server required).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "▶ Smoke tests (dialog + protocol parser)…"
python3 backend/tests/test_v3_2_smoke.py

echo "▶ Device auth tests…"
python3 backend/tests/test_device_auth.py
python3 backend/tests/test_sarvam_tts.py

echo "▶ SQLite persistence selftest…"
python3 backend/persistence.py selftest

echo "▶ Service worker syntax…"
node --check apps/web/sw.js

echo ""
echo "✓ All checks passed"
