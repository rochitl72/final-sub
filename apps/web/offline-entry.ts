/**
 * offline-entry.ts — browser build entry for the PWA offline engine.
 * ------------------------------------------------------------------
 * Bundles the SAME verified offline core the mobile app uses (resolver + graph
 * fine-cascade + narration) into a single global for the vanilla-JS
 * web client. The <<import type>> from the mobile services layer is erased by
 * esbuild, so this pulls in only the platform-agnostic offline modules + data.
 *
 * Rebuild:
 *   npx esbuild apps/web/offline-entry.ts --bundle --format=iife \
 *     --global-name=DriveLegalOffline --loader:.json=json \
 *     --outfile=apps/web/offline.bundle.js --minify
 */

import { localTurn, narrate } from '../mobile/src/offline/index';

// Exposed as window.DriveLegalOffline by the IIFE global-name.
export { localTurn, narrate };
