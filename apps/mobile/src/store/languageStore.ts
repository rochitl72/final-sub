/**
 * languageStore.ts — Global language preference (Zustand)
 * ─────────────────────────────────────────────────────────
 * Stores the user's chosen response language.
 * ChatScreen listens for changes and triggers progressive re-translation.
 *
 * Persisted to AsyncStorage so preference survives app restarts.
 */

import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Language, SUPPORTED_LANGUAGES } from '../services/sarvamApi';

const STORAGE_KEY = '@drivelegal/language';

interface LanguageState {
  language:      Language;
  setLanguage:   (lang: Language) => void;
  hydrate:       () => Promise<void>;
}

export const useLanguageStore = create<LanguageState>((set) => ({
  language: SUPPORTED_LANGUAGES[0],   // English default

  setLanguage: (lang) => {
    set({ language: lang });
    AsyncStorage.setItem(STORAGE_KEY, lang.code).catch(() => {});
  },

  hydrate: async () => {
    try {
      const saved = await AsyncStorage.getItem(STORAGE_KEY);
      if (saved) {
        const found = SUPPORTED_LANGUAGES.find(l => l.code === saved);
        if (found) set({ language: found });
      }
    } catch { /* ignore */ }
  },
}));
