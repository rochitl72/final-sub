/**
 * updateService.ts — Law dataset auto-update scheduler
 * ──────────────────────────────────────────────────────
 * Responsibilities:
 *  • Track the last time the user approved an update (AsyncStorage)
 *  • Determine when to show the UpdateBanner (≥ 7 days since last run,
 *    or user-configured longer interval)
 *  • Trigger the backend update cycle and download resulting patches
 *  • Cache patches locally so they survive offline sessions
 *
 * The base graph on the backend is NEVER modified.  Patches are stored
 * separately in data_patches (SQLite) and downloaded to AsyncStorage here.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { updateApi, UpdateStatus, DataPatch } from './api';

// ── Storage keys ──────────────────────────────────────────────────────────────

const KEY_LAST_UPDATE_MS   = '@drivelegal/update/lastRunMs';
const KEY_REMIND_AFTER_MS  = '@drivelegal/update/remindAfterMs';
const KEY_PATCHES_CACHE    = '@drivelegal/update/patchesCache';
const KEY_PATCHES_FETCH_MS = '@drivelegal/update/patchesFetchMs';

// ── Default interval ──────────────────────────────────────────────────────────

const DEFAULT_MIN_DAYS   = 7;
const MS_PER_DAY         = 86_400_000;
const PATCH_CACHE_TTL_MS = 7 * MS_PER_DAY;   // re-fetch patches every 7 days

// ── Banner decision ───────────────────────────────────────────────────────────

/**
 * Returns true if the UpdateBanner should be shown.
 * Checks both the local "last approved" timestamp and the backend status.
 */
export async function shouldShowBanner(): Promise<boolean> {
  // 1. Check local remind-after gate first (user said "remind me in X days")
  const remindAfterStr = await AsyncStorage.getItem(KEY_REMIND_AFTER_MS);
  if (remindAfterStr) {
    const remindAfter = parseInt(remindAfterStr, 10);
    if (Date.now() < remindAfter) {
      return false;  // User asked us to wait — respect it
    }
  }

  // 2. Check backend status (needs network — skip if offline)
  try {
    const status = await updateApi.status();
    if (!status) return false;

    // Show banner if:
    //  - never updated (days_since_run is null), OR
    //  - it's been ≥ 7 days since the last successful update
    if (status.days_since_run === null) return true;
    return status.days_since_run >= DEFAULT_MIN_DAYS;
  } catch {
    return false;   // Offline — don't show banner
  }
}

/**
 * Returns the UpdateStatus from the backend, or null if offline.
 */
export async function getUpdateStatus(): Promise<UpdateStatus | null> {
  return updateApi.status();
}

// ── User actions ──────────────────────────────────────────────────────────────

/**
 * Trigger the backend update cycle.
 * Returns a summary of what happened (or null on network error).
 */
export async function runUpdate(): Promise<{ ok: boolean; message: string } | null> {
  try {
    const result = await updateApi.run();
    if (result.ok) {
      await AsyncStorage.setItem(KEY_LAST_UPDATE_MS, String(Date.now()));
    }
    return result;
  } catch {
    return null;
  }
}

/**
 * User tapped "Remind me in X days" — suppress the banner until then.
 */
export async function remindLater(days: number): Promise<void> {
  const remindAt = Date.now() + days * MS_PER_DAY;
  await AsyncStorage.setItem(KEY_REMIND_AFTER_MS, String(remindAt));
}

/**
 * Dismiss the banner permanently this session (store nothing — banner
 * will reappear after 7 days on next app open).
 */
export async function dismissForSession(): Promise<void> {
  // Suppress for 1 day (session-level dismiss)
  await remindLater(1);
}

// ── Patch cache ───────────────────────────────────────────────────────────────

/**
 * Download all patches from the backend and cache in AsyncStorage.
 * Returns the patches array (empty if offline or backend error).
 */
export async function fetchAndCachePatches(): Promise<DataPatch[]> {
  try {
    const lastFetchStr = await AsyncStorage.getItem(KEY_PATCHES_FETCH_MS);
    const lastFetch    = lastFetchStr ? parseInt(lastFetchStr, 10) : 0;
    const stale        = Date.now() - lastFetch > PATCH_CACHE_TTL_MS;

    if (!stale) {
      // Serve from cache
      const cached = await AsyncStorage.getItem(KEY_PATCHES_CACHE);
      return cached ? JSON.parse(cached) : [];
    }

    const patches = await updateApi.getPatches();
    await AsyncStorage.setItem(KEY_PATCHES_CACHE,    JSON.stringify(patches));
    await AsyncStorage.setItem(KEY_PATCHES_FETCH_MS, String(Date.now()));
    return patches;
  } catch {
    // Offline — return cached patches
    try {
      const cached = await AsyncStorage.getItem(KEY_PATCHES_CACHE);
      return cached ? JSON.parse(cached) : [];
    } catch {
      return [];
    }
  }
}

/**
 * Return cached patches without hitting the network.
 */
export async function getCachedPatches(): Promise<DataPatch[]> {
  try {
    const cached = await AsyncStorage.getItem(KEY_PATCHES_CACHE);
    return cached ? JSON.parse(cached) : [];
  } catch {
    return [];
  }
}

/**
 * Look up any patches for a specific violation+state from the local cache.
 * Returns patches sorted most-recent first.
 */
export async function getPatchesForViolation(
  violationCode: string,
  stateCode?: string,
): Promise<DataPatch[]> {
  const all = await getCachedPatches();
  return all
    .filter(
      (p) =>
        p.violation_code === violationCode &&
        (p.state_code === null || p.state_code === stateCode),
    )
    .sort((a, b) => {
      if (a.effective_date && b.effective_date)
        return b.effective_date.localeCompare(a.effective_date);
      return b.scraped_at - a.scraped_at;
    });
}
