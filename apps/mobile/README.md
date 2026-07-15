# DriveLegal Mobile (Expo)

React Native client for DriveLegal — **primary demo surface** via **Expo Go** on iOS and Android.

## Features

- Device-ID auth (no login screen)
- Calculator + AI chat modes
- 11 Indian languages (Sarvam translate)
- TTS on bot messages (Sarvam Bulbul v3)
- GPS / map / browse location picker
- AI mode pill (Cloud / Rules / connectivity)

## Setup

**Do not read only this file** — follow the repo guide:

**[../../SETUP.md](../../SETUP.md)**

Quick path (macOS/Linux, from repo root):

```bash
cp config/.env.example .env
bash start.sh
```

## Structure

```
apps/mobile/
├── app/                 # expo-router screens
├── src/
│   ├── screens/         # Home, Chat
│   ├── components/      # Bubbles, FineCard, TtsButton, …
│   ├── services/        # api.ts, sarvamApi.ts
│   ├── store/           # auth, language, AI mode
│   └── hooks/           # useAiConnectivity
└── assets/
```

## Config

`apps/mobile/.env` (auto-written by `start.sh`):

```bash
EXPO_PUBLIC_API_BASE_URL=http://<YOUR_LAN_IP>:8000
```

## Scripts

```bash
cd apps/mobile
npm ci --legacy-peer-deps
npx expo start          # LAN QR for physical device
npx expo start --android
npx expo start --ios
```

## Platform notes

| Platform | Notes |
|----------|--------|
| iOS + Expo Go | Scan QR from terminal |
| Android + Expo Go | Scan QR inside Expo Go app |
| Android emulator | Use `http://10.0.2.2:8000` in `.env` |

Same codebase on both — no iOS-only features.
