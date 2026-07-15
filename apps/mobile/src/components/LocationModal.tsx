/**
 * LocationModal — 3-tab location picker
 * ──────────────────────────────────────────────────────────────
 *  GPS     — one-tap detect + confirm
 *  Map     — tap anywhere on India map to drop/drag a pin
 *  Browse  — scrollable state -> city list
 *
 * Pass `onPin(lat, lng, label)` or `onManual(stateCode, cityCode, text)`.
 * The `label` string is shown in the toast notification in ChatScreen.
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Modal,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
  Dimensions,
  Platform,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import MapView, { Marker, Region, PROVIDER_DEFAULT } from 'react-native-maps';
import * as Location from 'expo-location';

import { Colors, Gradients, Typography, Spacing, Radius } from '../theme';
import { catalogsApi } from '../services/api';

const { width: W, height: H } = Dimensions.get('window');

// ── India default region ──────────────────────────────────────────────────────
const INDIA_REGION: Region = {
  latitude:      20.5937,
  longitude:     78.9629,
  latitudeDelta:  26,
  longitudeDelta: 22,
};

type TabType = 'gps' | 'map' | 'browse';
type IconLib = 'ion' | 'mci';

const TABS: { id: TabType; lib: IconLib; icon: string; label: string }[] = [
  { id: 'gps',    lib: 'mci', icon: 'crosshairs-gps', label: 'GPS'    },
  { id: 'map',    lib: 'ion', icon: 'map',            label: 'Map'    },
  { id: 'browse', lib: 'ion', icon: 'business',       label: 'Browse' },
];

function TabIcon({ lib, name, color, size = 15 }: { lib: IconLib; name: string; color: string; size?: number }) {
  return lib === 'ion'
    ? <Ionicons name={name as any} size={size} color={color} />
    : <MaterialCommunityIcons name={name as any} size={size} color={color} />;
}

// ── Props ─────────────────────────────────────────────────────────────────────
export interface LocationModalProps {
  visible:    boolean;
  onClose:    () => void;
  /** Which tab to open when the modal becomes visible (default: gps). */
  initialTab?: TabType;
  /** Called when user picks via state/city list or free text */
  onManual:  (stateCode?: string, cityCode?: string, text?: string) => void;
  /** Called when user picks via GPS or map pin. `label` is human-readable. */
  onPin:     (lat: number, lng: number, label?: string) => void;
}

