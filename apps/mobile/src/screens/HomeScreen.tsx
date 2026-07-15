/**
 * HomeScreen — Session list + New Chat entry point
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  Alert,
  Animated,
  StatusBar,
  Dimensions,
  Linking,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';

import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { Colors, Gradients, Typography, Spacing, Radius, Shadows, Animation } from '../theme';
import { sessionsApi, Session } from '../services/api';
import { useChatStore } from '../store/chatStore';
import { useAuthStore } from '../store/authStore';
import { SessionCard } from '../components/SessionCard';
import { Logo } from '../components/Brand';
import { UpdateBanner } from '../components/UpdateBanner';
import { shouldShowBanner } from '../services/updateService';

const { width: W } = Dimensions.get('window');

// Mode 3 — On-device AI lives in the PWA (WebLLM needs the browser's WebGPU).
const SLM_URL = 'https://drivelegal-ai-r72.netlify.app';

// ── Mode selector ─────────────────────────────────────────────────────────────

interface ModeSelectorProps {
  selected: 'static' | 'dynamic';
  onChange: (m: 'static' | 'dynamic') => void;
}

function ModeSelector({ selected, onChange }: ModeSelectorProps) {
  const slideX = useRef(new Animated.Value(selected === 'static' ? 0 : 1)).current;

  const select = (m: 'static' | 'dynamic') => {
    Animated.spring(slideX, {
      toValue: m === 'static' ? 0 : 1,
      tension: 100,
      friction: 12,
      useNativeDriver: false,
    }).start();
    onChange(m);
  };

  // Pill slides only between the two real session modes (3 equal segments).
  const left = slideX.interpolate({
    inputRange: [0, 1],
    outputRange: ['1.3%', '34.3%'],
  });

  return (
    <View style={ms.wrap}>
      {/* Sliding pill */}
      <Animated.View style={[ms.pill, { left }]}>
        <LinearGradient colors={Gradients.blueGlow} style={StyleSheet.absoluteFillObject} />
      </Animated.View>
      {/* Labels */}
      <TouchableOpacity style={ms.tab} onPress={() => select('static')}>
        <MaterialCommunityIcons
          name="calculator-variant"
          size={16}
          color={selected === 'static' ? Colors.white : Colors.gray}
        />
        <Text style={[ms.tabText, selected === 'static' && ms.tabTextActive]}>Calculator</Text>
      </TouchableOpacity>
      <TouchableOpacity style={ms.tab} onPress={() => select('dynamic')}>
        <Ionicons
          name="sparkles"
          size={15}
          color={selected === 'dynamic' ? Colors.white : Colors.gray}
        />
        <Text style={[ms.tabText, selected === 'dynamic' && ms.tabTextActive]}>AI Chat</Text>
      </TouchableOpacity>
      {/* Mode 3 — On-device AI (opens the offline WebLLM PWA in the browser) */}
      <TouchableOpacity style={ms.tab} onPress={() => Linking.openURL(SLM_URL)}>
        <Ionicons name="hardware-chip-outline" size={15} color={Colors.accent} />
        <Text style={[ms.tabText, { color: Colors.accent }]}>On-device</Text>
      </TouchableOpacity>
    </View>
  );
}

const ms = StyleSheet.create({
  wrap: {
    flexDirection:   'row',
    backgroundColor: Colors.navyMid,
    borderRadius:    Radius.lg,
    borderWidth:     1,
    borderColor:     Colors.navyBorder,
    marginHorizontal: Spacing.base,
    marginBottom:    Spacing.base,
    padding:         3,
    position:        'relative',
  },
  pill: {
    position:     'absolute',
    top:          3,
    bottom:       3,
    width:        '31.7%',
    borderRadius: Radius.md,
    overflow:     'hidden',
  },
  tab: {
    flex:            1,
    flexDirection:   'row',
    gap:             5,
    paddingVertical: 10,
    alignItems:      'center',
    justifyContent:  'center',
  },
  tabText: {
    fontSize:   Typography.sm,
    fontWeight: Typography.semibold,
    color:      Colors.gray,
  },
  tabTextActive: {
    color: Colors.white,
  },
});

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ onNew }: { onNew: () => void }) {
  const fadeAnim = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(fadeAnim, { toValue: 1, duration: 600, useNativeDriver: true }).start();
  }, []);
  return (
    <Animated.View style={[es.wrap, { opacity: fadeAnim }]}>
      <Logo size={84} />
      <Text style={es.title}>No chats yet</Text>
      <Text style={es.sub}>Start a new session to calculate fines{'\n'}or chat with the AI assistant</Text>
      <TouchableOpacity style={es.btn} onPress={onNew} activeOpacity={0.8}>
        <LinearGradient colors={Gradients.blueGlow} style={es.btnGrad}>
          <Text style={es.btnText}>+ New Chat</Text>
        </LinearGradient>
      </TouchableOpacity>
    </Animated.View>
  );
}

