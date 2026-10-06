/**
 * SessionCard — sidebar / home list item
 */
import React, { useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Animated,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { Colors, Typography, Spacing, Radius, Shadows } from '../theme';
import { Session } from '../services/api';

interface Props {
  session:   Session;
  isActive?: boolean;
  onPress:   () => void;
  onDelete:  () => void;
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  const now = new Date();
  const diffH = (now.getTime() - ms) / 3600000;
  if (diffH < 24) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } else if (diffH < 168) {
    return d.toLocaleDateString([], { weekday: 'short' });
  }
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

const MODE_META: Record<string, { lib: 'ion' | 'mci'; icon: string; color: string; label: string }> = {
  static:  { lib: 'mci', icon: 'calculator-variant', color: Colors.blueVibrant, label: 'Calculator' },
  dynamic: { lib: 'ion', icon: 'sparkles',           color: Colors.accent,      label: 'AI Chat' },
};

export function SessionCard({ session, isActive, onPress, onDelete }: Props) {
  const meta   = MODE_META[session.mode] || MODE_META.static;
  const scaleAnim = useRef(new Animated.Value(1)).current;

  const onPressIn  = () => Animated.spring(scaleAnim, { toValue: 0.97, useNativeDriver: true }).start();
  const onPressOut = () => Animated.spring(scaleAnim, { toValue: 1.0,  useNativeDriver: true }).start();

  return (
    <Animated.View style={[styles.wrap, { transform: [{ scale: scaleAnim }] }]}>
      <TouchableOpacity
        activeOpacity={0.9}
        onPress={onPress}
        onPressIn={onPressIn}
        onPressOut={onPressOut}
        style={styles.touchArea}
      >
        <LinearGradient
          colors={isActive
            ? ['rgba(37,99,235,0.2)', 'rgba(26,79,168,0.15)']
            : ['rgba(15,32,64,0.8)', 'rgba(10,22,40,0.9)']
          }
          style={[styles.card, isActive && styles.cardActive]}
        >
          {/* Left accent bar */}
          {isActive && <View style={[styles.accentBar, { backgroundColor: meta.color }]} />}

          {/* Mode icon */}
          <View style={[styles.modeIcon, { borderColor: meta.color + '40' }]}>
            {meta.lib === 'ion' ? (
              <Ionicons name={meta.icon as any} size={20} color={meta.color} />
            ) : (
              <MaterialCommunityIcons name={meta.icon as any} size={20} color={meta.color} />
            )}
          </View>

          {/* Text content */}
          <View style={styles.textBlock}>
            <Text style={styles.title} numberOfLines={1}>
              {session.title || 'New chat'}
            </Text>
            {!!session.last_snippet && (
              <Text style={styles.snippet} numberOfLines={1}>
                {session.last_snippet.replace(/\*\*/g, '')}
              </Text>
            )}
            <View style={styles.metaRow}>
              <View style={[styles.modePill, { borderColor: meta.color + '50' }]}>
                <Text style={[styles.modeLabel, { color: meta.color }]}>{meta.label}</Text>
              </View>
              <Text style={styles.time}>{formatTime(session.updated_at)}</Text>
            </View>
          </View>

          {/* Delete button */}
          <TouchableOpacity
            style={styles.deleteBtn}
            onPress={onDelete}
            hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
          >
            <Ionicons name="close" size={16} color={Colors.grayDark} />
          </TouchableOpacity>
        </LinearGradient>
      </TouchableOpacity>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap:      { marginHorizontal: Spacing.base, marginVertical: 4 },
  touchArea: { borderRadius: Radius.lg, overflow: 'hidden' },
  card: {
    flexDirection:  'row',
    alignItems:     'center',
    borderRadius:   Radius.lg,
    borderWidth:    1,
    borderColor:    Colors.navyBorder,
    padding:        Spacing.md,
    gap:            Spacing.md,
    ...Shadows.subtle,
  },
  cardActive: {
    borderColor: Colors.blueVibrant + '60',
  },
  accentBar: {
    position:     'absolute',
    left:         0,
    top:          '15%',
    bottom:       '15%',
    width:        3,
    borderRadius: 3,
  },
  modeIcon: {
    width:          42,
    height:         42,
    borderRadius:   Radius.md,
    borderWidth:    1,
    backgroundColor: 'rgba(37,99,235,0.08)',
    alignItems:     'center',
    justifyContent: 'center',
  },
  modeEmoji: { fontSize: 20 },

  textBlock:  { flex: 1, gap: 2 },
  title: {
    fontSize:   Typography.base,
    fontWeight: Typography.semibold,
    color:      Colors.white,
  },
  snippet: {
    fontSize:   Typography.sm,
    color:      Colors.gray,
    lineHeight: Typography.sm * 1.4,
  },
  metaRow: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           Spacing.sm,
    marginTop:     2,
  },
  modePill: {
    borderWidth:     1,
    borderRadius:    Radius.full,
    paddingVertical: 1,
    paddingHorizontal: 7,
  },
  modeLabel: { fontSize: Typography.xs, fontWeight: Typography.medium },
  time: {
    fontSize: Typography.xs,
    color:    Colors.grayDark,
  },

  deleteBtn: {
    padding: Spacing.xs,
  },
  deleteIcon: {
    fontSize: 12,
    color:    Colors.grayDark,
  },
});
