/** Right-hand pane on desktop web before a chat is opened. */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { Colors, Gradients, Spacing } from '../theme';
import { FloatingLogo } from './Brand';

const TIPS = [
  { icon: 'calculator-outline', text: 'Calculator — pick location, road, vehicle and offence; get the exact fine.' },
  { icon: 'sparkles-outline', text: 'AI Chat — describe what happened, even with several people and offences.' },
  { icon: 'people-outline', text: 'Get a person-by-person breakdown: who is liable, for what, and how much.' },
] as const;

export function DesktopWelcome() {
  return (
    <View style={s.wrap}>
      <LinearGradient colors={Gradients.navyDeep} style={StyleSheet.absoluteFill} />
      <FloatingLogo size={96} />
      <Text style={s.title}>DriveLegal</Text>
      <Text style={s.sub}>Open a chat on the left, or start a new one.</Text>
      <View style={s.tips}>
        {TIPS.map((t) => (
          <View key={t.text} style={s.tip}>
            <Ionicons name={t.icon as any} size={18} color={Colors.bluePale} />
            <Text style={s.tipText}>{t.text}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  wrap:   { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.xxl },
  title:  { color: Colors.white, fontSize: 30, fontWeight: '800', marginTop: Spacing.lg },
  sub:    { color: Colors.textSecondary, fontSize: 15, marginTop: Spacing.sm },
  tips:   { marginTop: Spacing.xxl, gap: Spacing.md, maxWidth: 520, width: '100%' },
  tip:    { flexDirection: 'row', gap: Spacing.md, alignItems: 'center', backgroundColor: Colors.surfaceCard,
            borderWidth: 1, borderColor: Colors.navyBorder, borderRadius: 14, padding: Spacing.base },
  tipText:{ color: Colors.textSecondary, fontSize: 14, flex: 1, lineHeight: 20 },
});
