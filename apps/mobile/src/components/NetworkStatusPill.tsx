/**
 * NetworkStatusPill.tsx — Live AI connectivity indicator for the chat header
 * ──────────────────────────────────────────────────────────────────────────
 * Shows one of three states, updating in real-time:
 *
 *   🟢  "AI Online"      — Backend up + Groq reachable
 *   🟠  "Rules (auto)"   — Backend up + cloud AI unreachable (deprecated: use AiModePill)
 *   🔴  "No Connection"  — Backend unreachable (device offline)
 *
 * Polls /api/health every 10 seconds.
 * Listens to NetInfo for instant device-level changes.
 *
 * NOTE: Toast notifications on mode-switch are intentionally handled by the
 * parent (ChatScreen) via the `onModeChange` callback — this component only
 * renders the pill itself, avoiding absolute-position clipping inside flex rows.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Animated,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import NetInfo from '@react-native-community/netinfo';

import { Colors, Typography, Radius } from '../theme';
import { LinearGradient } from 'expo-linear-gradient';
import { healthApi } from '../services/api';

// ── Types ──────────────────────────────────────────────────────────────────

export type NetworkMode = 'online' | 'offline_mode' | 'disconnected';

/** Human-readable toast messages for each transition — parent should show these. */
export const NETWORK_MODE_MESSAGES: Record<NetworkMode, string> = {
  online:       '✅ AI Chat back online',
  offline_mode: '⚡ Switched to offline mode',
  disconnected: '📵 No internet — using offline data',
};

interface Props {
  /** Called whenever the mode changes — parent uses this to surface toasts. */
  onModeChange?: (mode: NetworkMode) => void;
}

// ── Config ─────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 10_000;
const ANIM_DURATION_MS = 300;

// ── Mode metadata ───────────────────────────────────────────────────────────

const MODE_META: Record<NetworkMode, { label: string; dot: string; bg: [string, string] }> = {
  online: {
    label: 'AI Online',
    dot:   Colors.success,
    bg:    ['#0a2a1a', '#0d3322'],
  },
  offline_mode: {
    label: 'Offline Mode',
    dot:   '#f97316',
    bg:    ['#2a1a08', '#3a2210'],
  },
  disconnected: {
    label: 'No Connection',
    dot:   Colors.error,
    bg:    ['#2a0a0a', '#3a1010'],
  },
};

// ── Component ───────────────────────────────────────────────────────────────

export function NetworkStatusPill({ onModeChange }: Props) {
  const [mode, setMode]  = useState<NetworkMode>('online');
  const prevModeRef      = useRef<NetworkMode>('online');
  const fadeAnim         = useRef(new Animated.Value(1)).current;
  const pulseAnim        = useRef(new Animated.Value(1)).current;
  const pollRef          = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef       = useRef(true);

  // Dot pulse animation (always running)
  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, { toValue: 1.7, duration: 900, useNativeDriver: true }),
        Animated.timing(pulseAnim, { toValue: 1.0, duration: 900, useNativeDriver: true }),
      ])
    ).start();
    return () => { mountedRef.current = false; };
  }, []);

  const _updateMode = useCallback((newMode: NetworkMode) => {
    if (!mountedRef.current) return;
    if (newMode === prevModeRef.current) return;
    prevModeRef.current = newMode;

    // Cross-fade pill on transition
    Animated.sequence([
      Animated.timing(fadeAnim, { toValue: 0.2, duration: ANIM_DURATION_MS / 2, useNativeDriver: true }),
      Animated.timing(fadeAnim, { toValue: 1.0, duration: ANIM_DURATION_MS / 2, useNativeDriver: true }),
    ]).start();

    setMode(newMode);
    // Notify parent — parent is responsible for toasts
    onModeChange?.(newMode);
  }, [onModeChange, fadeAnim]);

  const _checkStatus = useCallback(async () => {
    try {
      const net = await NetInfo.fetch();
      if (!net.isConnected) {
        _updateMode('disconnected');
        return;
      }
      const h = await healthApi.check();
      // ready is always true when backend is up; unreachable = caught below.
      // groq_ok distinguishes "AI online" vs "offline rule-based mode".
      if (h.groq_ok) {
        _updateMode('online');
      } else {
        _updateMode('offline_mode');
      }
    } catch {
      _updateMode('disconnected');
    }
  }, [_updateMode]);

  useEffect(() => {
    _checkStatus();
    pollRef.current = setInterval(_checkStatus, POLL_INTERVAL_MS);

    const unsub = NetInfo.addEventListener((state) => {
      if (!state.isConnected) {
        _updateMode('disconnected');
      } else {
        _checkStatus();
      }
    });

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      unsub();
    };
  }, [_checkStatus, _updateMode]);

  const meta = MODE_META[mode];

  return (
    <Animated.View style={[styles.pillWrap, { opacity: fadeAnim }]}>
      <LinearGradient
        colors={meta.bg}
        style={StyleSheet.absoluteFill}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
      />
      {/* Border glow */}
      <View style={[styles.pillBorder, { borderColor: meta.dot + '44' }]} />

      {/* Pulsing dot */}
      <View style={styles.dotWrap}>
        <Animated.View
          style={[
            styles.dotGlow,
            {
              backgroundColor: meta.dot,
              transform: [{ scale: pulseAnim }],
              opacity: mode === 'disconnected' ? 0.4 : 0.35,
            },
          ]}
        />
        <View style={[styles.dot, { backgroundColor: meta.dot }]} />
      </View>

      <Text style={[styles.label, { color: meta.dot }]}>
        {meta.label}
      </Text>
    </Animated.View>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────

const DOT_SIZE = 7;

const styles = StyleSheet.create({
  pillWrap: {
    flexDirection:     'row',
    alignItems:        'center',
    gap:               5,
    paddingVertical:   5,
    paddingHorizontal: 10,
    borderRadius:      Radius.full,
    overflow:          'hidden',
  },
  pillBorder: {
    ...StyleSheet.absoluteFill,
    borderRadius: Radius.full,
    borderWidth:  1,
  },
  dotWrap: {
    width:          DOT_SIZE,
    height:         DOT_SIZE,
    alignItems:     'center',
    justifyContent: 'center',
  },
  dotGlow: {
    position:     'absolute',
    width:        DOT_SIZE,
    height:       DOT_SIZE,
    borderRadius: DOT_SIZE / 2,
  },
  dot: {
    width:        DOT_SIZE,
    height:       DOT_SIZE,
    borderRadius: DOT_SIZE / 2,
  },
  label: {
    fontSize:      10,
    fontWeight:    '700' as const,
    letterSpacing: 0.3,
  },
});
