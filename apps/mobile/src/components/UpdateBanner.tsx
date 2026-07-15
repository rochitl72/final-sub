/**
 * UpdateBanner.tsx — Data freshness consent UI
 * ─────────────────────────────────────────────
 * Shown when the law dataset is ≥ 7 days stale.
 * User can: Update Now · Remind in X days · Dismiss for session
 *
 * Props:
 *  onDismiss  — called after any action (hides the banner)
 *  onUpdated  — called after a successful update run
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Animated,
  ActivityIndicator,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';

import { Colors, Gradients, Typography, Spacing, Radius, Shadows } from '../theme';
import {
  runUpdate,
  remindLater,
  dismissForSession,
  getUpdateStatus,
} from '../services/updateService';

interface Props {
  onDismiss: () => void;
  onUpdated?: () => void;
}

export function UpdateBanner({ onDismiss, onUpdated }: Props) {
  const [daysSince, setDaysSince]   = useState<number | null>(null);
  const [running,   setRunning]     = useState(false);
  const [result,    setResult]      = useState<string | null>(null);

  const slideY = useRef(new Animated.Value(-120)).current;
  const opacity = useRef(new Animated.Value(0)).current;

  // Slide in on mount
  useEffect(() => {
    Animated.parallel([
      Animated.spring(slideY, { toValue: 0, tension: 80, friction: 12, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 1, duration: 300, useNativeDriver: true }),
    ]).start();

    getUpdateStatus().then((s) => {
      if (s) setDaysSince(s.days_since_run);
    });
  }, []);

  const slideOut = (cb: () => void) => {
    Animated.parallel([
      Animated.timing(slideY, { toValue: -140, duration: 280, useNativeDriver: true }),
      Animated.timing(opacity, { toValue: 0,    duration: 280, useNativeDriver: true }),
    ]).start(cb);
  };

  const handleUpdate = async () => {
    setRunning(true);
    const res = await runUpdate();
    setRunning(false);
    if (res?.ok) {
      setResult('✓ Update started in background');
      onUpdated?.();
      setTimeout(() => slideOut(onDismiss), 2000);
    } else {
      setResult('Could not reach server. Check connection.');
      setTimeout(() => slideOut(onDismiss), 2500);
    }
  };

  const handleRemind = async (days: number) => {
    await remindLater(days);
    slideOut(onDismiss);
  };

  const handleDismiss = async () => {
    await dismissForSession();
    slideOut(onDismiss);
  };

  const ago = daysSince !== null
    ? daysSince === 0 ? 'today' : `${daysSince}d ago`
    : '—';

  return (
    <Animated.View style={[styles.wrap, { transform: [{ translateY: slideY }], opacity }]}>
      <LinearGradient
        colors={['#0d1f3c', '#112240']}
        style={StyleSheet.absoluteFillObject}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
      />
      {/* Left accent bar */}
      <View style={styles.accent} />

      <View style={styles.body}>
        {/* Icon + title row */}
        <View style={styles.titleRow}>
          <View style={styles.iconWrap}>
            <LinearGradient colors={Gradients.blueGlow} style={StyleSheet.absoluteFillObject} />
            <Ionicons name="cloud-download-outline" size={18} color={Colors.white} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.title}>Law dataset update available</Text>
            <Text style={styles.sub}>Last updated: {ago} · Only .gov.in sources</Text>
          </View>
          {/* Close */}
          <TouchableOpacity onPress={handleDismiss} style={styles.closeBtn} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="close" size={18} color={Colors.gray} />
          </TouchableOpacity>
        </View>

        {/* Result message */}
        {result ? (
          <Text style={styles.resultText}>{result}</Text>
        ) : running ? (
          <View style={styles.runningRow}>
            <ActivityIndicator size="small" color={Colors.blueVibrant} />
            <Text style={styles.runningText}>Connecting to backend…</Text>
          </View>
        ) : (
          <>
            {/* Primary action */}
            <TouchableOpacity onPress={handleUpdate} style={styles.updateBtn} activeOpacity={0.85}>
              <LinearGradient colors={Gradients.blueGloss} style={styles.updateBtnGrad}>
                <Ionicons name="refresh" size={15} color={Colors.white} />
                <Text style={styles.updateBtnText}>Update Now</Text>
              </LinearGradient>
            </TouchableOpacity>

            {/* Remind options */}
            <View style={styles.remindRow}>
              <Text style={styles.remindLabel}>Remind me in:</Text>
              {[7, 14, 30].map((d) => (
                <TouchableOpacity
                  key={d}
                  style={styles.remindChip}
                  onPress={() => handleRemind(d)}
                  activeOpacity={0.75}
                >
                  <Text style={styles.remindChipText}>{d}d</Text>
                </TouchableOpacity>
              ))}
            </View>
          </>
        )}
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    marginHorizontal: Spacing.base,
    marginBottom:     Spacing.sm,
    borderRadius:     Radius.lg,
    overflow:         'hidden',
    borderWidth:      1,
    borderColor:      'rgba(41, 121, 255, 0.25)',
    ...Shadows.glow,
  },
  accent: {
    position:        'absolute',
    left:            0,
    top:             0,
    bottom:          0,
    width:           3,
    backgroundColor: Colors.blueVibrant,
  },
  body: {
    paddingVertical:   Spacing.md,
    paddingLeft:       Spacing.lg + 3,   // offset for accent bar
    paddingRight:      Spacing.md,
    gap:               Spacing.sm,
  },
  titleRow: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           Spacing.sm,
  },
  iconWrap: {
    width:          32,
    height:         32,
    borderRadius:   Radius.md,
    overflow:       'hidden',
    alignItems:     'center',
    justifyContent: 'center',
    flexShrink:     0,
  },
  title: {
    fontSize:   Typography.sm,
    fontWeight: Typography.bold,
    color:      Colors.white,
  },
  sub: {
    fontSize:  Typography.xs,
    color:     Colors.gray,
    marginTop: 1,
  },
  closeBtn: { padding: 2 },

  updateBtn: {
    borderRadius: Radius.md,
    overflow:     'hidden',
    alignSelf:    'flex-start',
    marginTop:    Spacing.xs,
  },
  updateBtnGrad: {
    flexDirection:  'row',
    alignItems:     'center',
    gap:            6,
    paddingVertical:  9,
    paddingHorizontal: Spacing.md,
  },
  updateBtnText: {
    color:      Colors.white,
    fontWeight: Typography.semibold,
    fontSize:   Typography.sm,
  },

  remindRow: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           Spacing.xs,
    flexWrap:      'wrap',
    marginTop:     2,
  },
  remindLabel: {
    fontSize: Typography.xs,
    color:    Colors.gray,
  },
  remindChip: {
    paddingHorizontal: 10,
    paddingVertical:   4,
    borderRadius:      Radius.full,
    borderWidth:       1,
    borderColor:       Colors.navyBorder,
    backgroundColor:   Colors.navyMid,
  },
  remindChipText: {
    fontSize:   Typography.xs,
    color:      Colors.grayLight,
    fontWeight: Typography.semibold,
  },

  runningRow: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           Spacing.sm,
    marginTop:     Spacing.xs,
  },
  runningText: {
    fontSize: Typography.sm,
    color:    Colors.gray,
  },
  resultText: {
    fontSize:   Typography.sm,
    color:      Colors.blueVibrant,
    fontWeight: Typography.semibold,
    marginTop:  Spacing.xs,
  },
});
