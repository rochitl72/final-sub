/**
 * sarvam.ts — translation + text-to-speech.
 * Every call goes through the backend (/api/sarvam/*), so the Sarvam key stays server-side.
 */
import axios from 'axios';
import { BASE_URL } from './api';

export interface Language {
  code: string;   // BCP-47, e.g. "hi-IN"
  name: string;   // native script name
  label: string;  // English name
  script: string; // representative glyph
  color: string;  // accent
}

export const SUPPORTED_LANGUAGES: Language[] = [
  { code: 'en-IN', name: 'English', label: 'English', script: 'A', color: '#3b82f6' },
  { code: 'hi-IN', name: 'हिंदी', label: 'Hindi', script: 'हि', color: '#f97316' },
  { code: 'ta-IN', name: 'தமிழ்', label: 'Tamil', script: 'த', color: '#10b981' },
  { code: 'te-IN', name: 'తెలుగు', label: 'Telugu', script: 'తె', color: '#8b5cf6' },
  { code: 'kn-IN', name: 'ಕನ್ನಡ', label: 'Kannada', script: 'ಕ', color: '#ec4899' },
  { code: 'ml-IN', name: 'മലയാളം', label: 'Malayalam', script: 'മ', color: '#06b6d4' },
  { code: 'bn-IN', name: 'বাংলা', label: 'Bengali', script: 'বাং', color: '#f59e0b' },
  { code: 'mr-IN', name: 'मराठी', label: 'Marathi', script: 'म', color: '#84cc16' },
  { code: 'gu-IN', name: 'ગુજરાતી', label: 'Gujarati', script: 'ગ', color: '#a78bfa' },
  { code: 'pa-IN', name: 'ਪੰਜਾਬੀ', label: 'Punjabi', script: 'ਪੰ', color: '#fb7185' },
  { code: 'od-IN', name: 'ଓଡ଼ିଆ', label: 'Odia', script: 'ଓ', color: '#34d399' },
];

const client = axios.create({ baseURL: BASE_URL, timeout: 25000 });

/** Translate English → target language. Returns the original text on any failure. */
export async function translate(text: string, target: string, source = 'en-IN'): Promise<string> {
  if (!text.trim() || target === 'en-IN') return text;
  try {
    const { data } = await client.post('/api/sarvam/translate', {
      text, target_language: target, source_language: source,
    });
    return data.translated ?? text;
  } catch {
    return text;
  }
}

export interface TtsResult { ok: boolean; audioBase64: string | null; errorMessage?: string }

/** Speech via Sarvam Bulbul (server-side). */
export async function textToSpeech(text: string, languageCode = 'en-IN'): Promise<TtsResult> {
  try {
    const { data } = await client.post(
      '/api/sarvam/tts',
      { text: text.slice(0, 1000), language_code: languageCode, pace: 0.9 },
      { timeout: 45000 },
    );
    const audioBase64 = data.audio_base64 ?? null;
    return audioBase64 ? { ok: true, audioBase64 } : { ok: false, audioBase64: null, errorMessage: 'Server returned no audio' };
  } catch (err: any) {
    const status = err?.response?.status;
    if (status === 503) return { ok: false, audioBase64: null, errorMessage: 'Voice unavailable — SARVAM_API_KEY missing on the server' };
    if (status === 401) return { ok: false, audioBase64: null, errorMessage: 'Invalid Sarvam API key on the server' };
    return { ok: false, audioBase64: null, errorMessage: 'Voice request failed' };
  }
}
