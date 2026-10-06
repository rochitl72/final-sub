/**
 * ChatScreen
 * ──────────────────────────────────────────────────────────────
 * Full chat UI supporting both Static (calculator) and Dynamic (AI)
 * modes. Faithfully mirrors the web frontend's functionality:
 *
 *  - Chip-based slot filling
 *  - Free-text input (when allow_text is true)
 *  - Fine card rendering
 *  - Progress bar (4 slots)
 *  - Location picker (map pin or manual state/city)
 *  - Mode toggle (calculator ↔ chatbot)
 *  - Session history hydration on open
 */

import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  TextInput,
  KeyboardAvoidingView,
  Platform,
  Animated,
  StatusBar,
  ActivityIndicator,
  Dimensions,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { useRouter, useLocalSearchParams } from 'expo-router';

import { Colors, Gradients, Typography, Spacing, Radius, Shadows } from '../theme';
import { dialogApi, sessionsApi, SessionState, TurnResponse } from '../services/api';
import { useChatStore, ChatMessage, newMsgId, withAnswer } from '../store/chatStore';
import { ChatBubble } from '../components/ChatBubble';
import { ProgressBar } from '../components/ProgressBar';
import { LocationModal } from '../components/LocationModal';
import { AiModePill } from '../components/AiModePill';
import { LanguageTrigger } from '../components/LanguageSelector';
import { useLanguageStore } from '../store/languageStore';
import { useAiModeStore } from '../store/aiModeStore';
import { FALLBACK_TOAST, useAiConnectivity } from '../hooks/useAiConnectivity';
import { setPreferRulesMode } from '../services/api';
import { localTurn, shouldAnswerLocally } from '../offline';
import { translate } from '../services/sarvamApi';
import { useDesktop } from '../hooks/useDesktop';
import type { Language } from '../services/sarvamApi';

const { height: H } = Dimensions.get('window');

// ── Main Chat Screen ──────────────────────────────────────────────────────────

