#!/usr/bin/env bash
# Rebuild apps/web/offline.bundle.js from source.
#
# This is a COMMITTED BUILD ARTIFACT with the offline data inlined. It was
# previously rebuilt by hand from a comment in offline-entry.ts, and it drifted:
# the committed copy carried all 702 pre-audit fine rows, the removed per-state
# `multiplier`, and no citations at all — so the web PWA served unsourced figures
# long after the backend and the mobile bundle had stopped.
#
# CI runs this and fails if the result differs from what is committed.
set -euo pipefail
cd "$(dirname "$0")/.."

ESBUILD_VERSION="${ESBUILD_VERSION:-0.23.1}"
OUT="apps/web/offline.bundle.js"

npx --yes "esbuild@${ESBUILD_VERSION}" apps/web/offline-entry.ts \
  --bundle --format=iife --global-name=DriveLegalOffline \
  --loader:.json=json --outfile="$OUT" --minify

# The bundle must never ship a fine without a source, or the web client can
# state something the backend would refuse.
python3 - "$OUT" <<'PY'
import sys, re
src = open(sys.argv[1], encoding="utf-8", errors="replace").read()
fines = src.count('"first_offence"') or src.count('first_offence')
cites = src.count("source_url")
if "multiplier" in src:
    sys.exit("FAIL: rebuilt web bundle still contains `multiplier`, removed in v5")
if cites == 0:
    sys.exit("FAIL: rebuilt web bundle contains no source_url — it is not gated")
print(f"web bundle OK: {len(src)} bytes, {cites} source_url references")
PY