// ── Component ─────────────────────────────────────────────────────────────────
export function LocationModal({
  visible,
  onClose,
  initialTab = 'gps',
  onManual,
  onPin,
}: LocationModalProps) {
  const [activeTab, setActiveTab] = useState<TabType>(initialTab);

  useEffect(() => {
    if (visible) setActiveTab(initialTab);
  }, [visible, initialTab]);

  // ── GPS state ───────────────────────────────────────────────────────────────
  const [locating,         setLocating]         = useState(false);
  const [detectedCoord,    setDetectedCoord]    = useState<{ lat: number; lng: number } | null>(null);
  const [detectedLabel,    setDetectedLabel]    = useState('');

  // ── Map state ───────────────────────────────────────────────────────────────
  const [pinCoord,    setPinCoord]    = useState<{ latitude: number; longitude: number } | null>(null);
  const [pinLabel,    setPinLabel]    = useState('');
  const [geocoding,   setGeocoding]   = useState(false);
  const [mapExpanded, setMapExpanded] = useState(true);

  // ── Browse state ────────────────────────────────────────────────────────────
  const [states,   setStates]   = useState<{ code: string; name: string }[]>([]);
  const [cities,   setCities]   = useState<{ code: string; name: string }[]>([]);
  const [selState, setSelState] = useState<string | null>(null);
  const [selStateName, setSelStateName] = useState('');

  // ── Load catalogs when modal opens ──────────────────────────────────────────
  useEffect(() => {
    if (visible) {
      catalogsApi.states().then(setStates).catch(() => setStates([]));
    } else {
      // Reset on close
      setDetectedCoord(null);
      setDetectedLabel('');
      setPinCoord(null);
      setPinLabel('');
      setSelState(null);
      setCities([]);
      setMapExpanded(true);
    }
  }, [visible]);

  // ── GPS helpers ──────────────────────────────────────────────────────────────
  const detectGPS = async () => {
    setLocating(true);
    setDetectedCoord(null);
    setDetectedLabel('');
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        Alert.alert('Permission denied', 'Location permission is needed to auto-detect your position.');
        return;
      }
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      const { latitude, longitude } = loc.coords;

      // Reverse geocode
      const geo = await Location.reverseGeocodeAsync({ latitude, longitude }).catch(() => []);
      const g = geo[0];
      const label = [g?.city || g?.district, g?.region].filter(Boolean).join(', ')
        || `${latitude.toFixed(3)}°N, ${longitude.toFixed(3)}°E`;

      setDetectedCoord({ lat: latitude, lng: longitude });
      setDetectedLabel(label);
    } catch {
      Alert.alert('Error', 'Could not get your location. Make sure GPS is enabled.');
    } finally {
      setLocating(false);
    }
  };

  const confirmGPS = () => {
    if (!detectedCoord) return;
    onPin(detectedCoord.lat, detectedCoord.lng, detectedLabel);
    onClose();
  };

  // ── Map helpers ───────────────────────────────────────────────────────────────
  const handleMapPress = async (e: any) => {
    const { latitude, longitude } = e.nativeEvent.coordinate;
    setPinCoord({ latitude, longitude });
    setPinLabel('');
    setGeocoding(true);
    try {
      const geo = await Location.reverseGeocodeAsync({ latitude, longitude }).catch(() => []);
      const g = geo[0];
      const label = [g?.city || g?.district, g?.region].filter(Boolean).join(', ')
        || `${latitude.toFixed(3)}°N, ${longitude.toFixed(3)}°E`;
      setPinLabel(label);
    } catch {
      setPinLabel(`${latitude.toFixed(3)}°N, ${longitude.toFixed(3)}°E`);
    } finally {
      setGeocoding(false);
    }
  };

  const confirmMapPin = () => {
    if (!pinCoord) return;
    onPin(pinCoord.latitude, pinCoord.longitude, pinLabel);
    onClose();
  };

  // ── Browse helpers ────────────────────────────────────────────────────────────
  const pickState = async (code: string, name: string) => {
    setSelState(code);
    setSelStateName(name);
    setCities([]);
    const c = await catalogsApi.cities(code).catch(() => []);
    setCities(c);
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={s.overlay}>
        <View style={s.sheet}>
          <LinearGradient
            colors={['rgba(8,18,45,0.99)', 'rgba(4,10,24,1)']}
            style={StyleSheet.absoluteFillObject}
          />

          {/* Drag handle */}
          <View style={s.handle} />

          {/* Header */}
          <View style={s.header}>
            <View style={s.titleRow}>
              <Ionicons name="location" size={18} color={Colors.bluePale} />
              <Text style={s.title}>Set Location</Text>
            </View>
            <TouchableOpacity onPress={onClose} style={s.closeBtn}>
              <Ionicons name="close" size={16} color={Colors.gray} />
            </TouchableOpacity>
          </View>

          {/* Tab pills */}
          <View style={s.tabBar}>
            {TABS.map((tab) => {
              const isActive = activeTab === tab.id;
              return (
                <TouchableOpacity
                  key={tab.id}
                  style={[
                    s.tabPill,
                    isActive && s.tabPillActive,
                    tab.id === 'map' && s.tabPillMap,
                  ]}
                  onPress={() => {
                    setActiveTab(tab.id);
                    // Lazily (re)load states the moment Browse is opened, in
                    // case the initial fetch failed or hasn't returned yet.
                    if (tab.id === 'browse' && states.length === 0) {
                      catalogsApi.states().then(setStates).catch(() => {});
                    }
                  }}
                  activeOpacity={0.75}
                >
                  {isActive && (
                    <LinearGradient
                      colors={Gradients.blueGloss}
                      style={StyleSheet.absoluteFillObject}
                      start={{ x: 0, y: 0 }}
                      end={{ x: 1, y: 0 }}
                    />
                  )}
                  <View style={s.tabPillInnerRow}>
                    <TabIcon
                      lib={tab.lib}
                      name={tab.icon}
                      color={isActive ? Colors.white : Colors.gray}
                    />
                    <Text style={[s.tabLabel, isActive && s.tabLabelActive]}>
                      {tab.label}
                    </Text>
                  </View>
                  {tab.id === 'map' && (
                    <Text style={[s.tabNetHint, isActive && s.tabNetHintActive]}>
                      Needs internet
                    </Text>
                  )}
                </TouchableOpacity>
              );
            })}
          </View>

          {/* ── GPS Tab ─────────────────────────────────────────────────────── */}
          {activeTab === 'gps' && (
            <View style={s.tabContent}>
              {!detectedCoord ? (
                /* Pre-detect state */
                <>
                  <View style={s.gpsHero}>
                    <View style={s.gpsOrb}>
                      <LinearGradient
                        colors={['rgba(37,99,235,0.25)', 'rgba(37,99,235,0.05)']}
                        style={StyleSheet.absoluteFillObject}
                      />
                      <MaterialCommunityIcons name="crosshairs-gps" size={34} color={Colors.bluePale} />
                    </View>
                    <Text style={s.gpsHeroTitle}>Detect My Location</Text>
                    <Text style={s.gpsHeroSub}>
                      Uses your phone's GPS to instantly find your current state and city.
                      Works offline once location is granted.
                    </Text>
                  </View>
                  <TouchableOpacity
                    style={s.primaryBtn}
                    onPress={detectGPS}
                    disabled={locating}
                    activeOpacity={0.85}
                  >
                    <LinearGradient colors={Gradients.blueGlow} style={s.primaryBtnGrad}>
                      {locating ? (
                        <ActivityIndicator color={Colors.white} size="small" />
                      ) : (
                        <Text style={s.primaryBtnText}>Detect My Location</Text>
                      )}
                    </LinearGradient>
                  </TouchableOpacity>
                </>
              ) : (
                /* Post-detect state */
                <>
                  <View style={s.detectedCard}>
                    <View style={s.detectedCardGlow} />
                    <View style={s.detectedPinDot}>
                      <LinearGradient colors={Gradients.blueGloss} style={StyleSheet.absoluteFillObject} />
                      <Ionicons name="location" size={16} color={Colors.white} />
                    </View>
                    <View style={s.detectedInfo}>
                      <Text style={s.detectedLabel}>{detectedLabel}</Text>
                      <Text style={s.detectedCoords}>
                        {detectedCoord.lat.toFixed(4)}°N, {detectedCoord.lng.toFixed(4)}°E
                      </Text>
                    </View>
                    <TouchableOpacity
                      style={s.retryBtn}
                      onPress={() => setDetectedCoord(null)}
                    >
                      <Ionicons name="refresh" size={16} color={Colors.gray} />
                    </TouchableOpacity>
                  </View>

                  <TouchableOpacity
                    style={s.primaryBtn}
                    onPress={confirmGPS}
                    activeOpacity={0.85}
                  >
                    <LinearGradient colors={Gradients.blueGloss} style={[s.primaryBtnGrad, s.primaryBtnRow]}>
                      <Ionicons name="checkmark-circle" size={18} color={Colors.white} />
                      <Text style={s.primaryBtnText}>Use This Location</Text>
                    </LinearGradient>
                  </TouchableOpacity>

                  <TouchableOpacity style={s.secondaryBtn} onPress={detectGPS} disabled={locating}>
                    <Text style={s.secondaryBtnText}>Try Again</Text>
                  </TouchableOpacity>
                </>
              )}
            </View>
          )}

          {/* ── Map Tab ─────────────────────────────────────────────────────── */}
          {activeTab === 'map' && (
            <View style={s.tabContent}>
              {/* Map expand/collapse toggle */}
              <TouchableOpacity
                style={s.mapToggleRow}
                onPress={() => setMapExpanded(!mapExpanded)}
                activeOpacity={0.75}
              >
                <View style={s.mapToggleLabelRow}>
                  <Ionicons
                    name={mapExpanded ? 'chevron-up' : 'chevron-down'}
                    size={14}
                    color={Colors.bluePale}
                  />
                  <Text style={s.mapToggleLabel}>{mapExpanded ? 'Hide map' : 'Show map'}</Text>
                </View>
                <Text style={s.mapToggleHint}>Tap anywhere in India to drop a pin</Text>
              </TouchableOpacity>
              <Text style={s.mapNetNote}>Map tiles need an internet connection</Text>

              {/* Map */}
              {mapExpanded && (
                <View style={s.mapWrapper}>
                  <MapView
                    style={s.map}
                    initialRegion={INDIA_REGION}
                    onPress={handleMapPress}
                    showsUserLocation
                    showsCompass
                    showsScale
                    provider={PROVIDER_DEFAULT}
                    mapType="standard"
                  >
                    {pinCoord && (
                      <Marker
                        coordinate={pinCoord}
                        draggable
                        onDragEnd={handleMapPress}
                        title={pinLabel || 'Selected location'}
                        pinColor={Colors.blueVibrant}
                      />
                    )}
                  </MapView>

                  {/* "Tap the map" overlay — shown until first pin */}
                  {!pinCoord && (
                    <View style={s.mapHintOverlay} pointerEvents="none">
                      <View style={s.mapHintBadge}>
                        <Ionicons name="hand-left" size={12} color={Colors.bluePale} />
                        <Text style={s.mapHintText}>Tap anywhere to drop a pin</Text>
                      </View>
                    </View>
                  )}
                </View>
              )}

              {/* Pin location card */}
              {pinCoord ? (
                <View style={s.pinCard}>
                  {geocoding ? (
                    <ActivityIndicator color={Colors.blue} size="small" style={{ marginVertical: 8 }} />
                  ) : (
                    <View style={s.pinCardInner}>
                      <Ionicons name="pin" size={20} color={Colors.bluePale} />
                      <View style={{ flex: 1 }}>
                        <Text style={s.pinCardLabel}>{pinLabel || 'Location selected'}</Text>
                        <Text style={s.pinCardCoords}>
                          {pinCoord.latitude.toFixed(4)}°N, {pinCoord.longitude.toFixed(4)}°E
                        </Text>
                      </View>
                      <TouchableOpacity
                        style={s.clearPinBtn}
                        onPress={() => { setPinCoord(null); setPinLabel(''); }}
                      >
                        <Ionicons name="close" size={13} color={Colors.gray} />
                      </TouchableOpacity>
                    </View>
                  )}
                </View>
              ) : (
                <View style={s.pinPlaceholder}>
                  <Text style={s.pinPlaceholderText}>No pin placed yet</Text>
                </View>
              )}

              {/* Confirm button */}
              <TouchableOpacity
                style={[s.primaryBtn, !pinCoord && s.primaryBtnDisabled]}
                onPress={confirmMapPin}
                disabled={!pinCoord || geocoding}
                activeOpacity={0.85}
              >
                <LinearGradient
                  colors={pinCoord && !geocoding ? Gradients.blueGlow : ['#1a3a6b', '#1a3a6b']}
                  style={s.primaryBtnGrad}
                >
                  <Text style={[s.primaryBtnText, (!pinCoord || geocoding) && { opacity: 0.5 }]}>
                    {geocoding ? 'Getting location name…' : 'Confirm Location'}
                  </Text>
                </LinearGradient>
              </TouchableOpacity>
            </View>
          )}

          {/* ── Browse Tab ──────────────────────────────────────────────────── */}
          {activeTab === 'browse' && (
            <View style={s.browseContent}>
              <Text style={s.browseHint}>
                {selState
                  ? `Showing cities in  ${selStateName}`
                  : 'Select a state — then pick your city'}
              </Text>

              {!selState ? (
                /* State list */
                states.length === 0 ? (
                  <View style={s.browseLoading}>
                    <ActivityIndicator color={Colors.blue} />
                    <Text style={s.browseLoadingText}>Loading states…</Text>
                  </View>
                ) : (
                  <ScrollView
                    style={s.list}
                    contentContainerStyle={s.listInner}
                    showsVerticalScrollIndicator
                    nestedScrollEnabled
                    keyboardShouldPersistTaps="handled"
                  >
                    {states.map((st) => (
                      <TouchableOpacity
                        key={st.code}
                        style={s.listItem}
                        onPress={() => pickState(st.code, st.name)}
                        activeOpacity={0.7}
                      >
                        <Text style={s.listItemText}>{st.name}</Text>
                        <Ionicons name="chevron-forward" size={18} color={Colors.grayDark} />
                      </TouchableOpacity>
                    ))}
                  </ScrollView>
                )
              ) : (
                /* City list */
                <ScrollView
                  style={s.list}
                  contentContainerStyle={s.listInner}
                  showsVerticalScrollIndicator
                  nestedScrollEnabled
                  keyboardShouldPersistTaps="handled"
                >
                  {/* Back row */}
                  <TouchableOpacity
                    style={[s.listItem, s.listItemBack]}
                    onPress={() => { setSelState(null); setCities([]); }}
                  >
                    <Ionicons name="chevron-back" size={16} color={Colors.bluePale} />
                    <Text style={s.listItemBackText}>Back to states</Text>
                  </TouchableOpacity>

                  {/* State-only option */}
                  <TouchableOpacity
                    style={s.listItem}
                    onPress={() => { onManual(selState, undefined, undefined); onClose(); }}
                  >
                    <Text style={[s.listItemText, { color: Colors.bluePale }]}>
                      {selStateName} (state only)
                    </Text>
                    <Ionicons name="checkmark" size={18} color={Colors.bluePale} />
                  </TouchableOpacity>

                  {cities.length === 0 && (
                    <ActivityIndicator color={Colors.blue} style={{ marginTop: 20 }} />
                  )}

                  {cities.map((c) => (
                    <TouchableOpacity
                      key={c.code}
                      style={s.listItem}
                      onPress={() => { onManual(selState, c.code, undefined); onClose(); }}
                      activeOpacity={0.7}
                    >
                      <Text style={s.listItemText}>{c.name}</Text>
                      <Ionicons name="chevron-forward" size={18} color={Colors.grayDark} />
                    </TouchableOpacity>
                  ))}
                </ScrollView>
              )}
            </View>
          )}
        </View>
      </View>
    </Modal>
  );
}

