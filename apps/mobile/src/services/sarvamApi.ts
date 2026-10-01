/**
 * sarvamApi.ts — Mobile client for Sarvam AI features
 * ─────────────────────────────────────────────────────
 * All calls go through our FastAPI backend (/api/sarvam/*) so the
 * Sarvam API key stays server-side.
 *
 * Features:
 *   translate(text, targetLang)  → translated string (Mayura v1)
 *   textToSpeech(text, lang)     → { ok, audioBase64 } (Bulbul v3)
 *
 * Voice INPUT was removed — input is text-only.
 * Voice OUTPUT (TTS) remains active via TtsButton on every bot reply.
 */

import axios from 'axios';
import { BASE_URL } from './api';

// ── Language definitions (Mayura/Bulbul supported languages) ──────────────────

export interface Language {
  code:   string;   // BCP-47 e.g. "hi-IN"
  name:   string;   // Native script name
  label:  string;   // Short display name
  script: string;   // Representative character shown in selector
  color:  string;   // Accent color for UI
}

export const SUPPORTED_LANGUAGES: Language[] = [
  { code: 'en-IN', name: 'English',    label: 'English',   script: 'A',   color: '#3b82f6' },
  { code: 'hi-IN', name: 'हिंदी',       label: 'Hindi',     script: 'हि',  color: '#f97316' },
  { code: 'ta-IN', name: 'தமிழ்',       label: 'Tamil',     script: 'த',   color: '#10b981' },
  { code: 'te-IN', name: 'తెలుగు',      label: 'Telugu',    script: 'తె',  color: '#8b5cf6' },
  { code: 'kn-IN', name: 'ಕನ್ನಡ',       label: 'Kannada',   script: 'ಕ',   color: '#ec4899' },
  { code: 'ml-IN', name: 'മലയാളം',     label: 'Malayalam', script: 'മ',   color: '#06b6d4' },
  { code: 'bn-IN', name: 'বাংলা',       label: 'Bengali',   script: 'বাং', color: '#f59e0b' },
  { code: 'mr-IN', name: 'मराठी',       label: 'Marathi',   script: 'म',   color: '#84cc16' },
  { code: 'gu-IN', name: 'ગુજરાતી',    label: 'Gujarati',  script: 'ગ',   color: '#a78bfa' },
  { code: 'pa-IN', name: 'ਪੰਜਾਬੀ',     label: 'Punjabi',   script: 'ਪੰ',  color: '#fb7185' },
  { code: 'od-IN', name: 'ଓଡ଼ିଆ',       label: 'Odia',      script: 'ଓ',   color: '#34d399' },
];

export function getLanguage(code: string): Language {
  return SUPPORTED_LANGUAGES.find(l => l.code === code) ?? SUPPORTED_LANGUAGES[0];
}

// ── Axios client (uses same base URL as main API) ─────────────────────────────

const client = axios.create({ baseURL: BASE_URL, timeout: 25000 });

// ── 1. Translate ──────────────────────────────────────────────────────────────

/**
 * Translate English text to target Indian language.
 * Returns { text: translated, ok: true } on success,
 * or { text: original, ok: false, error: reason } on failure.
 * Callers can use the `ok` flag to surface a toast without crashing.
 */
export async function translate(
  text: string,
  targetLanguage: string,
  sourceLanguage: string = 'en-IN',
): Promise<string> {
  if (!text.trim() || targetLanguage === 'en-IN') return text;
  try {
    const { data } = await client.post('/api/sarvam/translate', {
      text,
      target_language: targetLanguage,
      source_language: sourceLanguage,
    });
    return data.translated ?? text;
  } catch (err: any) {
    // Log for debugging — backend 503 = API key not set, 401 = wrong key
    const status = err?.response?.status;
    if (status === 503) {
      console.warn('[sarvamApi] translate: server returned 503 — SARVAM_API_KEY likely missing');
    } else if (status === 401) {
      console.warn('[sarvamApi] translate: invalid API key');
    }
    return text;   // Always degrade gracefully — show original
  }
}

// ── 2. Text-to-Speech ─────────────────────────────────────────────────────────

export interface TtsResult {
  ok: boolean;
  audioBase64: string | null;
  errorMessage?: string;
}

/**
 * Synthesize speech from text via backend Sarvam Bulbul v3.
 */
export async function textToSpeech(
  text: string,
  languageCode: string = 'en-IN',
): Promise<TtsResult> {
  try {
    const { data } = await client.post(
      '/api/sarvam/tts',
      {
        text: text.slice(0, 1000),
        language_code: languageCode,
        pace: 0.9,
      },
      { timeout: 45000 },
    );
    const audioBase64 = data.audio_base64 ?? null;
    if (!audioBase64) {
      return { ok: false, audioBase64: null, errorMessage: '🔇 Server returned no audio' };
    }
    return { ok: true, audioBase64 };
  } catch (err: any) {
    const status = err?.response?.status;
    const detail = err?.response?.data?.detail;
    if (status === 503) {
      console.warn('[sarvamApi] TTS: server 503 —', detail ?? 'SARVAM_API_KEY missing?');
      return {
        ok: false,
        audioBase64: null,
        errorMessage: '🔇 Voice unavailable — add SARVAM_API_KEY to the repo-root .env and restart',
      };
    }
    if (status === 401) {
      return { ok: false, audioBase64: null, errorMessage: '🔇 Invalid Sarvam API key on server' };
    }
    if (status === 400) {
      return {
        ok: false,
        audioBase64: null,
        errorMessage: `🔇 Voice failed for ${languageCode} — try again after reload`,
      };
    }
    console.warn('[sarvamApi] TTS failed', status, detail ?? err?.message);
    return {
      ok: false,
      audioBase64: null,
      errorMessage: '🔇 Voice request failed — check phone is on same Wi‑Fi as your Mac',
    };
  }
}

