/**
 * ProgressBar — 4-slot indicator (Location → Road → Vehicle → Violation)
 * Rewritten for React 19 / RN 0.81 compatibility (no Animated.Value width interpolation)
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { Colors, Gradients, Typography, Spacing, Radius } from '../theme';
import { SessionState } from '../services/api';

interface Props {
  sessionState: SessionState | null;
}

type IconLib = 'ion' | 'mci';
const STEPS: { key: string; label: string; lib: IconLib; icon: string; field: keyof SessionState }[] = [
  { key: 'location',  label: 'Location',  lib: 'ion', icon: 'location',      field: 'state_code'      },
  { key: 'road',      label: 'Road',      lib: 'mci', icon: 'road-variant',  field: 'road_bucket'     },
  { key: 'vehicle',   label: 'Vehicle',   lib: 'ion', icon: 'car-sport',     field: 'vehicle_segment' },
  { key: 'violation', label: 'Violation', lib: 'ion', icon: 'alert-circle',  field: 'violation_code'  },
];

function StepIcon({ lib, name, color }: { lib: IconLib; name: string; color: string }) {
  return lib === 'ion'
    ? <Ionicons name={name as any} size={15} color={color} />
    : <MaterialCommunityIcons name={name as any} size={15} color={color} />;
}

export function ProgressBar({ sessionState }: Props) {
  if (!sessionState) return null;

  const filledCount = STEPS.filter((s) => !!sessionState[s.field]).length;
  const pct = `${Math.round((filledCount / STEPS.length) * 100)}%` as any;

  return (
    <View style={styles.wrap}>
      {/* Step dots */}
      <View style={styles.steps}>
        {STEPS.map((step, idx) => {
          const filled = !!sessionState[step.field];
          const active = !filled && (idx === 0 || !!sessionState[STEPS[idx - 1].field]);
          return (
            <View key={step.key} style={styles.step}>
              <View style={[
                styles.dot,
                filled ? styles.dotFilled : active ? styles.dotActive : styles.dotEmpty,
              ]}>
                {filled && (
                  <LinearGradient colors={Gradients.blueGloss} style={StyleSheet.absoluteFill} />
                )}
                <StepIcon
                  lib={step.lib}
                  name={step.icon}
                  color={filled ? Colors.white : active ? Colors.bluePale : Colors.grayDark}
                />
              </View>
              <Text style={[
                styles.label,
                filled ? styles.labelFilled : active ? styles.labelActive : styles.labelEmpty,
              ]} numberOfLines={1}>
                {step.label}
              </Text>
            </View>
          );
        })}
      </View>

      {/* Progress track — plain View, no Animated.Value */}
      <View style={styles.track}>
        <View style={[styles.fill, { width: pct }]}>
          <LinearGradient colors={Gradients.blueGlow} style={StyleSheet.absoluteFill} />
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    paddingHorizontal:  Spacing.base,
    paddingBottom:      Spacing.sm,
    paddingTop:         Spacing.sm,
    backgroundColor:    Colors.navyMid,
    borderBottomWidth:  1,
    borderBottomColor:  Colors.navyBorder,
  },
  steps: {
    flexDirection:  'row',
    justifyContent: 'space-around',
    marginBottom:   Spacing.sm,
  },
  step:       { alignItems: 'center', gap: 4, flex: 1 },

  dot: {
    width:          32,
    height:         32,
    borderRadius:   Radius.md,
    alignItems:     'center',
    justifyContent: 'center',
    overflow:       'hidden',
  },
  dotFilled: { /* gradient fills it */ },
  dotActive: {
    backgroundColor: Colors.navyLight,
    borderWidth:     1,
    borderColor:     Colors.blueVibrant,
  },
  dotEmpty: {
    backgroundColor: Colors.navyMid,
    borderWidth:     1,
    borderColor:     Colors.navyBorder,
  },
  dotIcon: { fontSize: 14 },

  label:        { fontSize: Typography.xs, textAlign: 'center' },
  labelFilled:  { color: Colors.white,    fontWeight: '600' },
  labelActive:  { color: Colors.bluePale },
  labelEmpty:   { color: Colors.grayDark },

  track: {
    height:          3,
    backgroundColor: Colors.navyBorder,
    borderRadius:    Radius.full,
    overflow:        'hidden',
  },
  fill: {
    height:          '100%',
    borderRadius:    Radius.full,
    overflow:        'hidden',
  },
});
