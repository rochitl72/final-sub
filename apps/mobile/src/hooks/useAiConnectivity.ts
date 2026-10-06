/**
 * Resolves device network + server health + user AI preference → effective mode.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import NetInfo from '@react-native-community/netinfo';

import { healthApi } from '../services/api';
import { AiPreference, useAiModeStore } from '../store/aiModeStore';

export type ConnectivityLevel = 'no_network' | 'no_server' | 'server_ok';

export type PillVariant = 'cloud' | 'rules_user' | 'rules_fallback' | 'no_server' | 'no_network';

export interface AiConnectivityState {
  connectivity: ConnectivityLevel;
  cloudAvailable: boolean;
  sarvamAvailable: boolean;   // Sarvam key is set and reachable (gates TTS)
  preference: AiPreference;
  effectiveMode: AiPreference;
  pillVariant: PillVariant;
  pillLabel: string;
  serverReachable: boolean;
  cloudFeaturesEnabled: boolean;
}

const POLL_MS = 10_000;

const PILL: Record<PillVariant, { label: string; dot: string; bg: [string, string] }> = {
  cloud: {
    label: 'LLM',
    dot:   '#22c55e',
    bg:    ['#0a2a1a', '#0d3322'],
  },
  rules_user: {
    label: 'Rules only',
    dot:   '#60a5fa',
    bg:    ['#0a1a2a', '#0d2233'],
  },
  rules_fallback: {
    label: 'Rules (auto)',
    dot:   '#f97316',
    bg:    ['#2a1a08', '#3a2210'],
  },
  no_server: {
    label: 'No server',
    dot:   '#ef4444',
    bg:    ['#2a0a0a', '#3a1010'],
  },
  no_network: {
    label: 'No internet',
    dot:   '#ef4444',
    bg:    ['#2a0a0a', '#3a1010'],
  },
};

export function resolveAiConnectivity(
  connectivity: ConnectivityLevel,
  cloudAvailable: boolean,
  preference: AiPreference,
  sarvamOk: boolean = false,
): AiConnectivityState {
  const serverReachable = connectivity === 'server_ok';

  let effectiveMode: AiPreference = preference;
  let pillVariant: PillVariant = 'cloud';

  if (connectivity === 'no_network') {
    pillVariant = 'no_network';
    effectiveMode = 'rules';
  } else if (connectivity === 'no_server') {
    pillVariant = 'no_server';
    effectiveMode = 'rules';
  } else if (preference === 'rules') {
    pillVariant = 'rules_user';
    effectiveMode = 'rules';
  } else if (cloudAvailable) {
    pillVariant = 'cloud';
    effectiveMode = 'cloud';
  } else {
    pillVariant = 'rules_fallback';
    effectiveMode = 'rules';
  }

  const cloudFeaturesEnabled =
    serverReachable && cloudAvailable && effectiveMode === 'cloud';

  // sarvamAvailable is true only when the backend has a valid Sarvam API key
  // and the server is reachable — this specifically gates TTS/translate features.
  const sarvamAvailable = serverReachable && sarvamOk;

  return {
    connectivity,
    cloudAvailable,
    sarvamAvailable,
    preference,
    effectiveMode,
    pillVariant,
    pillLabel: PILL[pillVariant].label,
    serverReachable,
    cloudFeaturesEnabled,
  };
}

export function getPillMeta(variant: PillVariant) {
  return PILL[variant];
}

export type FallbackReason = 'no_network' | 'no_server' | 'cloud_unavailable';

export const FALLBACK_TOAST: Record<FallbackReason, string> = {
  no_network:        '📵 No internet — using rule-based replies',
  no_server:         '⚠️ Can\'t reach server — check API URL / start backend',
  cloud_unavailable: '⚡ LLM unavailable — using rule-based replies',
};

function fallbackReason(
  connectivity: ConnectivityLevel,
  cloudAvailable: boolean,
  preference: AiPreference,
  effectiveMode: AiPreference,
): FallbackReason | null {
  if (preference !== 'cloud' || effectiveMode !== 'rules') return null;
  if (connectivity === 'no_network') return 'no_network';
  if (connectivity === 'no_server') return 'no_server';
  if (!cloudAvailable) return 'cloud_unavailable';
  return null;
}

// How long (ms) a degraded check result must persist before we switch the UI.
// Prevents single slow health checks from flipping the pill to "No server".
const DEGRADE_DEBOUNCE_MS = 4000;

export function useAiConnectivity(onFallback?: (reason: FallbackReason) => void) {
  const preference = useAiModeStore((s) => s.preference);

  // Start optimistic (server up, checking cloud) — avoids "No server" flash on mount
  const [state, setState] = useState<AiConnectivityState>(() =>
    resolveAiConnectivity('server_ok', true, 'cloud'),
  );

  const lastFallbackKey  = useRef<string | null>(null);
  // Pending degraded state waiting to be committed after debounce
  const degradePending   = useRef<AiConnectivityState | null>(null);
  const degradeTimer     = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Track consecutive failed checks before declaring no_server
  const failStreak       = useRef(0);

  // Keep the latest callback in a ref so callers can pass an inline arrow
  // without re-creating emitFallback → runCheck → the polling effect on every
  // render (that loop fired thousands of /api/health calls per minute on web).
  const onFallbackRef = useRef(onFallback);
  onFallbackRef.current = onFallback;

  const emitFallback = useCallback(
    (next: AiConnectivityState) => {
      const onFallback = onFallbackRef.current;
      if (!onFallback) return;
      const reason = fallbackReason(
        next.connectivity,
        next.cloudAvailable,
        next.preference,
        next.effectiveMode,
      );
      if (!reason) {
        lastFallbackKey.current = null;
        return;
      }
      const key = `${reason}:${next.preference}`;
      if (lastFallbackKey.current === key) return;
      lastFallbackKey.current = key;
      onFallback(reason);
    },
    [],
  );

  // Apply a state change with smart debouncing:
  //  • Upgrades (offline → online, rules → cloud) apply IMMEDIATELY
  //  • Degradations (online → offline, cloud → rules) wait DEGRADE_DEBOUNCE_MS
  //    before committing — a second successful check cancels the debounce
  const applyNext = useCallback((next: AiConnectivityState) => {
    setState((prev) => {
      const isUpgrade =
        (prev.connectivity !== 'server_ok' && next.connectivity === 'server_ok') ||
        (prev.effectiveMode === 'rules' && next.effectiveMode === 'cloud');

      const isDegrade =
        (prev.connectivity === 'server_ok' && next.connectivity !== 'server_ok') ||
        (prev.effectiveMode === 'cloud'    && next.effectiveMode === 'rules');

      if (isUpgrade) {
        // Cancel any pending degradation — we're back online
        if (degradeTimer.current) {
          clearTimeout(degradeTimer.current);
          degradeTimer.current  = null;
          degradePending.current = null;
        }
        failStreak.current = 0;
        emitFallback(next);
        return next;
      }

      if (isDegrade) {
        // Store the degraded state but don't apply it yet
        degradePending.current = next;
        if (!degradeTimer.current) {
          degradeTimer.current = setTimeout(() => {
            if (degradePending.current) {
              setState(degradePending.current);
              emitFallback(degradePending.current);
            }
            degradeTimer.current   = null;
            degradePending.current = null;
          }, DEGRADE_DEBOUNCE_MS);
        }
        // Return prev — hold the current good state during the debounce window
        return prev;
      }

      // Same level or no change — apply immediately without toast
      if (prev.pillVariant !== next.pillVariant) emitFallback(next);
      return next;
    });
  }, [emitFallback]);

  const runCheck = useCallback(async () => {
    let connectivity: ConnectivityLevel = 'no_server';
    let cloudAvailable = false;
    let sarvamOk = false;

    try {
      const net = await NetInfo.fetch();
      if (!net.isConnected) {
        connectivity = 'no_network';
        failStreak.current++;
      } else {
        const h = await healthApi.check();
        // Backend is "up" if it returned any valid status
        if (h.status === 'ok' || h.ready || h.app === 'drivelegal') {
          connectivity   = 'server_ok';
          cloudAvailable = !!(h.groq_ok || h.sarvam_ok);
          sarvamOk       = !!h.sarvam_ok;
          failStreak.current = 0;   // reset streak on success
        } else {
          failStreak.current++;
        }
      }
    } catch {
      failStreak.current++;
      // Only treat as no_server after 2 consecutive failures
      // to avoid reacting to a single slow/dropped request
      if (failStreak.current < 2) return;
      connectivity = 'no_server';
    }

    applyNext(resolveAiConnectivity(connectivity, cloudAvailable, preference, sarvamOk));
  }, [preference, applyNext]);

  useEffect(() => {
    runCheck();
    const id = setInterval(runCheck, POLL_MS);

    const unsub = NetInfo.addEventListener((s) => {
      if (!s.isConnected) {
        // Device-level disconnect is immediate and reliable — apply without debounce
        failStreak.current = 5;
        const next = resolveAiConnectivity('no_network', false, preference);
        if (degradeTimer.current) {
          clearTimeout(degradeTimer.current);
          degradeTimer.current  = null;
          degradePending.current = null;
        }
        setState(next);
        emitFallback(next);
      } else {
        // Reconnected — run a health check to re-establish server status
        failStreak.current = 0;
        runCheck();
      }
    });

    return () => {
      clearInterval(id);
      unsub();
      if (degradeTimer.current) clearTimeout(degradeTimer.current);
    };
  }, [runCheck, preference, emitFallback]);

  return {
    ...state,
    refresh: runCheck,
    resetFallbackToasts: () => { lastFallbackKey.current = null; },
  };
}