export default function ChatScreen() {
  const router = useRouter();
  const desktop = useDesktop();
  const params = useLocalSearchParams<{ sessionId: string; mode?: string }>();
  const sessionId = params.sessionId;

  const {
    messages, addMessage, setMessages, removePendingMessage, sessionState, setSessionState,
    isSending, setIsSending, applyTurnResponse, activeSessionId, updateMessage,
  } = useChatStore();

  const { language, hydrate: hydrateLanguage } = useLanguageStore();

  const [inputText, setInputText]         = useState('');
  const [showLocModal, setShowLocModal]   = useState(false);
  const [locModalTab, setLocModalTab]     = useState<'gps' | 'map' | 'browse'>('gps');
  const [currentMode, setCurrentMode]     = useState<'static' | 'dynamic'>(
    (params.mode as 'static' | 'dynamic') || 'static'
  );

  // ── Toast ─────────────────────────────────────────────────────────────────
  const [toastMsg,  setToastMsg]  = useState('');
  const toastAnim = useRef(new Animated.Value(0)).current;
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((msg: string) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToastMsg(msg);
    Animated.sequence([
      Animated.timing(toastAnim, { toValue: 1, duration: 280, useNativeDriver: true }),
      Animated.delay(2600),
      Animated.timing(toastAnim, { toValue: 0, duration: 300, useNativeDriver: true }),
    ]).start(() => setToastMsg(''));
  }, [toastAnim]);

  const hydrateAiMode = useAiModeStore((s) => s.hydrate);
  const toggleAiPreference = useAiModeStore((s) => s.togglePreference);

  const aiConnectivity = useAiConnectivity((reason) => {
    showToast(FALLBACK_TOAST[reason]);
  });

  const isOnline       = aiConnectivity.cloudFeaturesEnabled;
  const sarvamAvailable = aiConnectivity.sarvamAvailable;
  // Translation runs on the backend (Sarvam, else Groq), so it only needs the
  // server — not cloud AI mode. Rules mode can be translated too.
  const canTranslate    = aiConnectivity.serverReachable;

  useEffect(() => {
    setPreferRulesMode(aiConnectivity.effectiveMode === 'rules');
  }, [aiConnectivity.effectiveMode]);

  useEffect(() => {
    hydrateLanguage();
    hydrateAiMode();
  }, []);

  // ── Sarvam: translate a single assistant message ─────────────────────────

  const translateMessage = useCallback(async (msgId: string, text: string, targetLang: string) => {
    if (targetLang === 'en-IN' || !text.trim()) return;
    updateMessage(msgId, { isTranslating: true });
    const translated = await translate(text, targetLang);
    // translate() returns the original text on failure — don't badge that as
    // "translated".
    updateMessage(msgId, {
      translatedContent: translated && translated !== text ? translated : undefined,
      isTranslating: false,
    });
  }, [updateMessage]);

  // ── Sarvam: auto-translate latest assistant message whenever messages change

  const lastAutoTranslatedId = useRef<string | null>(null);
  useEffect(() => {
    if (!canTranslate || language.code === 'en-IN') return;
    const lastMsg = messages[messages.length - 1];
    if (!lastMsg || lastMsg.role !== 'assistant' || lastMsg.pending) return;
    if (lastMsg.id === lastAutoTranslatedId.current) return;
    if (lastMsg.translatedContent || lastMsg.isTranslating) return;
    lastAutoTranslatedId.current = lastMsg.id;
    translateMessage(lastMsg.id, lastMsg.content, language.code);
  }, [messages, canTranslate, language.code, translateMessage]);

  // ── Sarvam: progressive re-translation on language change ────────────────

  const retranslateAllRef = useRef(false);
  const isOnlineRef = useRef(canTranslate);
  useEffect(() => { isOnlineRef.current = canTranslate; }, [canTranslate]);

  const retranslateAll = useCallback(async (targetLang: string) => {
    // Clear translations when switching back to English
    if (targetLang === 'en-IN') {
      useChatStore.getState().messages.forEach(m => {
        if (m.role === 'assistant' && m.translatedContent) {
          updateMessage(m.id, { translatedContent: undefined, isTranslating: false });
        }
      });
      return;
    }

    if (!isOnlineRef.current) return;

    retranslateAllRef.current = true;
    // Snapshot messages at this moment (avoids stale closure)
    const botMsgs = useChatStore.getState().messages
      .filter(m => m.role === 'assistant' && !!m.content);

    for (const msg of botMsgs) {
      if (!retranslateAllRef.current) break;     // cancelled by language change
      if (!isOnlineRef.current) break;           // went offline mid-translation
      await translateMessage(msg.id, msg.content, targetLang);
      await new Promise(r => setTimeout(r, 100)); // 100ms gap — respects Sarvam RPM
    }
  }, [translateMessage, updateMessage]);

  // ── Sarvam: handle language change ───────────────────────────────────────

  const handleLanguageChange = useCallback((lang: Language) => {
    retranslateAllRef.current = false;  // cancel any in-progress re-translation
    showToast(`🌐 ${lang.name} — translating conversation…`);
    // Kick off re-translation (non-blocking)
    setTimeout(() => retranslateAll(lang.code), 50);
  }, [retranslateAll]);

  // Cancel re-translation on unmount
  useEffect(() => () => { retranslateAllRef.current = false; }, []);

  const listRef = useRef<FlatList>(null);
  const wasSendingRef = useRef(false);

  const scrollToBottom = useCallback((animated = true) => {
    const run = () => listRef.current?.scrollToEnd({ animated });
    run();
    requestAnimationFrame(run);
    setTimeout(run, 120);
    setTimeout(run, 350);
    setTimeout(run, 600);
  }, []);

  // ── Load history on mount ─────────────────────────────────────────────────

  useEffect(() => {
    if (!sessionId) return;
    setMessages([]);
    sessionsApi.history(sessionId).then(({ messages: hist, session_state }) => {
      const converted: ChatMessage[] = hist.map((m: any) => ({
        id:           newMsgId(),
        role:         m.role,
        content:      m.content || '',
        chips:          m.payload?.chips,
        multi_select:   m.payload?.multi_select,
        selection_mode: m.payload?.selection_mode,
        allow_other:    m.payload?.allow_other,
        fine_card:      m.payload?.fine_card,
        scenario:       m.payload?.scenario,
        replace_last:   !!m.payload?.replace_last,
        detail_table: m.payload?.detail_table,
        explanation:  m.payload?.explanation,
        allow_text:   m.payload?.allow_text,
        intent:       m.payload?.intent,
        payload:      m.payload,
        ts:           m.created_at,
        pending:      false,
      }));
      // Collapse superseded answers exactly like the live chat does.
      setMessages(converted.reduce<ChatMessage[]>((acc, m) => withAnswer(acc, m), []));
      setSessionState(session_state);
      if (session_state?.mode) setCurrentMode(session_state.mode as 'static' | 'dynamic');
      // Kick off the first turn if it's a brand-new session with no messages
      if (converted.length === 0) {
        sendInitialGreeting();
      }
    }).catch(() => {
      // Offline or session doesn't exist — show empty state
    });
  }, [sessionId]);

  const lastMsgKey = messages.length
    ? `${messages[messages.length - 1].id}:${messages[messages.length - 1].content?.length ?? 0}`
    : '';

  // Scroll when messages grow or the latest bubble updates (e.g. after Proceed).
  useEffect(() => {
    if (messages.length > 0) scrollToBottom();
  }, [messages.length, lastMsgKey, scrollToBottom]);

  // Scroll again when a turn finishes (new assistant MCQ / reply rendered).
  useEffect(() => {
    if (wasSendingRef.current && !isSending && messages.length > 0) {
      scrollToBottom();
    }
    wasSendingRef.current = isSending;
  }, [isSending, messages.length, scrollToBottom]);

  // ── Helpers ───────────────────────────────────────────────────────────────

  const sendInitialGreeting = async () => {
    if (!sessionId) return;
    setIsSending(true);
    // Add pending typing indicator
    const pendingId = newMsgId();
    addMessage({ id: pendingId, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      let resp: TurnResponse;
      if (shouldAnswerLocally(aiConnectivity.connectivity)) {
        resp = await localTurn(sessionId, useChatStore.getState().sessionState, 'hi');
      } else {
        resp = await dialogApi.turn(sessionId);
      }
      removePendingMessage(pendingId);
      applyTurnResponse(resp);
    } catch {
      removePendingMessage(pendingId);
      // Even if the greeting call fails, greet locally so the screen isn't blank.
      applyTurnResponse(await localTurn(sessionId, useChatStore.getState().sessionState, 'hi'));
    } finally {
      setIsSending(false);
    }
  };

  const handleSend = useCallback(async (text?: string, chipId?: string) => {
    if (!sessionId || isSending) return;
    const msg = text || inputText.trim();
    if (!msg && !chipId) return;

    setInputText('');
    setIsSending(true);

    // Optimistic user message
    if (msg) {
      addMessage({ id: newMsgId(), role: 'user', content: msg, ts: Date.now() });
    }

    // Typing indicator
    const pendingId = newMsgId();
    addMessage({ id: pendingId, role: 'assistant', content: '', pending: true, ts: Date.now() });

    // Freshest state/connectivity (avoids stale-closure reads).
    const liveState = useChatStore.getState().sessionState;

    try {
      let resp: TurnResponse;
      if (shouldAnswerLocally(aiConnectivity.connectivity)) {
        // No network / no server → answer on-device from the local graph.
        resp = await localTurn(sessionId, liveState, msg || '');
      } else {
        try {
          resp = await dialogApi.turn(sessionId, msg || undefined, chipId);
        } catch (netErr) {
          // Server dropped mid-request → graceful on-device fallback instead
          // of the old "Something went wrong" dead-end.
          resp = await localTurn(sessionId, liveState, msg || '');
        }
      }
      removePendingMessage(pendingId);
      applyTurnResponse(resp);
    } catch (err: any) {
      removePendingMessage(pendingId);
      addMessage({
        id:      newMsgId(),
        role:    'assistant',
        content: 'Something went wrong. Please try again.',
        ts:      Date.now(),
      });
    } finally {
      setIsSending(false);
    }
  }, [sessionId, isSending, inputText, aiConnectivity.connectivity]);

  const handleChipPress = (chipId: string, label: string) => {
    addMessage({ id: newMsgId(), role: 'user', content: label, ts: Date.now() });
    // "Use map pin" should open the map — not advance to state chips on the server.
    if (chipId === 'map') {
      setLocModalTab('map');
      setShowLocModal(true);
      return;
    }
    handleSend(undefined, chipId);
  };

  const handleMultiSubmit = useCallback(async (ids: string[], otherText?: string) => {
    if (!sessionId || isSending) return;

    // Map-pin MCQ selection opens the map modal locally (no server round-trip).
    if (ids.includes('map')) {
      setLocModalTab('map');
      setShowLocModal(true);
      return;
    }

    setIsSending(true);
    const pendingId = newMsgId();
    addMessage({ id: pendingId, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      const resp = await dialogApi.turnMulti(sessionId, ids, otherText);
      removePendingMessage(pendingId);
      applyTurnResponse(resp);
    } catch {
      removePendingMessage(pendingId);
      addMessage({
        id:      newMsgId(),
        role:    'assistant',
        content: 'Something went wrong. Please try again.',
        ts:      Date.now(),
      });
    } finally {
      setIsSending(false);
    }
  }, [sessionId, isSending]);

  const handleModeToggle = async () => {
    if (!sessionId || isSending) return;
    const newMode = currentMode === 'static' ? 'dynamic' : 'static';
    // Optimistic UI; the server response is authoritative.
    setCurrentMode(newMode);
    setIsSending(true);
    const pendingId = newMsgId();
    addMessage({ id: pendingId, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      const resp = await dialogApi.setMode(sessionId, newMode);
      removePendingMessage(pendingId);
      // Trust the server's resolved mode (it may stay put if already there).
      const resolved = (resp.mode || resp.session_state?.mode || newMode) as 'static' | 'dynamic';
      setCurrentMode(resolved);
      applyTurnResponse(resp);
    } catch {
      removePendingMessage(pendingId);
      // Roll back optimistic toggle on failure.
      setCurrentMode(currentMode);
    } finally {
      setIsSending(false);
    }
  };

  const handleLocationManual = async (stateCode?: string, cityCode?: string, text?: string) => {
    if (!sessionId) return;
    const locationHint = text || cityCode || stateCode;
    if (locationHint) showToast(`Location set: ${locationHint}`);
    setIsSending(true);
    const pendingId = newMsgId();
    addMessage({ id: pendingId, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      const resp = await dialogApi.manualLocation(sessionId, { state_code: stateCode, city_code: cityCode, text });
      removePendingMessage(pendingId);
      applyTurnResponse(resp);
    } catch {
      removePendingMessage(pendingId);
    } finally {
      setIsSending(false);
    }
  };

  const handlePinDrop = async (lat: number, lng: number, label?: string) => {
    if (!sessionId) return;
    if (label) showToast(`Location set: ${label}`);
    setIsSending(true);
    const pendingId = newMsgId();
    addMessage({ id: pendingId, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      const resp = await dialogApi.pinDrop(sessionId, lat, lng);
      removePendingMessage(pendingId);
      applyTurnResponse(resp);
    } catch {
      removePendingMessage(pendingId);
    } finally {
      setIsSending(false);
    }
  };

  const handleOpenLocationPicker = () => {
    setLocModalTab(hasState ? 'browse' : 'gps');
    setShowLocModal(true);
  };

  const handleUndoLocation = async () => {
    if (!sessionId || !sessionState?.has_prev_location) return;
    setIsSending(true);
    const pendingId = newMsgId();
    addMessage({ id: pendingId, role: 'assistant', content: '', pending: true, ts: Date.now() });
    try {
      const resp = await dialogApi.revertLocation(sessionId);
      removePendingMessage(pendingId);
      applyTurnResponse(resp);
      scrollToBottom();
    } catch {
      removePendingMessage(pendingId);
    } finally {
      setIsSending(false);
    }
  };

  // Check if free-text input is allowed for the last assistant message
  const lastMsg         = messages[messages.length - 1];
  const allowText       = lastMsg?.role === 'assistant' && lastMsg?.allow_text !== false;
  const hasState        = !!sessionState?.state_code;
  const geoFlowStarted  = !!sessionState?.geo_mode;
  const showLocBtn      = !hasState && !geoFlowStarted && !showLocModal;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      keyboardVerticalOffset={0}
    >
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
      <LinearGradient colors={Gradients.navyDeep} style={StyleSheet.absoluteFill} />

      {/* ── Header ───────────────────────────────────────────────────── */}
      <View style={styles.header}>
        {!desktop && (
          <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
            <Ionicons name="chevron-back" size={24} color={Colors.white} />
          </TouchableOpacity>
        )}

        <View style={styles.headerCenter}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {sessionState?.city_name
              ? `${sessionState.city_name}`
              : sessionState?.state_code
              ? `State: ${sessionState.state_code}`
              : 'New Session'}
          </Text>
          <View style={styles.headerSubRow}>
            {currentMode === 'static' ? (
              <MaterialCommunityIcons name="calculator-variant" size={12} color={Colors.gray} />
            ) : (
              <Ionicons name="sparkles" size={11} color={Colors.gray} />
            )}
            <Text style={styles.headerSub}>
              {currentMode === 'static' ? 'Calculator' : 'AI Chat'}
            </Text>
            {/* Network status pill — only shown in AI Chat mode */}
            {currentMode === 'dynamic' && (
              <AiModePill
                pillVariant={aiConnectivity.pillVariant}
                pillLabel={aiConnectivity.pillLabel}
                onToggle={() => {
                  aiConnectivity.resetFallbackToasts();
                  toggleAiPreference().then(() => {
                    const pref = useAiModeStore.getState().preference;
                    showToast(
                      pref === 'cloud'
                        ? '🤖 LLM — Groq when available'
                        : '📋 Rules only — no Groq calls',
                    );
                    aiConnectivity.refresh();
                  });
                }}
              />
            )}
          </View>
        </View>

        {/* Actions */}
        <View style={styles.headerActions}>
          {/* 🌐 Language selector (Sarvam — online only) */}
          <LanguageTrigger
            isOnline={canTranslate}
            onLanguageChange={handleLanguageChange}
          />

          {/* Location button */}
          <TouchableOpacity
            style={[styles.headerBtn, hasState && styles.headerBtnActive]}
            onPress={() => { setLocModalTab('gps'); setShowLocModal(true); }}
          >
            <Ionicons
              name={hasState ? 'location' : 'location-outline'}
              size={18}
              color={hasState ? Colors.bluePale : Colors.gray}
            />
          </TouchableOpacity>

          {/* Mode toggle */}
          <TouchableOpacity
            style={[styles.headerBtn, styles.headerBtnMode]}
            onPress={handleModeToggle}
          >
            {currentMode === 'static' ? (
              <Ionicons name="sparkles" size={16} color={Colors.bluePale} />
            ) : (
              <MaterialCommunityIcons name="calculator-variant" size={17} color={Colors.bluePale} />
            )}
          </TouchableOpacity>
        </View>
      </View>

      {/* ── Progress bar (static mode) ───────────────────────────────── */}
      {currentMode === 'static' && <ProgressBar sessionState={sessionState} />}

      {/* ── Message list ─────────────────────────────────────────────── */}
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={(m) => m.id}
        renderItem={({ item }) => (
          <ChatBubble
            message={item}
            onChipPress={handleChipPress}
            onMultiSubmit={handleMultiSubmit}
            onMcqProceed={() => scrollToBottom(false)}
            isOnline={isOnline}
            sarvamAvailable={sarvamAvailable}
            onTtsError={(msg) => showToast(msg)}
          />
        )}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
        onContentSizeChange={() => scrollToBottom()}
        onScrollToIndexFailed={() => scrollToBottom()}
      />

      {/* ── Quick action: Set Location (if not set yet) ────────────── */}
      {showLocBtn && (
        <TouchableOpacity
          style={styles.locBanner}
          onPress={() => { setLocModalTab('gps'); setShowLocModal(true); }}
        >
          <LinearGradient colors={['rgba(26,79,168,0.3)', 'rgba(15,32,64,0.3)']} style={StyleSheet.absoluteFill} />
          <Ionicons name="location" size={15} color={Colors.bluePale} />
          <Text style={styles.locBannerText}>Tap to set your location to get started</Text>
        </TouchableOpacity>
      )}

      {/* ── Input area ───────────────────────────────────────────────── */}
      <View style={styles.inputArea}>
        <LinearGradient
          colors={['rgba(10,22,40,0.98)', 'rgba(5,13,31,1)']}
          style={StyleSheet.absoluteFill}
        />
        {/* Top border glow */}
        <View style={styles.inputTopBorder} />

        {/* Calculator is chip-driven — the message bar only appears in AI Chat */}
        {currentMode === 'dynamic' && (
          <View style={styles.inputRow}>
            <TextInput
              style={[styles.input, !allowText && styles.inputDisabled]}
              placeholder={
                !allowText
                  ? 'Tap a chip above to continue…'
                  : currentMode === 'dynamic'
                  ? 'Ask anything about traffic law…'
                  : 'Type a message…'
              }
              placeholderTextColor={Colors.grayDark}
              value={inputText}
              onChangeText={setInputText}
              onSubmitEditing={() => handleSend()}
              // Web: Enter sends, Shift+Enter adds a new line (multiline inputs ignore onSubmitEditing there).
              onKeyPress={Platform.OS === 'web' ? (e: any) => {
                if (e.nativeEvent.key === 'Enter' && !e.nativeEvent.shiftKey) { e.preventDefault?.(); handleSend(); }
              } : undefined}
              returnKeyType="send"
              editable={allowText && !isSending}
              multiline
              maxLength={2000}
            />
            <TouchableOpacity
              style={[
                styles.sendBtn,
                (!inputText.trim() || isSending || !allowText) && styles.sendBtnDisabled,
              ]}
              onPress={() => handleSend()}
              disabled={!inputText.trim() || isSending || !allowText}
            >
              {isSending ? (
                <ActivityIndicator color={Colors.white} size="small" />
              ) : (
                <LinearGradient colors={Gradients.blueGloss} style={styles.sendBtnGrad}>
                  <Ionicons name="arrow-up" size={22} color={Colors.white} />
                </LinearGradient>
              )}
            </TouchableOpacity>
          </View>
        )}

        {hasState && (
          <View style={styles.locFooterRow}>
            <TouchableOpacity style={styles.revertBtn} onPress={handleOpenLocationPicker}>
              <Ionicons name="location-outline" size={13} color={Colors.gray} />
              <Text style={styles.revertText}>Change Location</Text>
            </TouchableOpacity>
            {sessionState?.has_prev_location && (
              <TouchableOpacity style={styles.revertBtn} onPress={handleUndoLocation}>
                <Ionicons name="arrow-undo" size={13} color={Colors.gray} />
                <Text style={styles.revertText}>Undo last change</Text>
              </TouchableOpacity>
            )}
          </View>
        )}
      </View>

      {/* ── Location modal ────────────────────────────────────────────── */}
      <LocationModal
        visible={showLocModal}
        initialTab={locModalTab}
        onClose={() => { setShowLocModal(false); setLocModalTab('gps'); }}
        onManual={handleLocationManual}
        onPin={handlePinDrop}
      />

      {/* ── Location toast notification ──────────────────────────────── */}
      {!!toastMsg && (
        <Animated.View
          style={[
            styles.toast,
            {
              opacity:   toastAnim,
              transform: [{
                translateY: toastAnim.interpolate({
                  inputRange:  [0, 1],
                  outputRange: [20, 0],
                }),
              }],
            },
          ]}
          pointerEvents="none"
        >
          <LinearGradient
            colors={['rgba(8,18,45,0.96)', 'rgba(4,10,24,0.98)']}
            style={StyleSheet.absoluteFill}
          />
          <View style={styles.toastBorder} />
          <View style={styles.toastRow}>
            <Ionicons name="location" size={14} color={Colors.bluePale} />
            <Text style={styles.toastText}>{toastMsg}</Text>
          </View>
        </Animated.View>
      )}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.black },

  // Header
  header: {
    flexDirection:   'row',
    alignItems:      'center',
    paddingTop:      56,
    paddingBottom:   Spacing.md,
    paddingHorizontal: Spacing.md,
    gap:             Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.navyBorder,
  },
  backBtn: {
    width:          40,
    height:         40,
    borderRadius:   Radius.md,
    backgroundColor: Colors.navyMid,
    alignItems:     'center',
    justifyContent: 'center',
  },
  backIcon:   { fontSize: 22, color: Colors.white },
  headerCenter: { flex: 1 },
  headerTitle:  { fontSize: Typography.base, fontWeight: Typography.bold, color: Colors.white },
  headerSubRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 1 },
  headerSub:    { fontSize: Typography.xs, color: Colors.gray },
  headerActions: { flexDirection: 'row', gap: Spacing.sm },
  headerBtn: {
    width:          38,
    height:         38,
    borderRadius:   Radius.md,
    backgroundColor: Colors.navyMid,
    borderWidth:    1,
    borderColor:    Colors.navyBorder,
    alignItems:     'center',
    justifyContent: 'center',
  },
  headerBtnActive: { borderColor: Colors.blueVibrant },
  headerBtnMode:   {},
  headerBtnIcon:   { fontSize: 16 },

  // Messages
  listContent: { paddingVertical: Spacing.md, paddingBottom: Spacing.xl },

  // Location banner
  locBanner: {
    marginHorizontal: Spacing.base,
    marginBottom:     Spacing.sm,
    borderRadius:     Radius.lg,
    borderWidth:      1,
    borderColor:      Colors.blue + '50',
    overflow:         'hidden',
    padding:          Spacing.md,
    flexDirection:    'row',
    alignItems:       'center',
    justifyContent:   'center',
    gap:              Spacing.xs,
  },
  locBannerText: {
    color:     Colors.bluePale,
    fontSize:  Typography.sm,
    fontWeight: Typography.medium,
  },

  // Input
  inputArea: {
    paddingHorizontal: Spacing.md,
    paddingVertical:   Spacing.sm,
    paddingBottom:     Platform.OS === 'ios' ? 30 : Spacing.md,
    overflow:          'hidden',
  },
  inputTopBorder: {
    position:        'absolute',
    top:             0,
    left:            0,
    right:           0,
    height:          1,
    backgroundColor: Colors.navyBorder,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems:    'flex-end',
    gap:           Spacing.sm,
  },
  input: {
    flex:            1,
    backgroundColor: Colors.navyMid,
    borderRadius:    Radius.lg,
    borderWidth:     1,
    borderColor:     Colors.navyBorder,
    paddingHorizontal: Spacing.md,
    paddingTop:      12,
    paddingBottom:   12,
    color:           Colors.white,
    fontSize:        Typography.base,
    maxHeight:       120,
    lineHeight:      Typography.base * 1.5,
  },
  inputDisabled: { opacity: 0.5 },
  sendBtn: {
    width:         46,
    height:        46,
    borderRadius:  Radius.lg,
    overflow:      'hidden',
    alignItems:    'center',
    justifyContent: 'center',
    backgroundColor: Colors.navyMid,
  },
  sendBtnDisabled: { opacity: 0.4 },
  sendBtnGrad: {
    width:          '100%',
    height:         '100%',
    alignItems:     'center',
    justifyContent: 'center',
  },
  sendIcon: { fontSize: 22, color: Colors.white, fontWeight: Typography.bold },

  locFooterRow: {
    flexDirection:  'row',
    justifyContent: 'center',
    flexWrap:       'wrap',
    gap:            Spacing.md,
    marginTop:      Spacing.sm,
  },
  revertBtn: {
    paddingVertical: 4,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
  },
  revertText: {
    fontSize: Typography.xs,
    color:    Colors.gray,
  },

  // ── Location toast ──────────────────────────────────────────────────────────
  toast: {
    position:        'absolute',
    bottom:          120,
    alignSelf:       'center',
    borderRadius:    Radius.full,
    overflow:        'hidden',
    paddingVertical:   10,
    paddingHorizontal: 20,
    borderWidth:     1,
    borderColor:     'rgba(37,99,235,0.5)',
    shadowColor:     Colors.blueVibrant,
    shadowOffset:    { width: 0, height: 0 },
    shadowOpacity:   0.35,
    shadowRadius:    12,
    elevation:       8,
  },
  toastBorder: {
    position:        'absolute',
    top:             0,
    left:            '15%' as any,
    right:           '15%' as any,
    height:          1,
    backgroundColor: Colors.blueVibrant,
    opacity:         0.6,
  },
  toastRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.xs },
  toastText: {
    fontSize:   Typography.sm,
    fontWeight: Typography.semibold,
    color:      Colors.white,
    letterSpacing: 0.2,
  },
});
