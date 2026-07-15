/**
 * AiModePill — tap to toggle Cloud AI ↔ Rules only (display only).
 */

import React, { useEffect, useRef } from 'react';
import {
  Animated,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

import { Radius } from '../theme';
import { getPillMeta, PillVariant } from '../hooks/useAiConnectivity';

interface Props {
  pillVariant: PillVariant;
  pillLabel:   string;
  onToggle:    () => void;
}

export function AiModePill({ pillVariant, pillLabel, onToggle }: Props) {
  const fadeAnim  = useRef(new Animated.Value(1)).current;
  const pulseAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, { toValue: 1.7, duration: 900, useNativeDriver: true }),
        Animated.timing(pulseAnim, { toValue: 1.0, duration: 900, useNativeDriver: true }),
      ]),
    ).start();
  }, [pulseAnim]);

  useEffect(() => {
    Animated.sequence([
      Animated.timing(fadeAnim, { toValue: 0.3, duration: 150, useNativeDriver: true }),
      Animated.timing(fadeAnim, { toValue: 1.0, duration: 150, useNativeDriver: true }),
    ]).start();
  }, [pillVariant, fadeAnim]);

  const meta = getPillMeta(pillVariant);
  const dimmed = pillVariant === 'no_network' || pillVariant === 'no_server';

  return (
    <TouchableOpacity
      activeOpacity={0.75}
      onPress={onToggle}
      accessibilityLabel={`AI mode: ${pillLabel}. Tap to switch Cloud AI or Rules only.`}
      accessibilityRole="button"
    >
      <Animated.View style={[styles.pillWrap, { opacity: fadeAnim }]}>
        <LinearGradient
          colors={meta.bg}
          style={StyleSheet.absoluteFillObject}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
        />
        <View style={[styles.pillBorder, { borderColor: meta.dot + '44' }]} />

        <View style={styles.dotWrap}>
          <Animated.View
            style={[
              styles.dotGlow,
              {
                backgroundColor: meta.dot,
                transform: [{ scale: pulseAnim }],
                opacity: dimmed ? 0.35 : 0.4,
              },
            ]}
          />
          <View style={[styles.dot, { backgroundColor: meta.dot }]} />
        </View>

        <Text style={[styles.label, { color: meta.dot }]}>{pillLabel}</Text>
      </Animated.View>
    </TouchableOpacity>
  );
}

export type { PillVariant };

const DOT = 7;

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
    ...StyleSheet.absoluteFillObject,
    borderRadius: Radius.full,
    borderWidth:  1,
  },
  dotWrap: {
    width:          DOT,
    height:         DOT,
    alignItems:     'center',
    justifyContent: 'center',
  },
  dotGlow: {
    position:     'absolute',
    width:        DOT,
    height:       DOT,
    borderRadius: DOT / 2,
  },
  dot: {
    width:        DOT,
    height:       DOT,
    borderRadius: DOT / 2,
  },
  label: {
    fontSize:      10,
    fontWeight:    '700',
    letterSpacing: 0.3,
  },
});
