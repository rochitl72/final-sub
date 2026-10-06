/**
 * LanguageSelector.tsx — Colorful Indian language picker
 * ────────────────────────────────────────────────────────
 * Globe icon trigger in chat header → bottom-sheet modal with 11 languages.
 * Each language has its script character, native name, and a unique accent color.
 * Online-only: shows "requires internet" hint when offline.
 */

import React, { useCallback, useRef, useState } from 'react';
import {
  Animated,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { BlurView } from 'expo-blur';

import { Colors, Radius, Spacing, Typography, Shadows } from '../theme';
import { Language, SUPPORTED_LANGUAGES } from '../services/sarvamApi';
import { useLanguageStore } from '../store/languageStore';

interface Props {
  isOnline:    boolean;
  onLanguageChange?: (lang: Language) => void;
}

// ── Trigger button (shown in header) ─────────────────────────────────────────

export function LanguageTrigger({ isOnline, onLanguageChange }: Props) {
  const { language } = useLanguageStore();
  const [open, setOpen] = useState(false);

  return (
    <>
      <TouchableOpacity
        style={[styles.trigger, !isOnline && styles.triggerDim]}
        onPress={() => isOnline && setOpen(true)}
        activeOpacity={0.75}
      >
        <LinearGradient
          colors={isOnline ? [language.color + '33', language.color + '18'] : ['#0a1628', '#0a1628']}
          style={StyleSheet.absoluteFill}
          start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
        />
        <View style={[styles.triggerBorder, { borderColor: isOnline ? language.color + '66' : Colors.navyBorder }]} />
        <Text style={[styles.triggerScript, { color: isOnline ? language.color : Colors.gray }]}>
          {language.script}
        </Text>
        <View style={styles.globeBadge}>
          <Ionicons name="globe-outline" size={9} color={isOnline ? language.color : Colors.gray} />
        </View>
        {!isOnline && (
          <View style={styles.offlineDot} />
        )}
      </TouchableOpacity>

      <LanguageSelectorModal
        visible={open}
        onClose={() => setOpen(false)}
        onSelect={(lang) => {
          setOpen(false);
          onLanguageChange?.(lang);
        }}
      />
    </>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────────

function LanguageSelectorModal({
  visible,
  onClose,
  onSelect,
}: {
  visible:  boolean;
  onClose:  () => void;
  onSelect: (lang: Language) => void;
}) {
  const { language: current, setLanguage } = useLanguageStore();
  const slideAnim = useRef(new Animated.Value(400)).current;

  React.useEffect(() => {
    if (visible) {
      Animated.spring(slideAnim, { toValue: 0, tension: 90, friction: 14, useNativeDriver: true }).start();
    } else {
      slideAnim.setValue(400);
    }
  }, [visible]);

  const handleSelect = useCallback((lang: Language) => {
    setLanguage(lang);
    onSelect(lang);
  }, [setLanguage, onSelect]);

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      statusBarTranslucent
      onRequestClose={onClose}
    >
      {/* Backdrop */}
      <Pressable style={styles.backdrop} onPress={onClose}>
        <View style={StyleSheet.absoluteFill} />
      </Pressable>

      {/* Sheet */}
      <Animated.View style={[styles.sheet, { transform: [{ translateY: slideAnim }] }]}>
        <LinearGradient
          colors={['#050d1f', '#0a1628']}
          style={StyleSheet.absoluteFill}
        />
        <View style={styles.sheetBorder} />

        {/* Handle */}
        <View style={styles.handle} />

        {/* Header */}
        <View style={styles.sheetHeader}>
          <Ionicons name="language" size={20} color={Colors.blueVibrant} />
          <Text style={styles.sheetTitle}>Response Language</Text>
          <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="close" size={20} color={Colors.gray} />
          </TouchableOpacity>
        </View>

        <Text style={styles.sheetSub}>
          Sarvam AI (or Groq) · All fines & chat replies translated instantly
        </Text>

        {/* Language grid */}
        <ScrollView
          contentContainerStyle={styles.grid}
          showsVerticalScrollIndicator={false}
        >
          {SUPPORTED_LANGUAGES.map((lang) => {
            const isSelected = lang.code === current.code;
            return (
              <TouchableOpacity
                key={lang.code}
                style={[styles.langCard, isSelected && { borderColor: lang.color }]}
                onPress={() => handleSelect(lang)}
                activeOpacity={0.75}
              >
                {isSelected && (
                  <LinearGradient
                    colors={[lang.color + '22', lang.color + '0a']}
                    style={StyleSheet.absoluteFill}
                  />
                )}
                {/* Script badge */}
                <View style={[styles.scriptBadge, { backgroundColor: lang.color + '22', borderColor: lang.color + '55' }]}>
                  <Text style={[styles.scriptChar, { color: lang.color }]}>{lang.script}</Text>
                </View>

                {/* Language info */}
                <Text style={[styles.langNative, isSelected && { color: Colors.white }]}>
                  {lang.name}
                </Text>
                <Text style={styles.langLabel}>{lang.label}</Text>

                {isSelected && (
                  <View style={[styles.checkDot, { backgroundColor: lang.color }]}>
                    <Ionicons name="checkmark" size={10} color="#fff" />
                  </View>
                )}
              </TouchableOpacity>
            );
          })}
        </ScrollView>

        <View style={styles.footer}>
          <Ionicons name="sparkles" size={12} color={Colors.gray} />
          <Text style={styles.footerText}>Change language anytime — previous messages update automatically</Text>
        </View>
      </Animated.View>
    </Modal>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  // Trigger
  trigger: {
    width:          36,
    height:         36,
    borderRadius:   Radius.md,
    overflow:       'hidden',
    alignItems:     'center',
    justifyContent: 'center',
    position:       'relative',
  },
  triggerDim: { opacity: 0.5 },
  triggerBorder: {
    ...StyleSheet.absoluteFill,
    borderRadius: Radius.md,
    borderWidth:  1,
  },
  triggerScript: {
    fontSize:   14,
    fontWeight: '700' as const,
    lineHeight: 18,
  },
  globeBadge: {
    position:        'absolute',
    bottom:          2,
    right:           2,
  },
  offlineDot: {
    position:        'absolute',
    top:             2,
    right:           2,
    width:           6,
    height:          6,
    borderRadius:    3,
    backgroundColor: Colors.error,
  },

  // Modal backdrop
  backdrop: {
    flex:            1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent:  'flex-end',
  },

  // Bottom sheet
  sheet: {
    position:         'absolute',
    bottom:           0,
    left:             0,
    right:            0,
    borderTopLeftRadius:  Radius.xl,
    borderTopRightRadius: Radius.xl,
    overflow:         'hidden',
    paddingBottom:    Platform.OS === 'ios' ? 32 : 16,
    maxHeight:        '80%',
    // Desktop browsers: a centred sheet instead of a full-width strip
    ...(Platform.OS === 'web' ? { maxWidth: 560, marginLeft: 'auto', marginRight: 'auto' } : null),
  },
  sheetBorder: {
    ...StyleSheet.absoluteFill,
    borderTopLeftRadius:  Radius.xl,
    borderTopRightRadius: Radius.xl,
    borderWidth:          1,
    borderColor:          Colors.navyBorder,
    borderBottomWidth:    0,
  },
  handle: {
    width:           40,
    height:          4,
    borderRadius:    2,
    backgroundColor: Colors.navyBorder,
    alignSelf:       'center',
    marginTop:       12,
    marginBottom:    4,
  },
  sheetHeader: {
    flexDirection:  'row',
    alignItems:     'center',
    gap:            Spacing.sm,
    paddingHorizontal: Spacing.lg,
    paddingVertical:   Spacing.md,
  },
  sheetTitle: {
    flex:       1,
    fontSize:   Typography.md,
    fontWeight: Typography.bold,
    color:      Colors.white,
  },
  sheetSub: {
    fontSize:          Typography.xs,
    color:             Colors.gray,
    paddingHorizontal: Spacing.lg,
    marginBottom:      Spacing.md,
  },

  // Language grid — 3 columns
  grid: {
    flexDirection:  'row',
    flexWrap:       'wrap',
    paddingHorizontal: Spacing.md,
    gap:            Spacing.sm,
    paddingBottom:  Spacing.md,
  },
  langCard: {
    width:          '30%',
    backgroundColor: Colors.navyMid,
    borderWidth:     1,
    borderColor:     Colors.navyBorder,
    borderRadius:    Radius.lg,
    padding:         Spacing.sm,
    alignItems:      'center',
    gap:             4,
    overflow:        'hidden',
    position:        'relative',
  },
  scriptBadge: {
    width:          44,
    height:         44,
    borderRadius:   Radius.md,
    borderWidth:    1,
    alignItems:     'center',
    justifyContent: 'center',
    marginBottom:   2,
  },
  scriptChar: {
    fontSize:   22,
    fontWeight: '700' as const,
    lineHeight: 28,
  },
  langNative: {
    fontSize:  Typography.sm,
    fontWeight: Typography.semibold,
    color:      Colors.grayLight,
    textAlign: 'center',
  },
  langLabel: {
    fontSize:  Typography.xs,
    color:     Colors.gray,
    textAlign: 'center',
  },
  checkDot: {
    position:       'absolute',
    top:            6,
    right:          6,
    width:          16,
    height:         16,
    borderRadius:   8,
    alignItems:     'center',
    justifyContent: 'center',
  },

  // Footer
  footer: {
    flexDirection:  'row',
    alignItems:     'center',
    gap:            Spacing.xs,
    paddingHorizontal: Spacing.lg,
    paddingTop:     Spacing.sm,
  },
  footerText: {
    fontSize: Typography.xs,
    color:    Colors.gray,
    flex:     1,
  },
});
