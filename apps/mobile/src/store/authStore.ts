/**
 * Auth Store (Zustand)
 * Device-ID auth: stable UUID in SecureStore → POST /auth/device → JWT.
 */

import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import { authApi, setAuthToken, User } from '../services/api';

const TOKEN_KEY    = 'drivelegal_jwt';
const USER_KEY     = 'drivelegal_user';
const DEVICE_KEY   = 'drivelegal_device_id';

function generateDeviceId(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

async function getOrCreateDeviceId(): Promise<string> {
  let id = await SecureStore.getItemAsync(DEVICE_KEY);
  if (!id) {
    id = generateDeviceId();
    await SecureStore.setItemAsync(DEVICE_KEY, id);
  }
  return id;
}

interface AuthState {
  token:     string | null;
  user:      User | null;
  isLoading: boolean;
  isReady:   boolean;

  setAuth:         (token: string, user: User) => Promise<void>;
  clearAuth:       () => Promise<void>;
  ensureDeviceAuth: () => Promise<void>;
  loadFromStorage: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token:     null,
  user:      null,
  isLoading: false,
  isReady:   false,

  setAuth: async (token: string, user: User) => {
    setAuthToken(token);
    await SecureStore.setItemAsync(TOKEN_KEY, token);
    await SecureStore.setItemAsync(USER_KEY, JSON.stringify(user));
    set({ token, user });
  },

  /** Clears JWT only — keeps device_id so the same guest account is restored. */
  clearAuth: async () => {
    setAuthToken(null);
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    await SecureStore.deleteItemAsync(USER_KEY);
    set({ token: null, user: null });
  },

  ensureDeviceAuth: async () => {
    const deviceId = await getOrCreateDeviceId();
    try {
      const resp = await authApi.registerDevice(deviceId);
      await get().setAuth(resp.access_token, resp.user);
      return;
    } catch {
      // Offline — keep existing token if we have one
      const { token, user } = get();
      if (token && user) {
        setAuthToken(token);
      }
    }
  },

  loadFromStorage: async () => {
    set({ isLoading: true });
    try {
      const token = await SecureStore.getItemAsync(TOKEN_KEY);
      const userStr = await SecureStore.getItemAsync(USER_KEY);

      if (token && userStr) {
        const user = JSON.parse(userStr) as User;
        setAuthToken(token);
        set({ token, user });

        try {
          const fresh = await authApi.getMe();
          await get().setAuth(token, fresh);
          set({ isLoading: false, isReady: true });
          return;
        } catch {
          // Token expired or backend unreachable — drop stale JWT, re-register device
          setAuthToken(null);
          await SecureStore.deleteItemAsync(TOKEN_KEY);
          await SecureStore.deleteItemAsync(USER_KEY);
          set({ token: null, user: null });
        }
      }

      await get().ensureDeviceAuth();
    } catch {
      await get().ensureDeviceAuth();
    }
    set({ isLoading: false, isReady: true });
  },
}));

export async function resetDeviceIdentity(): Promise<void> {
  await SecureStore.deleteItemAsync(DEVICE_KEY);
  await SecureStore.deleteItemAsync(TOKEN_KEY);
  await SecureStore.deleteItemAsync(USER_KEY);
  setAuthToken(null);
  useAuthStore.setState({ token: null, user: null });
}
