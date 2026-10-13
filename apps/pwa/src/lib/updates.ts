/**
 * updates.ts — law-dataset freshness banner logic.
 * The banner appears when the backend's .gov.in update cycle hasn't run for ≥ 7 days,
 * unless the user asked to be reminded later.
 */
import { updateApi } from './api';
import { storage } from './storage';

const KEY_REMIND_AFTER = '@drivelegal/update/remindAfterMs';
const KEY_LAST_RUN = '@drivelegal/update/lastRunMs';
const DAY = 86_400_000;

export async function shouldShowBanner(): Promise<boolean> {
  const remindAfter = Number(storage.get(KEY_REMIND_AFTER) || 0);
  if (remindAfter && Date.now() < remindAfter) return false;
  const status = await updateApi.status();
  if (!status) return false;
  return status.days_since_run === null || status.days_since_run >= 7;
}

export const getUpdateStatus = () => updateApi.status();

export async function runUpdate() {
  try {
    const res = await updateApi.run();
    if (res.ok) storage.set(KEY_LAST_RUN, String(Date.now()));
    return res;
  } catch {
    return null;
  }
}

export function remindLater(days: number) {
  storage.set(KEY_REMIND_AFTER, String(Date.now() + days * DAY));
}
