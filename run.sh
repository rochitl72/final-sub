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
pkill -f "uvicorn api:app" 2>/dev/null || true
cd backend
( sleep 3; echo; curl -s "localhost:$PORT/api/health"; echo; echo ">>> Open http://localhost:$PORT" ) &
exec python3 -m uvicorn api:app --host 0.0.0.0 --port "$PORT"
