# DriveLegal — Setup guide

Step-by-step instructions for **new contributors** cloning this repo and testing the app on **Windows, macOS, or Linux**, using a phone with **Expo Go** (iOS or Android).

> **Demo scope:** This repository is configured for **easy demo testing** via **Expo Go** + a **free-tier Groq** API key + optional **Sarvam** keys. It is not a production App Store / Play Store build.

---

## What you need

| Item | Purpose |
|------|---------|
| **Python 3.11+** | FastAPI backend |
| **Node.js 18+** | Expo / Metro bundler |
| **Expo Go** on your phone | [iOS App Store](https://apps.apple.com/app/expo-go/id982107779) · [Google Play](https://play.google.com/store/apps/details?id=host.exp.exponent) |
| **Same Wi‑Fi** | Phone must reach your computer’s backend (`:8000`) |
| **Optional API keys** | Groq (free tier), Sarvam (TTS + translate) — see [API keys](#4-api-keys-optional-but-recommended) |

---

## Repository layout (after reorganisation)

```
final-sub/
├── README.md                 # Project overview
├── SETUP.md                  # This file
├── start.sh                  # One command: backend + Expo (macOS/Linux)
├── .env                      # Your secrets (create from config/.env.example)
├── apps/
│   ├── mobile/               # Expo React Native app (primary demo client)
│   └── web/                  # Browser PWA (optional)
├── backend/                  # FastAPI server + engine
├── data/
│   └── compiled/
│       └── drivelegal_graph.json   # Knowledge graph (~1.2 MB)
├── config/
│   └── .env.example
├── docs/
└── scripts/
    ├── verify.sh             # Run tests without starting servers
```

---

## 1. Clone and enter the project

**macOS / Linux**

```bash
cd ~/Downloads
git clone https://github.com/rochitl72/final-sub.git
cd final-sub
```

**Windows (PowerShell)**

```powershell
cd $env:USERPROFILE\Downloads
git clone https://github.com/rochitl72/final-sub.git
cd final-sub
```

---

## 2. Install prerequisites

### macOS

```bash
# Xcode Command Line Tools (if needed)
xcode-select --install

# Homebrew optional
brew install python node

python3 --version
node --version
```

### Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-pip nodejs npm curl

python3 --version
node --version
```

### Windows

1. Install [Python 3.11+](https://www.python.org/downloads/) — check **“Add to PATH”**.
2. Install [Node.js LTS](https://nodejs.org/).
3. Use **Git Bash** or **PowerShell** for commands below.

```powershell
python --version
node --version
```

---

## 3. Python dependencies (backend)

**macOS / Linux**

```bash
cd final-sub
python3 -m pip install -r backend/requirements.txt
```

**Windows**

```powershell
cd final-sub
python -m pip install -r backend\requirements.txt
```

Verify the graph file exists:

```bash
ls -la data/compiled/drivelegal_graph.json
```

---

## 4. API keys (optional but recommended)

Copy the template and edit:

**macOS / Linux**

```bash
cp config/.env.example .env
nano .env
```

**Windows**

```powershell
copy config\.env.example .env
notepad .env
```

| Variable | Used for | Demo note |
|----------|----------|-----------|
| `GROQ_CHAT_API_KEY` | Cloud AI narrate (gpt-oss-20b by default; override with `GROQ_CHAT_MODEL`) | **Free tier** — used in this demo |
| `SARVAM_API_KEY` | Hindi/Tamil TTS + translation | Strongly recommended for voice |
| `GROQ_API_KEY` | Gov.in patch scraper only | Optional |

Without keys: **rules-only mode** still works (graph + `offline_engine`).

---

## 5. Start everything (recommended — macOS / Linux)

From the repo root (`final-sub/`):

```bash
bash start.sh
```

This script:

1. Detects your LAN IP and writes `apps/mobile/.env`
2. Loads `.env` API keys
3. Installs npm packages in `apps/mobile/`
4. Starts FastAPI on **port 8000**
5. Starts **Expo Metro** and prints a **QR code**

When Metro is ready it prints a QR code in the terminal:

- **Metro URL:** `exp://<YOUR_LAN_IP>:8081`
- **Backend URL:** `http://<YOUR_LAN_IP>:8000`

---

## 6. Manual start (all platforms, including Windows)

Use **two terminals**.

### Terminal A — Backend

**macOS / Linux**

```bash
cd final-sub
set -a && source .env && set +a   # skip if no .env yet
cd backend
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

**Windows PowerShell**

```powershell
cd final-sub\backend
$env:PYTHONPATH = "."
# Load .env manually or set keys:
# $env:GROQ_CHAT_API_KEY = "gsk_..."
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Check health:

```bash
curl http://127.0.0.1:8000/api/health
```

### Terminal B — Expo (mobile)

1. Find your computer’s LAN IP:
   - **macOS:** `ipconfig getifaddr en0`
   - **Linux:** `hostname -I | awk '{print $1}'`
   - **Windows:** `ipconfig` → IPv4 Address

2. Create `apps/mobile/.env`:

```bash
# Replace with YOUR IP
echo EXPO_PUBLIC_API_BASE_URL=http://192.168.1.42:8000 > apps/mobile/.env
```

**macOS / Linux**

```bash
cd final-sub/apps/mobile
npm ci --legacy-peer-deps
npx expo install --fix
npx expo start --lan
```

**Windows**

```powershell
cd final-sub\apps\mobile
npm ci --legacy-peer-deps
npx expo install --fix
npx expo start --lan
```

---

## 7. Open the app on your phone (Expo Go only)

> **Testing is intended for Expo Go** — no native build or store signing required.

### iOS

1. Install **Expo Go** from the App Store.
2. Connect iPhone to the **same Wi‑Fi** as your computer.
3. Scan the **QR code** in the terminal (Camera app or Expo Go).
4. Allow local network access if prompted.

### Android

1. Install **Expo Go** from Google Play.
2. Same Wi‑Fi as your computer.
3. Scan the QR code **inside Expo Go** (or use Metro’s `a` for emulator).

### Android emulator only

The emulator cannot use your Mac’s LAN IP. Use:

```bash
echo EXPO_PUBLIC_API_BASE_URL=http://10.0.2.2:8000 > apps/mobile/.env
```

Then restart Expo.

---

## 8. Quick test checklist

| Step | Expected |
|------|----------|
| Open app | Home screen, no login |
| New chat → Calculator | Chips + fine card |
| New chat → Chatbot | Location → describe violation |
| Status pill | “Cloud AI” or “Rules (auto)” / “Rules only” |
| Language → Hindi | Translated reply text |
| Speaker icon | Voice in selected language (needs `SARVAM_API_KEY`) |
| Web browser | `http://<IP>:8000` serves PWA from `apps/web/` |

---

## 9. Run automated checks (no phone)

```bash
cd final-sub
bash scripts/verify.sh
```

---

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| Phone can’t connect | Same Wi‑Fi; disable VPN; check firewall allows **8000** and **8081** |
| `Groq AI: offline` in terminal | Normal without key or quota — rules engine still works |
| TTS fails | Add `SARVAM_API_KEY`, restart `start.sh`, reload app (`r` in Metro) |
| `TTS API error 400` | Restart backend after pulling latest (Bulbul v3 speaker fix) |
| Watchman warnings (macOS) | Optional: `watchman watch-del '/Users/you' ; watchman watch-project '/Users/you'` |
| Windows + phone | Use LAN IP in `.env`, not `127.0.0.1` |

Backend logs: `/tmp/drivelegal_api.log` when using `start.sh`.

---

## 11. 3D knowledge graph (no server)

One-click in your browser (public repo):

**https://htmlpreview.github.io/?https://raw.githubusercontent.com/rochitl72/final-sub/main/docs/visualizations/drivelegal_graph_3d.html**

Or open `docs/visualizations/drivelegal_graph_3d.html` locally.

## 12. Optional: web PWA

With backend running: `http://127.0.0.1:8000/` (serves `apps/web/`).

---

## Next steps

- Architecture & offline/online flows: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Backend module map: [backend/README.md](backend/README.md)
- Mobile app notes: [apps/mobile/README.md](apps/mobile/README.md)