const es = StyleSheet.create({
  wrap:     { alignItems: 'center', justifyContent: 'center', flex: 1, padding: Spacing.xxxl, gap: Spacing.md },
  title:    { fontSize: Typography.xl, fontWeight: Typography.bold, color: Colors.white, marginBottom: Spacing.sm },
  sub:      { fontSize: Typography.base, color: Colors.gray, textAlign: 'center', lineHeight: 22 },
  btn:      { marginTop: Spacing.xl, borderRadius: Radius.lg, overflow: 'hidden' },
  btnGrad:  { paddingHorizontal: Spacing.xl, paddingVertical: 14 },
  btnText:  { color: Colors.white, fontWeight: Typography.bold, fontSize: Typography.md },
});

// ── Main ─────────────────────────────────────────────────────────────────────

export default function HomeScreen() {
  const router = useRouter();
  const { user, clearAuth, ensureDeviceAuth } = useAuthStore();
  const { sessions, setSessions, setActiveSession, removeSession, setLoadingSessions, isLoadingSessions } = useChatStore();

  const [mode, setMode] = useState<'static' | 'dynamic'>('static');
  const [refreshing, setRefreshing] = useState(false);
  const [showBanner, setShowBanner] = useState(false);
  const [manualBanner, setManualBanner] = useState(false);
  const fabScale = useRef(new Animated.Value(1)).current;

  const loadSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      const list = await sessionsApi.list();
      setSessions(list);
    } catch { /* offline — keep existing list */ }
    setLoadingSessions(false);
  }, []);

  useEffect(() => {
    loadSessions();
    // Check if we should auto-show the update banner (≥7 days since last run)
    shouldShowBanner().then(setShowBanner);
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    await loadSessions();
    setRefreshing(false);
  };

  const handleNewChat = async () => {
    // FAB bounce
    Animated.sequence([
      Animated.timing(fabScale, { toValue: 0.88, duration: 100, useNativeDriver: true }),
      Animated.spring(fabScale,  { toValue: 1.0,  tension: 200, friction: 5, useNativeDriver: true }),
    ]).start();

    try {
      const resp = await sessionsApi.create(mode);
      const newSession: Session = {
        id:           resp.session_id,
        title:        'New chat',
        mode,
        created_at:   Date.now(),
        updated_at:   Date.now(),
        last_snippet: '',
      };
      setSessions([newSession, ...sessions]);
      setActiveSession(resp.session_id);
      router.push({ pathname: '/(app)/chat', params: { sessionId: resp.session_id, mode } });
    } catch (err: any) {
      Alert.alert('Error', 'Could not create session. Is the backend running?');
    }
  };

  const handleOpenSession = (session: Session) => {
    setActiveSession(session.id);
    router.push({ pathname: '/(app)/chat', params: { sessionId: session.id, mode: session.mode } });
  };

  const handleDeleteSession = (session: Session) => {
    Alert.alert(
      'Delete Chat',
      `Delete "${session.title || 'this chat'}"?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            removeSession(session.id);
            try { await sessionsApi.delete(session.id); } catch { /* offline */ }
          },
        },
      ]
    );
  };

  const handleRefreshSession = () => {
    Alert.alert('Refresh session', 'Get a new access token for this device?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Refresh',
        onPress: async () => {
          await clearAuth();
          await ensureDeviceAuth();
          await loadSessions();
        },
      },
    ]);
  };

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
      <LinearGradient colors={Gradients.navyDeep} style={StyleSheet.absoluteFillObject} />

      {/* ── Header ─────────────────────────────────────────────────── */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Logo size={40} ring glow={false} />
          <View style={{ flexShrink: 1 }}>
            <Text style={styles.headerTitle}>DriveLegal</Text>
            {user && (
              <Text style={styles.headerSub} numberOfLines={1}>
                {user.name || user.email}
              </Text>
            )}
          </View>
        </View>
        <View style={styles.headerRight}>
          {/* Health dot */}
          <HealthDot />
          {/* Update button */}
          <TouchableOpacity
            style={styles.updateBtn}
            onPress={() => setManualBanner(true)}
            activeOpacity={0.8}
          >
            <LinearGradient colors={['#1a3a5c', '#1f4878']} style={styles.updateBtnGrad}>
              <Ionicons name="cloud-download-outline" size={14} color={Colors.blueVibrant} />
              <Text style={styles.updateBtnText}>Update</Text>
            </LinearGradient>
          </TouchableOpacity>
          {/* Profile / logout */}
          <TouchableOpacity style={styles.avatarBtn} onPress={handleRefreshSession} activeOpacity={0.8}>
            <LinearGradient colors={Gradients.blueGloss} style={styles.avatarGrad}>
              {user ? (
                <Text style={styles.avatarInitial}>
                  {(user.name?.[0] || user.email?.[0] || '?').toUpperCase()}
                </Text>
              ) : (
                <Ionicons name="person" size={18} color={Colors.white} />
              )}
            </LinearGradient>
          </TouchableOpacity>
        </View>
      </View>

      {/* ── Mode selector ──────────────────────────────────────────── */}
      <ModeSelector selected={mode} onChange={setMode} />

      {/* ── Update banner (auto or manual) ─────────────────────────── */}
      {(showBanner || manualBanner) && (
        <UpdateBanner
          onDismiss={() => { setShowBanner(false); setManualBanner(false); }}
          onUpdated={() => setShowBanner(false)}
        />
      )}

      {/* ── Session list ───────────────────────────────────────────── */}
      <FlatList
        data={sessions}
        keyExtractor={(s) => s.id}
        renderItem={({ item }) => (
          <SessionCard
            session={item}
            isActive={false}
            onPress={() => handleOpenSession(item)}
            onDelete={() => handleDeleteSession(item)}
          />
        )}
        contentContainerStyle={sessions.length === 0 ? styles.listEmpty : styles.listContent}
        ListEmptyComponent={<EmptyState onNew={handleNewChat} />}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            tintColor={Colors.blueVibrant}
            colors={[Colors.blueVibrant]}
          />
        }
        showsVerticalScrollIndicator={false}
      />

      {/* ── FAB ────────────────────────────────────────────────────── */}
      <Animated.View style={[styles.fab, { transform: [{ scale: fabScale }] }]}>
        <TouchableOpacity onPress={handleNewChat} activeOpacity={0.85} style={styles.fabTouch}>
          <LinearGradient colors={Gradients.blueGloss} style={styles.fabGrad}>
            <Ionicons name="add" size={32} color={Colors.white} />
          </LinearGradient>
        </TouchableOpacity>
      </Animated.View>
    </View>
  );
}

// ── Health indicator ──────────────────────────────────────────────────────────

function HealthDot() {
  const [ready, setReady] = useState<boolean | null>(null);
  const pulse = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    import('../services/api').then(({ healthApi }) => {
      healthApi.check().then((h) => setReady(h.ready));
    });
    // Pulse animation
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1.5, duration: 800, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 1.0, duration: 800, useNativeDriver: true }),
      ])
    ).start();
  }, []);

  const color = ready === null ? Colors.gray : ready ? Colors.success : Colors.warning;

  return (
    <View style={{ width: 28, height: 28, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View
        style={{
          width: 8, height: 8, borderRadius: 4,
          backgroundColor: color,
          transform: [{ scale: pulse }],
          opacity: 0.5,
          position: 'absolute',
        }}
      />
      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color }} />
    </View>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root:  { flex: 1, backgroundColor: Colors.black },

  header: {
    flexDirection:   'row',
    alignItems:      'center',
    justifyContent:  'space-between',
    paddingTop:      56,
    paddingBottom:   Spacing.lg,
    paddingHorizontal: Spacing.base,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           Spacing.sm,
    flexShrink:    1,
  },
  headerTitle: {
    fontSize:   Typography.xl,
    fontWeight: Typography.extrabold,
    color:      Colors.white,
  },
  headerSub: {
    fontSize:  Typography.sm,
    color:     Colors.gray,
    marginTop: 1,
    maxWidth:  180,
  },
  headerRight: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           Spacing.sm,
  },
  avatarBtn:    { borderRadius: 20, overflow: 'hidden', ...Shadows.glow },
  avatarGrad:   { width: 38, height: 38, alignItems: 'center', justifyContent: 'center' },
  avatarInitial: { color: Colors.white, fontWeight: Typography.bold, fontSize: Typography.md },

  updateBtn: {
    borderRadius: Radius.md,
    overflow:     'hidden',
    borderWidth:  1,
    borderColor:  'rgba(41, 121, 255, 0.35)',
  },
  updateBtnGrad: {
    flexDirection:   'row',
    alignItems:      'center',
    gap:             4,
    paddingVertical:  6,
    paddingHorizontal: 10,
  },
  updateBtnText: {
    fontSize:   Typography.xs,
    fontWeight: Typography.semibold,
    color:      Colors.blueVibrant,
  },

  listContent: { paddingBottom: 100 },
  listEmpty:   { flex: 1 },

  fab: {
    position:  'absolute',
    bottom:    Spacing.xxxl,
    right:     Spacing.xl,
    ...Shadows.glow,
  },
  fabTouch:  { borderRadius: 32, overflow: 'hidden' },
  fabGrad:   {
    width:          64,
    height:         64,
    borderRadius:   32,
    alignItems:     'center',
    justifyContent: 'center',
  },
  fabIcon:   { fontSize: 32, color: Colors.white, lineHeight: 36 },
});
