/**
 * useConnectivity — device network + server health + the user's AI preference → effective engine.
 *
 *   no_network  → answer on-device (rules)
 *   no_server   → answer on-device (rules)
 *   server_ok   → server rules, or the LLM when the user wants it and it is up
 *
 * Getting worse is debounced (one slow health check must not flip the UI to "No server");
 * getting better applies immediately.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { healthApi } from '../lib/api';
import { usePrefs, type AiPreference } from '../store/prefs';

export type Connectivity = 'no_network' | 'no_server' | 'server_ok';
export type PillVariant = 'cloud' | 'rules_user' | 'rules_fallback' | 'no_server' | 'no_network';
export type FallbackReason = 'no_network' | 'no_server' | 'cloud_unavailable';

export const PILL: Record<PillVariant, { label: string; dot: string; hint: string }> = {
  cloud: { label: 'AI', dot: '#22c55e', hint: 'Answers use the AI model, facts from the law graph' },
  rules_user: { label: 'Rules only', dot: '#60a5fa', hint: 'You chose rules only — no AI calls' },
  rules_fallback: { label: 'Rules (auto)', dot: '#f97316', hint: 'AI model unavailable — using the rule engine' },
  no_server: { label: 'Offline', dot: '#ef4444', hint: "Can't reach the server — answering on this device" },
  no_network: { label: 'No internet', dot: '#ef4444', hint: 'No internet — answering on this device' },
};

export const FALLBACK_TOAST: Record<FallbackReason, string> = {
  no_network: 'No internet — answering on this device',
  no_server: "Can't reach the server — answering on this device",
  cloud_unavailable: 'AI model unavailable — using the rule engine',
};

export interface ConnectivityState {
  connectivity: Connectivity;
  cloudAvailable: boolean;
  sarvamAvailable: boolean;
  preference: AiPreference;
  effectiveMode: AiPreference;
  pill: PillVariant;
  serverReachable: boolean;
}

export function resolve(connectivity: Connectivity, cloudAvailable: boolean, preference: AiPreference, sarvamOk = false): ConnectivityState {
  let effectiveMode: AiPreference = 'rules';
  let pill: PillVariant;
  if (connectivity === 'no_network') pill = 'no_network';
  else if (connectivity === 'no_server') pill = 'no_server';
  else if (preference === 'rules') pill = 'rules_user';
  else if (cloudAvailable) { pill = 'cloud'; effectiveMode = 'cloud'; }
  else pill = 'rules_fallback';
  const serverReachable = connectivity === 'server_ok';
  return { connectivity, cloudAvailable, sarvamAvailable: serverReachable && sarvamOk, preference, effectiveMode, pill, serverReachable };
}

function fallbackReason(s: ConnectivityState): FallbackReason | null {
  if (s.preference !== 'cloud' || s.effectiveMode !== 'rules') return null;
  if (s.connectivity === 'no_network') return 'no_network';
  if (s.connectivity === 'no_server') return 'no_server';
  return s.cloudAvailable ? null : 'cloud_unavailable';
}

const POLL_MS = 10_000;
const DEGRADE_DEBOUNCE_MS = 4_000;

export function useConnectivity(onFallback?: (r: FallbackReason) => void) {
  const preference = usePrefs((s) => s.aiPreference);
  const [state, setState] = useState<ConnectivityState>(() => resolve('server_ok', true, preference));

  const onFallbackRef = useRef(onFallback);
  onFallbackRef.current = onFallback;
  const lastToast = useRef<string | null>(null);
  const degradeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<ConnectivityState | null>(null);
  const failStreak = useRef(0);

  const emit = useCallback((s: ConnectivityState) => {
    const r = fallbackReason(s);
    if (!r) { lastToast.current = null; return; }
    const key = `${r}:${s.preference}`;
    if (lastToast.current === key) return;
    lastToast.current = key;
    onFallbackRef.current?.(r);
  }, []);

  const apply = useCallback((next: ConnectivityState, immediate = false) => {
    setState((prev) => {
      const worse =
        (prev.connectivity === 'server_ok' && next.connectivity !== 'server_ok') ||
        (prev.effectiveMode === 'cloud' && next.effectiveMode === 'rules' && prev.preference === next.preference);
      if (worse && !immediate) {
        pending.current = next;
        if (!degradeTimer.current) {
          degradeTimer.current = setTimeout(() => {
            degradeTimer.current = null;
            if (pending.current) { setState(pending.current); emit(pending.current); pending.current = null; }
          }, DEGRADE_DEBOUNCE_MS);
        }
        return prev;
      }
      if (degradeTimer.current) { clearTimeout(degradeTimer.current); degradeTimer.current = null; pending.current = null; }
      if (prev.pill !== next.pill) emit(next);
      return next;
    });
  }, [emit]);

  const check = useCallback(async () => {
    if (!navigator.onLine) { apply(resolve('no_network', false, preference), true); return; }
    const h = await healthApi.check();
    if (h.status === 'ok' || h.ready || h.app === 'drivelegal') {
      failStreak.current = 0;
      apply(resolve('server_ok', !!(h.llm_ok ?? h.groq_ok), preference, !!h.sarvam_ok));
    } else if (++failStreak.current >= 2) {
      apply(resolve('no_server', false, preference));
    }
  }, [preference, apply]);

  useEffect(() => {
    check();
    const id = setInterval(check, POLL_MS);
    const offline = () => apply(resolve('no_network', false, preference), true);
    const online = () => { failStreak.current = 0; check(); };
    window.addEventListener('offline', offline);
    window.addEventListener('online', online);
    return () => {
      clearInterval(id);
      window.removeEventListener('offline', offline);
      window.removeEventListener('online', online);
      if (degradeTimer.current) clearTimeout(degradeTimer.current);
    };
  }, [check, apply, preference]);

  return { ...state, refresh: check, resetToasts: () => { lastToast.current = null; } };
}
