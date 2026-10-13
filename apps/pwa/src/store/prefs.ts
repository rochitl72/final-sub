/**
 * User preferences that survive reloads: AI engine (LLM vs rules only) and reply language.
 */
import { create } from 'zustand';
import { storage } from '../lib/storage';
import { SUPPORTED_LANGUAGES, type Language } from '../lib/sarvam';

export type AiPreference = 'cloud' | 'rules';

const AI_KEY = 'drivelegal_ai_preference';
const LANG_KEY = '@drivelegal/language';

interface PrefsState {
  aiPreference: AiPreference;
  language: Language;
  setAiPreference: (p: AiPreference) => void;
  toggleAiPreference: () => AiPreference;
  setLanguage: (l: Language) => void;
}

const savedAi = storage.get(AI_KEY);
const savedLang = SUPPORTED_LANGUAGES.find((l) => l.code === storage.get(LANG_KEY));

export const usePrefs = create<PrefsState>((set, get) => ({
  aiPreference: savedAi === 'rules' ? 'rules' : 'cloud',
  language: savedLang ?? SUPPORTED_LANGUAGES[0],

  setAiPreference: (p) => {
    storage.set(AI_KEY, p);
    set({ aiPreference: p });
  },
  toggleAiPreference: () => {
    const next: AiPreference = get().aiPreference === 'cloud' ? 'rules' : 'cloud';
    get().setAiPreference(next);
    return next;
  },
  setLanguage: (l) => {
    storage.set(LANG_KEY, l.code);
    set({ language: l });
  },
}));
