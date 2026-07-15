# DriveLegal Web PWA

Static progressive web app served by FastAPI at `/` when the backend runs.

## Files

- `index.html` — main UI (chips, map, chat)
- `sw.js` — service worker
- `manifest.webmanifest`

## Run

Start the backend (see root [SETUP.md](../../SETUP.md)), then open:

```text
http://127.0.0.1:8000/
```

No separate npm install for the web client in normal demo flow.
