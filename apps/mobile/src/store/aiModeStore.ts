/**
 * User preference: Cloud AI (Groq/Sarvam path on server) vs Rules only.
 */

import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';

export type AiPreference = 'cloud' | 'rules';

const PREF_KEY = 'drivelegal_ai_preference';

interface AiModeState {
  preference: AiPreference;
  isReady: boolean;
  setPreference: (p: AiPreference) => Promise<void>;
  togglePreference: () => Promise<void>;
  hydrate: () => Promise<void>;
}

export const useAiModeStore = create<AiModeState>((set, get) => ({
  preference: 'cloud',
  isReady:    false,

  setPreference: async (p: AiPreference) => {
    await AsyncStorage.setItem(PREF_KEY, p);
    set({ preference: p });
  },

  togglePreference: async () => {
    const next = get().preference === 'cloud' ? 'rules' : 'cloud';
    await get().setPreference(next);
  },

  hydrate: async () => {
    try {
      const raw = await AsyncStorage.getItem(PREF_KEY);
      if (raw === 'cloud' || raw === 'rules') {
        set({ preference: raw, isReady: true });
        return;
      }
    } catch { /* ignore */ }
    set({ isReady: true });
  },
}));
