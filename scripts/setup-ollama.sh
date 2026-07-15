#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# DriveLegal — One-shot setup & launcher (macOS / Linux)
#
# What this does:
#   1. Installs Ollama (if not present)
#   2. Starts the Ollama daemon
#   3. Downloads phi3.5 model (~2.2 GB, runs ONCE, fully offline after)
#   4. Installs Python deps (fastapi, uvicorn, requests)
#   5. Starts the DriveLegal server on http://localhost:8000
#
# Note: the compiled graph (drivelegal/data/compiled/drivelegal_graph.json) is
# shipped pre-built — no per-machine build step is required.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# To use a different model (e.g. llama3.2:3b for less RAM):
#   DRIVELEGAL_MODEL=llama3.2:3b ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$ROOT"
MODEL="${DRIVELEGAL_MODEL:-phi3.5}"

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║   DriveLegal — Offline Road Law Chatbot   ║"
echo "╚════════════════════════════════════════════╝"
echo "  Model  : $MODEL"
echo "  Project : $SCRIPT_DIR"
echo ""

# ── Step 1: Ollama ────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
  echo "▶ Installing Ollama …"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS — download the app or use Homebrew
    if command -v brew &>/dev/null; then
      brew install ollama
    else
      echo "  → Please install Ollama from https://ollama.com/download and re-run."
      echo "    Or install Homebrew first: https://brew.sh"
      exit 1
    fi
  else
    # Linux
    curl -fsSL https://ollama.com/install.sh | sh
  fi
else
  echo "✓ Ollama $(ollama --version 2>/dev/null || echo 'installed')"
fi

# ── Step 2: Start Ollama daemon ───────────────────────────────────────────────
if ! curl -sf http://localhost:11434/api/version &>/dev/null; then
  echo "▶ Starting Ollama daemon …"
  ollama serve >>/tmp/drivelegal_ollama.log 2>&1 &
  echo "  Waiting for Ollama to be ready …"
  for i in {1..15}; do
    sleep 1
    curl -sf http://localhost:11434/api/version &>/dev/null && break
    printf "  ."
  done
  echo ""
fi
echo "✓ Ollama is running"

# ── Step 3: Pull the model ────────────────────────────────────────────────────
if ollama list 2>/dev/null | grep -q "$MODEL"; then
  echo "✓ Model '$MODEL' already downloaded"
else
  echo ""
  echo "▶ Downloading '$MODEL' (~2.2 GB for phi3.5) — runs once, fully offline after …"
  echo "  This may take 5–15 minutes on first run depending on your internet speed."
  echo ""
  ollama pull "$MODEL"
  echo "✓ $MODEL downloaded"
fi

# ── Step 4: Python dependencies ───────────────────────────────────────────────
echo ""
echo "▶ Checking Python dependencies …"
python3 -c "import fastapi, uvicorn, requests" 2>/dev/null || {
  echo "  Installing fastapi, uvicorn, requests …"
  pip3 install fastapi uvicorn requests --break-system-packages -q
}
echo "✓ Python deps ready"

# ── Step 5: Verify compiled graph is present ─────────────────────────────────
GRAPH="$SCRIPT_DIR/data/compiled/drivelegal_graph.json"
if [ ! -f "$GRAPH" ]; then
  echo "✗ Missing compiled graph: $GRAPH"
  echo "  The repo should ship drivelegal_graph.json — re-clone if missing."
  exit 1
fi
echo "✓ Graph ready ($(wc -c < "$GRAPH" | tr -d ' ') bytes)"

# ── Step 6: Launch ────────────────────────────────────────────────────────────
echo ""
echo "╔════════════════════════════════════════════╗"
echo "║   DriveLegal starting on port 8000 …     ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "  Open in browser → http://localhost:8000  (Saathi uses :8080 — separate app)"
echo "  API docs        → http://localhost:8000/docs"
echo "  Stop server     → Ctrl+C"
echo ""
echo "  Model in use    : $MODEL"
echo "  Ollama logs     : /tmp/drivelegal_ollama.log"
echo ""

export DRIVELEGAL_MODEL="$MODEL"
cd "$SCRIPT_DIR/backend"
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
