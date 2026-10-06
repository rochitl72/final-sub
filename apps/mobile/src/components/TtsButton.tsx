/**
 * TtsButton.tsx — Voice output button for chat bubbles
 * ──────────────────────────────────────────────────────
 * Tap to hear a message read aloud in the user's selected language.
 * States: idle (speaker) → loading (spinner) → playing (pause/stop)
 *
 * Uses expo-audio for playback and expo-file-system to write the WAV temp file.
 * Online-only: invisible when isOnline=false.
 */

import React, { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  StyleSheet,
  TouchableOpacity,
} from 'react-native';
import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
// SDK 54+: cacheDirectory / writeAsStringAsync live under /legacy only
import * as FileSystem from 'expo-file-system/legacy';
import { Ionicons } from '@expo/vector-icons';

import { Colors } from '../theme';
import { textToSpeech, translate } from '../services/sarvamApi';
import { useLanguageStore } from '../store/languageStore';

type TtsState = 'idle' | 'loading' | 'playing' | 'error';

interface Props {
  text:     string;
  isOnline: boolean;
  onError?: (msg: string) => void;
}

// Global sound ref so only one TTS plays at a time across all bubbles
let _currentSound: AudioPlayer | null = null;
let _currentStop:  (() => void) | null = null;

// ── Web playback helpers ────────────────────────────────────────────────────
// expo-file-system has no cache dir in the browser, so on web we play Sarvam's
// WAV from a data: URL, and fall back to the browser's own speech engine when
// Sarvam is unavailable (no key / no credits).
const IS_WEB = Platform.OS === 'web';
let _webAudio: any = null;

function webHasSpeech(): boolean {
  return IS_WEB && typeof window !== 'undefined' && 'speechSynthesis' in window;
}

function webStop() {
  try { _webAudio?.pause(); } catch { /* ignore */ }
  _webAudio = null;
  try { if (webHasSpeech()) window.speechSynthesis.cancel(); } catch { /* ignore */ }
}

function webSpeak(text: string, lang: string, onEnd: () => void): boolean {
  if (!webHasSpeech()) return false;
  const synth = window.speechSynthesis;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang === 'od-IN' ? 'or-IN' : lang;
  const base = u.lang.slice(0, 2);
  const voice = synth.getVoices().find(v => v.lang === u.lang)
             ?? synth.getVoices().find(v => v.lang.startsWith(base));
  if (voice) u.voice = voice;
  u.rate = 0.95;
  u.onend = onEnd;
  u.onerror = onEnd;
  synth.cancel();
  synth.speak(u);
  return true;
}

/** True when text is mostly Latin — needs translate before non-English TTS. */
function isMostlyLatin(text: string): boolean {
  const letters = text.replace(/\s/g, '');
  if (!letters.length) return true;
  const latin = (letters.match(/[\u0000-\u024F]/g) || []).length;
  return latin / letters.length > 0.85;
}

