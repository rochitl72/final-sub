/**
 * Auth store — device-ID auth.
 * A stable UUID lives in localStorage → POST /auth/device → JWT. No sign-up, no password.
 */
import { create } from 'zustand';
import { authApi, setAuthToken, type User } from '../lib/api';
import { storage } from '../lib/storage';

const TOKEN_KEY = 'drivelegal_jwt';
const USER_KEY = 'drivelegal_user';
const DEVICE_KEY = 'drivelegal_device_id';

function deviceId(): string {
  let id = storage.get(DEVICE_KEY);
  if (!id) {
    id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
          const r = (Math.random() * 16) | 0;
          return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
        });
    storage.set(DEVICE_KEY, id);
  }
  return id;
}

interface AuthState {
  token: string | null;
  user: User | null;
  isReady: boolean;
  boot: () => Promise<void>;
  refresh: () => Promise<void>;
}

function save(token: string, user: User) {
  setAuthToken(token);
  storage.set(TOKEN_KEY, token);
  storage.setJSON(USER_KEY, user);
}

async function register(): Promise<{ token: string; user: User } | null> {
  try {
    const resp = await authApi.registerDevice(deviceId());
    save(resp.access_token, resp.user);
    return { token: resp.access_token, user: resp.user };
  } catch {
    return null; // offline — the app still works on-device
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  isReady: false,

  boot: async () => {
    const token = storage.get(TOKEN_KEY);
    const user = storage.getJSON<User>(USER_KEY);
    if (token && user) {
      setAuthToken(token);
      set({ token, user });
      try {
        const fresh = await authApi.getMe();
        save(token, fresh);
        set({ user: fresh, isReady: true });
        return;
      } catch (err: any) {
        // Offline: keep the cached token. Expired token: register again.
        if (!err?.response) { set({ isReady: true }); return; }
      }
    }
    const r = await register();
    set({ token: r?.token ?? null, user: r?.user ?? null, isReady: true });
  },

  /** Fresh token for the same device (same chats). */
  refresh: async () => {
    storage.remove(TOKEN_KEY);
    setAuthToken(null);
    const r = await register();
    if (r) set({ token: r.token, user: r.user });
  },
}));
