#!/usr/bin/env bash
# DriveLegal — single command: install deps, load .env, start backend + web app.
#   bash run.sh                      # start (uses .env if present)
#   GROQ_CHAT_API_KEY=gsk_xxx bash run.sh   # also saves the key to local .env
#   bash run.sh test                 # run all checks instead
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
python3 -m pip install -q -r backend/requirements.txt 2>/dev/null \
  || python3 -m pip install -q --user -r backend/requirements.txt 2>/dev/null \
  || python3 -m pip install -q --break-system-packages -r backend/requirements.txt
[ -f .env ] || { [ -f .env.example ] && cp .env.example .env || touch .env; }
if [ -n "$GROQ_CHAT_API_KEY" ] && ! grep -q "^GROQ_CHAT_API_KEY=." .env; then
  sed -i.bak '/^GROQ_CHAT_API_KEY=/d' .env && rm -f .env.bak
  echo "GROQ_CHAT_API_KEY=$GROQ_CHAT_API_KEY" >> .env; chmod 600 .env
fi
set -a; . ./.env; set +a
if [ "$1" = "test" ]; then
  python3 -m pytest -q backend/tests tests 2>&1 | tail -3
  python3 scripts/eval_conversations.py --engines rules $([ -n "$GROQ_CHAT_API_KEY" ] && echo groq --pace 3)
  exit
fi
# The DriveLegal PWA (apps/pwa — React + Vite + Tailwind), served by the backend at "/". Needs Node 18+.
# Rebuilt automatically when there is no build yet or any source is newer than it.
#   UI development with hot reload:  (cd apps/pwa && npm run dev)  → http://localhost:5173 (proxies /api to :8000)
_pwa_stale=0
if [ ! -f apps/pwa/dist/sw.js ]; then _pwa_stale=1
elif [ -n "$(find apps/pwa/src apps/pwa/public apps/pwa/index.html apps/pwa/package.json apps/pwa/vite.config.ts \
              apps/pwa/tailwind.config.js -newer apps/pwa/dist/sw.js -type f 2>/dev/null | head -1)" ]; then _pwa_stale=1; fi
if [ "$_pwa_stale" = 1 ]; then
  if command -v npm >/dev/null 2>&1; then
    echo ">>> Building the DriveLegal PWA (~30 s)… log: /tmp/drivelegal-pwa-build.log"
    if (cd apps/pwa && NODE_ENV=development npm install --include=dev --no-audit --no-fund && npm run build) >/tmp/drivelegal-pwa-build.log 2>&1; then
      echo ">>> PWA built."
    else
      echo "!! PWA build failed — see /tmp/drivelegal-pwa-build.log"
    fi
  else
    echo "!! npm not found — install Node 18+ to build the PWA (the API still runs)."
  fi
fi
pkill -f "uvicorn api:app" 2>/dev/null || true
cd backend
( sleep 3; echo; curl -s "localhost:$PORT/api/health"; echo; echo ">>> Open http://localhost:$PORT" ) &
exec python3 -m uvicorn api:app --host 0.0.0.0 --port "$PORT"
