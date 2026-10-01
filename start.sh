#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# DriveLegal — one-shot launcher
# Starts the FastAPI backend + Expo mobile dev server
# Run from the repo root: bash start.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 1. Find local IP ─────────────────────────────────────────────────────────
# macOS: ipconfig · Linux: hostname -I / ip route · fallback: loopback
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null \
  || ipconfig getifaddr en1 2>/dev/null \
  || hostname -I 2>/dev/null | awk '{print $1}' \
  || true)
if [ -z "$LOCAL_IP" ]; then
  LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
fi
LOCAL_IP=${LOCAL_IP:-127.0.0.1}
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          DriveLegal Launcher             ║"
echo "╠══════════════════════════════════════════╣"
echo "║  LAN IP  : $LOCAL_IP"
echo "║  Backend : http://$LOCAL_IP:8000"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 2. Write .env for the mobile app ─────────────────────────────────────────
echo "EXPO_PUBLIC_API_BASE_URL=http://$LOCAL_IP:8000" > "$DIR/apps/mobile/.env"
echo "✅  Wrote apps/mobile/.env with local IP"

# ── 3. Install Python deps (silent if already installed) ─────────────────────
echo ""
echo "📦  Checking Python deps…"
REQ="$DIR/backend/requirements.txt"
python3 -m pip install --quiet -r "$REQ" 2>/dev/null \
  || python3 -m pip install --quiet --user -r "$REQ" 2>/dev/null \
  || python3 -m pip install --quiet --break-system-packages -r "$REQ" 2>/dev/null \
  || echo "⚠️   pip install failed — run: python3 -m pip install -r backend/requirements.txt"
echo "✅  Python deps OK"

# ── Load API keys from .env ───────────────────────────────────────────────────
if [ ! -f "$DIR/.env" ] && [ -f "$DIR/.env.example" ]; then
  echo "⚠️   No .env found — copying from .env.example"
  cp "$DIR/.env.example" "$DIR/.env"
  echo "   Edit $DIR/.env and add your API keys, then re-run start.sh"
fi

if [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
  # Log which keys are loaded (mask value)
  [ -n "$SARVAM_API_KEY"    ] && echo "✅  SARVAM_API_KEY      loaded" || echo "⚠️   SARVAM_API_KEY      not set (Sarvam features disabled)"
  [ -n "$GROQ_CHAT_API_KEY" ] && echo "✅  GROQ_CHAT_API_KEY   loaded" || echo "⚠️   GROQ_CHAT_API_KEY   not set (Groq fallback disabled)"
  [ -n "$GROQ_API_KEY"      ] && echo "✅  GROQ_API_KEY        loaded" || echo "⚠️   GROQ_API_KEY        not set (gov.in scraper disabled)"
else
  echo "⚠️   No .env file — all cloud AI features disabled, offline mode only"
fi

# ── 4. Install Node deps ─────────────────────────────────────────────────────
echo ""
echo "📦  Installing Node deps…"
cd "$DIR/apps/mobile"

if [ -f package-lock.json ]; then
  npm ci --legacy-peer-deps
else
  npm install --legacy-peer-deps
fi

# Align native modules with the installed Expo SDK (uses package.json pins)
npx expo install --fix --non-interactive 2>/dev/null || true

echo "✅  Node deps installed"

# ── 5. Start FastAPI backend in background ───────────────────────────────────
echo ""
echo "🚀  Starting FastAPI backend on port 8000…"

# Kill any old instance first
pkill -f "uvicorn api:app" 2>/dev/null || true
sleep 1

cd "$DIR/backend"
python3 -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload > /tmp/drivelegal_api.log 2>&1 &
API_PID=$!
# Stop the backend when this script exits (Ctrl+C in Expo included).
trap "kill $API_PID 2>/dev/null; pkill -f 'uvicorn api:app' 2>/dev/null; echo 'Stopped.'" EXIT

# Wait up to 8s for backend to respond
echo "   Waiting for backend to be ready…"
for i in $(seq 1 8); do
  sleep 1
  if curl -sf "http://127.0.0.1:8000/api/health" 2>/dev/null | grep -q '"app"[[:space:]]*:[[:space:]]*"drivelegal"'; then
    echo "✅  DriveLegal backend is up on :8000"
    # Check Groq connectivity
    if curl -sf "http://127.0.0.1:8000/api/health" 2>/dev/null | grep -q '"groq_ok"[[:space:]]*:[[:space:]]*true'; then
      echo "✅  Groq AI: online ($(curl -sf http://127.0.0.1:8000/api/health | sed -n 's/.*"model":"\([^"]*\)".*/\1/p'))"
    else
      echo "⚠️  Groq AI: offline — using local rule-based fallback"
    fi
    break
  fi
  if [ $i -eq 8 ]; then
    echo "⚠️  Backend didn't start in time. Check logs:"
    tail -20 /tmp/drivelegal_api.log
    echo ""
    echo "   Try: python3 -m pip install -r backend/requirements.txt"
  fi
done

# ── 7. Start Expo ─────────────────────────────────────────────────────────────
echo ""
echo "📱  Starting Expo — scan the QR code in Expo Go (iOS or Android)…"
echo ""
cd "$DIR/apps/mobile"

# Remove stale reanimated if present (babel-preset-expo auto-loads it otherwise)
rm -rf node_modules/react-native-reanimated 2>/dev/null || true

EXPO_PUBLIC_API_BASE_URL="http://$LOCAL_IP:8000" npx expo start --lan --clear