// ── Styles ───────────────────────────────────────────────────────────────────
const MAP_HEIGHT = Math.min(H * 0.30, 220);

const s = StyleSheet.create({
  overlay: {
    flex:            1,
    justifyContent:  'flex-end',
    backgroundColor: 'rgba(0,0,0,0.75)',
  },
  sheet: {
    borderTopLeftRadius:  Radius.xxl,
    borderTopRightRadius: Radius.xxl,
    overflow:             'hidden',
    paddingHorizontal:    Spacing.lg,
    paddingBottom:        Platform.OS === 'ios' ? 36 : Spacing.xl,
    maxHeight:            H * 0.9,
    borderWidth:          1,
    borderColor:          Colors.navyBorder,
    borderBottomWidth:    0,
  },

  handle: {
    alignSelf:       'center',
    width:           44,
    height:          4,
    borderRadius:    2,
    backgroundColor: Colors.navyBorder,
    marginTop:       Spacing.md,
    marginBottom:    Spacing.md,
  },

  // Header
  header: {
    flexDirection:  'row',
    alignItems:     'center',
    justifyContent: 'space-between',
    marginBottom:   Spacing.lg,
  },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.sm },
  title:    { fontSize: Typography.lg, fontWeight: Typography.bold, color: Colors.white },
  closeBtn: {
    width:           32,
    height:          32,
    borderRadius:    16,
    backgroundColor: Colors.navyMid,
    alignItems:      'center',
    justifyContent:  'center',
  },
  closeX: { fontSize: 14, color: Colors.gray, fontWeight: Typography.bold },

  // Tab bar
  tabBar: {
    flexDirection:   'row',
    gap:             Spacing.sm,
    marginBottom:    Spacing.lg,
  },
  tabPill: {
    flex:            1,
    flexDirection:   'row',
    alignItems:      'center',
    justifyContent:  'center',
    gap:             6,
    paddingVertical: 10,
    borderRadius:    Radius.lg,
    backgroundColor: Colors.navyMid,
    borderWidth:     1,
    borderColor:     Colors.navyBorder,
    overflow:        'hidden',
  },
  tabPillActive: {
    borderColor: Colors.blueVibrant,
  },
  tabPillMap: {
    flexDirection:  'column',
    paddingVertical: 8,
  },
  tabPillInnerRow: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           6,
  },
  tabNetHint: {
    fontSize:   9,
    color:      Colors.grayDark,
    marginTop:  2,
    fontWeight: Typography.medium,
  },
  tabNetHintActive: {
    color: Colors.gray,
  },
  tabIcon:       { fontSize: 15 },
  tabLabel:      { fontSize: Typography.sm, color: Colors.gray,  fontWeight: Typography.medium },
  tabLabelActive:{ fontSize: Typography.sm, color: Colors.white, fontWeight: Typography.semibold },

  // Generic tab content wrapper
  tabContent: {
    gap: Spacing.md,
  },

  // ── GPS ─────────────────────────────────────────────────────────────────────
  gpsHero: {
    alignItems:    'center',
    paddingVertical: Spacing.lg,
    gap:           Spacing.md,
  },
  gpsOrb: {
    width:          80,
    height:         80,
    borderRadius:   40,
    alignItems:     'center',
    justifyContent: 'center',
    overflow:       'hidden',
    borderWidth:    1,
    borderColor:    'rgba(37,99,235,0.4)',
    marginBottom:   Spacing.sm,
  },
  gpsOrbIcon:   { fontSize: 34 },
  gpsHeroTitle: { fontSize: Typography.md, fontWeight: Typography.bold, color: Colors.white },
  gpsHeroSub: {
    fontSize:   Typography.sm,
    color:      Colors.gray,
    textAlign:  'center',
    lineHeight: 20,
    paddingHorizontal: Spacing.md,
  },

  detectedCard: {
    flexDirection:   'row',
    alignItems:      'center',
    gap:             Spacing.md,
    padding:         Spacing.md,
    borderRadius:    Radius.lg,
    borderWidth:     1,
    borderColor:     'rgba(37,99,235,0.35)',
    backgroundColor: 'rgba(37,99,235,0.1)',
    overflow:        'hidden',
  },
  detectedCardGlow: {
    position:        'absolute',
    top:             0,
    left:            0,
    right:           0,
    height:          1,
    backgroundColor: Colors.blueVibrant,
    opacity:         0.5,
  },
  detectedPinDot: {
    width:          36,
    height:         36,
    borderRadius:   18,
    alignItems:     'center',
    justifyContent: 'center',
    overflow:       'hidden',
  },
  detectedPinIcon: { fontSize: 16 },
  detectedInfo:    { flex: 1 },
  detectedLabel: {
    fontSize:   Typography.base,
    fontWeight: Typography.semibold,
    color:      Colors.white,
    marginBottom: 2,
  },
  detectedCoords: { fontSize: Typography.xs, color: Colors.gray },
  retryBtn:  {
    width:          32,
    height:         32,
    borderRadius:   16,
    backgroundColor: Colors.navyMid,
    alignItems:     'center',
    justifyContent: 'center',
  },
  retryIcon: { fontSize: 18, color: Colors.gray },

  // ── Map ──────────────────────────────────────────────────────────────────────
  mapToggleRow: {
    flexDirection:  'row',
    alignItems:     'center',
    justifyContent: 'space-between',
  },
  mapToggleLabelRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  mapToggleLabel: {
    fontSize:   Typography.sm,
    fontWeight: Typography.semibold,
    color:      Colors.bluePale,
  },
  mapToggleHint: {
    fontSize: Typography.xs,
    color:    Colors.grayDark,
  },
  mapNetNote: {
    fontSize:     Typography.xs,
    color:        Colors.grayDark,
    fontStyle:    'italic',
    textAlign:    'center',
    marginTop:    -Spacing.xs,
  },

  mapWrapper: {
    borderRadius: Radius.lg,
    overflow:     'hidden',
    borderWidth:  1,
    borderColor:  Colors.navyBorder,
    height:       MAP_HEIGHT,
  },
  map: {
    width:  '100%',
    height: '100%',
  },
  mapHintOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems:     'flex-end',
    justifyContent: 'flex-end',
    padding:        Spacing.sm,
  },
  mapHintBadge: {
    flexDirection:   'row',
    alignItems:      'center',
    gap:             6,
    backgroundColor: 'rgba(5,13,31,0.82)',
    borderRadius:    Radius.md,
    paddingVertical: 6,
    paddingHorizontal: Spacing.sm,
    borderWidth:     1,
    borderColor:     Colors.navyBorder,
  },
  mapHintText: { fontSize: Typography.xs, color: Colors.bluePale },

  pinCard: {
    borderRadius:    Radius.lg,
    borderWidth:     1,
    borderColor:     'rgba(37,99,235,0.3)',
    backgroundColor: 'rgba(37,99,235,0.08)',
    padding:         Spacing.md,
  },
  pinCardInner: {
    flexDirection: 'row',
    alignItems:    'center',
    gap:           Spacing.sm,
  },
  pinCardIcon:   { fontSize: 20 },
  pinCardLabel: {
    flex:       1,
    fontSize:   Typography.base,
    fontWeight: Typography.semibold,
    color:      Colors.white,
    marginBottom: 2,
  },
  pinCardCoords: { fontSize: Typography.xs, color: Colors.gray },
  clearPinBtn: {
    width:          28,
    height:         28,
    borderRadius:   14,
    backgroundColor: Colors.navyMid,
    alignItems:     'center',
    justifyContent: 'center',
  },
  clearPinX: { fontSize: 12, color: Colors.gray },

  pinPlaceholder: {
    borderRadius:    Radius.lg,
    borderWidth:     1,
    borderColor:     Colors.navyBorder,
    borderStyle:     'dashed',
    padding:         Spacing.md,
    alignItems:      'center',
  },
  pinPlaceholderText: { fontSize: Typography.sm, color: Colors.grayDark },

  // ── Browse ───────────────────────────────────────────────────────────────────
  browseContent: {
    height: Math.min(H * 0.5, 440),
    gap:    Spacing.md,
  },
  browseHint: {
    fontSize:   Typography.xs,
    color:      Colors.gray,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    fontWeight: Typography.semibold,
  },
  browseLoading: {
    flex:           1,
    alignItems:     'center',
    justifyContent: 'center',
    gap:            Spacing.sm,
  },
  browseLoadingText: { fontSize: Typography.sm, color: Colors.gray },
  list:      { flex: 1 },
  listInner: { paddingBottom: Spacing.lg },
  listItem: {
    flexDirection:   'row',
    alignItems:      'center',
    justifyContent:  'space-between',
    paddingVertical:   11,
    paddingHorizontal: Spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: Colors.navyBorder,
  },
  listItemText:    { fontSize: Typography.base, color: Colors.grayLight },
  listItemChevron: { fontSize: 18, color: Colors.grayDark },
  listItemBack: {
    backgroundColor: 'rgba(37,99,235,0.08)',
    borderRadius:    Radius.sm,
    marginBottom:    Spacing.xs,
  },
  listItemBackText: {
    fontSize:   Typography.sm,
    fontWeight: Typography.semibold,
    color:      Colors.bluePale,
  },

  // ── Shared buttons ────────────────────────────────────────────────────────────
  primaryBtn: {
    borderRadius: Radius.lg,
    overflow:     'hidden',
    marginTop:    Spacing.xs,
  },
  primaryBtnDisabled: { opacity: 0.55 },
  primaryBtnGrad: {
    alignItems:      'center',
    justifyContent:  'center',
    paddingVertical: 15,
  },
  primaryBtnRow: { flexDirection: 'row', gap: Spacing.sm },
  primaryBtnText: {
    fontSize:   Typography.md,
    fontWeight: Typography.semibold,
    color:      Colors.white,
    letterSpacing: 0.2,
  },

  secondaryBtn: {
    alignItems:      'center',
    paddingVertical: Spacing.sm,
  },
  secondaryBtnText: {
    fontSize:   Typography.sm,
    color:      Colors.gray,
    fontWeight: Typography.medium,
  },
});
