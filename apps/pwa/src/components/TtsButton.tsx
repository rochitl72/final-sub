import { useState } from 'react';
import { Loader2, Square, Volume2, VolumeX } from 'lucide-react';
import { textToSpeech, translate } from '../lib/sarvam';
import { usePrefs } from '../store/prefs';
import { cx } from '../lib/format';

type TtsState = 'idle' | 'loading' | 'playing' | 'error';

// Only one reply speaks at a time across all bubbles.
let current: { stop: () => void } | null = null;

const hasSpeech = () => typeof window !== 'undefined' && 'speechSynthesis' in window;

function browserSpeak(text: string, lang: string, onEnd: () => void): boolean {
  if (!hasSpeech()) return false;
  const synth = window.speechSynthesis;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang === 'od-IN' ? 'or-IN' : lang;
  const voices = synth.getVoices();
  u.voice = voices.find((v) => v.lang === u.lang) ?? voices.find((v) => v.lang.startsWith(u.lang.slice(0, 2))) ?? null;
  u.rate = 0.95;
  u.onend = onEnd;
  u.onerror = onEnd;
  synth.cancel();
  synth.speak(u);
  return true;
}

const mostlyLatin = (t: string) => {
  const s = t.replace(/\s/g, '');
  return !s.length || (s.match(/[\u0000-\u024F]/g) || []).length / s.length > 0.85;
};

/** Read a reply aloud in the chosen language: Sarvam voice when available, browser voice otherwise. */
export function TtsButton({ text, sarvamAvailable, onError }: { text: string; sarvamAvailable: boolean; onError?: (m: string) => void }) {
  const [state, setState] = useState<TtsState>('idle');
  const lang = usePrefs((s) => s.language.code);

  if (!sarvamAvailable && !hasSpeech()) return null;

  const press = async () => {
    if (state === 'playing') { current?.stop(); return; }
    current?.stop();
    setState('loading');
    let speak = text.replace(/\*\*([^*]+)\*\*/g, '$1').slice(0, 800);
    if (lang !== 'en-IN' && mostlyLatin(speak) && sarvamAvailable) speak = (await translate(speak, lang)).slice(0, 800);

    let audio: HTMLAudioElement | null = null;
    const done = () => { setState('idle'); if (current?.stop === stop) current = null; };
    const stop = () => {
      try { audio?.pause(); } catch { /* ignore */ }
      try { if (hasSpeech()) window.speechSynthesis.cancel(); } catch { /* ignore */ }
      done();
    };

    const tts = sarvamAvailable ? await textToSpeech(speak, lang) : null;
    try {
      if (tts?.ok && tts.audioBase64) {
        audio = new Audio(`data:audio/wav;base64,${tts.audioBase64}`);
        audio.onended = done;
        current = { stop };
        await audio.play();
        setState('playing');
        return;
      }
      if (browserSpeak(speak, lang, done)) { current = { stop }; setState('playing'); return; }
      throw new Error(tts?.errorMessage || 'Voice unavailable');
    } catch (e: any) {
      setState('error');
      onError?.(e?.message || 'Voice playback failed');
      setTimeout(() => setState('idle'), 2500);
    }
  };

  return (
    <button
      type="button"
      onClick={press}
      aria-label={state === 'playing' ? 'Stop reading' : 'Read aloud'}
      title={state === 'playing' ? 'Stop' : 'Read aloud'}
      className={cx(
        'flex h-7 w-7 items-center justify-center rounded-full transition',
        state === 'playing' ? 'bg-brand/20 text-brand-light' : state === 'error' ? 'bg-red-500/15 text-red-400' : 'text-mist-soft hover:bg-white/5 hover:text-white',
      )}
    >
      {state === 'loading' ? <Loader2 size={14} className="animate-spin" /> : state === 'error' ? <VolumeX size={14} /> : state === 'playing' ? <Square size={12} fill="currentColor" /> : <Volume2 size={15} />}
    </button>
  );
}
