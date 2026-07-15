/**
 * ChatBubble — a single message row in the chat feed
 */
import React, { useRef, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Animated,
  ActivityIndicator,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Colors, Gradients, Typography, Spacing, Radius } from '../theme';
import { ChatMessage } from '../store/chatStore';
import { FineCard } from './FineCard';
import { MultiSelect } from './MultiSelect';
import { Logo } from './Brand';
import { TtsButton } from './TtsButton';

interface Props {
  message:          ChatMessage;
  onChipPress:      (chipId: string, label: string) => void;
  onMultiSubmit?:   (ids: string[], otherText?: string) => void;
  onMcqProceed?:    () => void;
  isOnline?:        boolean;
  sarvamAvailable?: boolean;  // specifically gates TTS button visibility
  onTtsError?:      (msg: string) => void;
}

/** Lightweight **bold** markdown renderer (no external dep). */
function RichText({ text, style, boldStyle }: { text: string; style: any; boldStyle: any }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return (
    <Text style={style}>
      {parts.map((p, i) =>
        p.startsWith('**') && p.endsWith('**') ? (
          <Text key={i} style={boldStyle}>{p.slice(2, -2)}</Text>
        ) : (
          <Text key={i}>{p}</Text>
        )
      )}
    </Text>
  );
}

function TypingDots() {
  const d1 = useRef(new Animated.Value(0.3)).current;
  const d2 = useRef(new Animated.Value(0.3)).current;
  const d3 = useRef(new Animated.Value(0.3)).current;

  useEffect(() => {
    const bounce = (dot: Animated.Value, delay: number) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(delay),
          Animated.timing(dot, { toValue: 1, duration: 300, useNativeDriver: true }),
          Animated.timing(dot, { toValue: 0.3, duration: 300, useNativeDriver: true }),
        ])
      );
    Animated.parallel([bounce(d1, 0), bounce(d2, 150), bounce(d3, 300)]).start();
  }, []);

  const dot = (anim: Animated.Value) => (
    <Animated.View style={[td.dot, { opacity: anim }]} />
  );
  return <View style={td.wrap}>{dot(d1)}{dot(d2)}{dot(d3)}</View>;
}

export function ChatBubble({ message, onChipPress, onMultiSubmit, onMcqProceed, isOnline = false, sarvamAvailable = false, onTtsError }: Props) {
  const slideAnim = useRef(new Animated.Value(message.role === 'user' ? 20 : -20)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.spring(slideAnim, { toValue: 0, tension: 100, friction: 12, useNativeDriver: true }),
      Animated.timing(fadeAnim,  { toValue: 1, duration: 250, useNativeDriver: true }),
    ]).start();
  }, []);

  const isUser      = message.role === 'user';
  const isPending   = message.pending;
  const hasContent  = !!message.content;
  const hasFineCard = !!message.fine_card;
  const hasChips    = !!(message.chips?.length);
  // Show translated text when available, fall back to original
  const displayText = message.translatedContent ?? message.content;

  // Every assistant chip list is a vertical checkbox MCQ — never horizontal pills.
  const showMcq = hasChips && !isUser;

  return (
    <Animated.View
      style={[
        styles.row,
        isUser ? styles.rowUser : styles.rowAssistant,
        { opacity: fadeAnim, transform: [{ translateX: slideAnim }] },
      ]}
    >
      {!isUser && (
        <View style={styles.avatar}>
          <Logo size={32} ring glow={false} />
        </View>
      )}

      <View style={[styles.bubbleGroup, isUser && { alignItems: 'flex-end' }]}>
        {(hasContent || isPending) && (
          <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAssistant]}>
            {isUser ? (
              <LinearGradient
                colors={Gradients.userBubble}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 1 }}
                style={StyleSheet.absoluteFillObject}
              />
            ) : null}
            {isPending ? (
              <TypingDots />
            ) : (
              <>
                <RichText
                  text={displayText}
                  style={[styles.bubbleText, isUser && styles.bubbleTextUser]}
                  boldStyle={styles.bubbleTextBold}
                />
                {/* Translation loading shimmer */}
                {message.isTranslating && !isUser && (
                  <View style={styles.translatingRow}>
                    <ActivityIndicator size={10} color={Colors.blueVibrant} />
                    <Text style={styles.translatingText}>Translating…</Text>
                  </View>
                )}
                {/* TTS + translation indicator row */}
                {!isUser && !message.isTranslating && hasContent && (
                  <View style={styles.bubbleFooter}>
                    <TtsButton
                      text={displayText}
                      isOnline={sarvamAvailable}
                      onError={onTtsError}
                    />
                    {message.translatedContent && (
                      <Text style={styles.translatedBadge}>
                        🌐 translated
                      </Text>
                    )}
                  </View>
                )}
              </>
            )}
          </View>
        )}

        {showMcq && (
          <MultiSelect
            options={message.chips!}
            mode={message.selection_mode === 'single' ? 'single' : 'multi'}
            allowOther={message.allow_other === true}
            onSubmit={(ids, otherText) => onMultiSubmit?.(ids, otherText)}
            onProceed={onMcqProceed}
          />
        )}

        {hasFineCard && (
          <FineCard card={message.fine_card!} detailTable={message.detail_table} />
        )}
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection:  'row',
    paddingHorizontal: Spacing.base,
    marginVertical: 4,
    gap:            Spacing.sm,
  },
  rowUser:      { justifyContent: 'flex-end' },
  rowAssistant: { justifyContent: 'flex-start' },

  avatar: { marginTop: 4 },

  bubbleGroup: { flex: 1, maxWidth: '85%', gap: Spacing.sm },

  bubble: {
    borderRadius: Radius.lg,
    overflow:     'hidden',
    maxWidth:     '100%',
  },
  bubbleUser: {
    borderBottomRightRadius: Radius.xs,
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.3, shadowRadius: 6, elevation: 4,
  },
  bubbleAssistant: {
    backgroundColor:        Colors.surfaceRaised,
    borderWidth:            1,
    borderColor:            Colors.navyBorder,
    borderBottomLeftRadius: Radius.xs,
  },
  bubbleText: {
    fontSize:   Typography.base,
    color:      Colors.textSecondary,
    lineHeight: Typography.base * 1.55,
    padding:    Spacing.md,
  },
  bubbleTextUser: { color: Colors.white },
  bubbleTextBold: {
    fontWeight: Typography.bold,
    color:      Colors.white,
  },
  translatingRow: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           4,
    paddingHorizontal: Spacing.md,
    paddingBottom: Spacing.sm,
  },
  translatingText: {
    fontSize: Typography.xs,
    color:    Colors.blueVibrant,
    fontStyle: 'italic' as const,
  },
  bubbleFooter: {
    flexDirection:  'row',
    alignItems:     'center',
    gap:            Spacing.xs,
    paddingHorizontal: Spacing.md,
    paddingBottom:  Spacing.xs,
  },
  translatedBadge: {
    fontSize:  9,
    color:     Colors.gray,
    fontStyle: 'italic' as const,
  },
});

const td = StyleSheet.create({
  wrap: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 8, paddingHorizontal: 12 },
  dot:  { width: 7, height: 7, borderRadius: 4, backgroundColor: Colors.bluePale },
});