export function TtsButton({ text, isOnline, onError }: Props) {
  const [ttsState, setTtsState] = useState<TtsState>('idle');
  const soundRef   = useRef<AudioPlayer | null>(null);
  const errorTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { language } = useLanguageStore();

  const stopCurrent = useCallback(async () => {
    if (IS_WEB) webStop();
    try {
      if (_currentSound) {
        _currentSound.pause();
        _currentSound.remove();
        _currentSound = null;
      }
    } catch { /* ignore */ }
    _currentStop?.();
    _currentStop = null;
  }, []);

  const handlePress = useCallback(async () => {
    if (!isOnline && !webHasSpeech()) return;

    // If this button is playing, stop it
    if (ttsState === 'playing') {
      await stopCurrent();
      setTtsState('idle');
      return;
    }

    // Stop any other playing TTS
    await stopCurrent();

    setTtsState('loading');
    try {
      // Strip markdown bold markers for cleaner TTS
      let speakText = text.replace(/\*\*([^*]+)\*\*/g, '$1').slice(0, 800);
      const speakLang = language.code;

      // Bubble may still show English if translation is in flight — translate on demand
      if (speakLang !== 'en-IN' && isMostlyLatin(speakText)) {
        const translated = await translate(speakText, speakLang);
        if (translated.trim()) speakText = translated.slice(0, 800);
      }

      const tts = isOnline ? await textToSpeech(speakText, speakLang)
                           : { ok: false, audioBase64: null as string | null, errorMessage: undefined };

      if (IS_WEB) {
        const done = () => {
          setTtsState('idle');
          if (_currentStop === done) _currentStop = null;
        };
        if (tts.ok && tts.audioBase64) {
          const audio = new (window as any).Audio(`data:audio/wav;base64,${tts.audioBase64}`);
          _webAudio = audio;
          audio.onended = done;
          _currentStop = done;
          await audio.play();
          setTtsState('playing');
          return;
        }
        // Sarvam unavailable → browser voice
        if (webSpeak(speakText, speakLang, done)) {
          _currentStop = done;
          setTtsState('playing');
          return;
        }
      }

      if (!tts.ok || !tts.audioBase64) {
        setTtsState('error');
        onError?.(tts.errorMessage ?? '🔇 Voice unavailable — check server Sarvam setup');
        if (errorTimer.current) clearTimeout(errorTimer.current);
        errorTimer.current = setTimeout(() => setTtsState('idle'), 2500);
        return;
      }

      const cacheDir = FileSystem.cacheDirectory;
      if (!cacheDir) {
        throw new Error('No cache directory on this device');
      }

      const tmpUri = `${cacheDir}tts_${Date.now()}.wav`;
      await FileSystem.writeAsStringAsync(tmpUri, tts.audioBase64, {
        encoding: FileSystem.EncodingType.Base64,
      });

      // Load and play
      await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false });
      const player = createAudioPlayer({ uri: tmpUri });
      soundRef.current  = player;
      _currentSound     = player;
      _currentStop      = () => setTtsState('idle');

      player.addListener('playbackStatusUpdate', (status) => {
        if (status.didJustFinish) {
          setTtsState('idle');
          _currentSound = null;
          _currentStop  = null;
          try { player.remove(); } catch { /* ignore */ }
          FileSystem.deleteAsync(tmpUri, { idempotent: true }).catch(() => {});
        }
      });
      player.play();
      setTtsState('playing');
    } catch (err) {
      console.warn('[TtsButton] playback failed', err);
      setTtsState('error');
      onError?.('🔇 Voice playback failed — please try again');
      if (errorTimer.current) clearTimeout(errorTimer.current);
      errorTimer.current = setTimeout(() => setTtsState('idle'), 2500);
    }
  }, [isOnline, ttsState, text, language.code, stopCurrent, onError]);

  if (!isOnline && !webHasSpeech()) return null;

  return (
    <TouchableOpacity
      onPress={handlePress}
      style={[
        styles.btn,
        ttsState === 'playing' && styles.btnActive,
        ttsState === 'error'   && styles.btnError,
      ]}
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      activeOpacity={0.7}
    >
      {ttsState === 'loading' ? (
        <ActivityIndicator size={10} color={Colors.blueVibrant} />
      ) : ttsState === 'error' ? (
        <Ionicons name="volume-mute" size={14} color="#ef4444" />
      ) : (
        <Ionicons
          name={ttsState === 'playing' ? 'stop-circle' : 'volume-medium-outline'}
          size={14}
          color={ttsState === 'playing' ? Colors.blueVibrant : Colors.gray}
        />
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  btn: {
    width:          24,
    height:         24,
    alignItems:     'center',
    justifyContent: 'center',
    borderRadius:   12,
  },
  btnActive: {
    backgroundColor: 'rgba(37,99,235,0.15)',
  },
  btnError: {
    backgroundColor: 'rgba(239,68,68,0.12)',
  },
});
