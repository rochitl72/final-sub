/**
 * storage.ts — tiny localStorage wrapper.
 * Every access is guarded: private windows / blocked site data make localStorage throw,
 * and the app must still work (it just won't remember things between visits).
 */
function ls(): Storage | undefined {
  try { return window.localStorage; } catch { return undefined; }
}

export const storage = {
  get(key: string): string | null {
    try { return ls()?.getItem(key) ?? null; } catch { return null; }
  },
  set(key: string, value: string): void {
    try { ls()?.setItem(key, value); } catch { /* quota / blocked — non-fatal */ }
  },
  remove(key: string): void {
    try { ls()?.removeItem(key); } catch { /* ignore */ }
  },
  getJSON<T>(key: string): T | null {
    const raw = storage.get(key);
    if (!raw) return null;
    try { return JSON.parse(raw) as T; } catch { return null; }
  },
  setJSON(key: string, value: unknown): void {
    storage.set(key, JSON.stringify(value));
  },
};
