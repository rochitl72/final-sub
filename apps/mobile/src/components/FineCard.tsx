/**
 * FineCard — displays calculated fine details
 */
import React, { useRef, useEffect } from 'react';
import { View, Text, StyleSheet, Animated } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { Colors, Gradients, Typography, Spacing, Radius, Shadows } from '../theme';
import { FineCard as FineCardData, DetailRow } from '../services/api';

interface Props {
  card: FineCardData;
  detailTable?: DetailRow[];
}

function FineRow({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <View style={fc.row}>
      <Text style={fc.label}>{label}</Text>
      <Text style={[fc.value, highlight && fc.valueHighlight]}>{value}</Text>
    </View>
  );
}

function formatAmount(amount: number | null | undefined): string {
  if (amount == null) return '—';
  return `₹${amount.toLocaleString('en-IN')}`;
}

// Labels in the rich detail table that should be visually emphasised.
const HIGHLIGHT_LABELS = ['Fine (1st offence)', 'First Offence'];

export function FineCard({ card, detailTable }: Props) {
  const slideAnim = useRef(new Animated.Value(30)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.spring(slideAnim, { toValue: 0, tension: 80, friction: 10, useNativeDriver: true }),
      Animated.timing(fadeAnim,  { toValue: 1, duration: 400, useNativeDriver: true }),
    ]).start();
  }, []);

  // The narrate path supplies a normalized detail_table; the calculator path
  // uses the legacy structured fields. Prefer the rich table when present.
  const anyCard: any = card;
  const sectionLabel = card.section || anyCard.mv_section
    ? `Section ${(card.section || anyCard.mv_section)}`
    : null;
  const rows: DetailRow[] = (detailTable && detailTable.length)
    ? detailTable.filter((r) => r.label !== 'Violation' && r.label !== 'MV Act Section')
    : buildLegacyRows(card);

  return (
    <Animated.View style={[fc.wrap, { opacity: fadeAnim, transform: [{ translateY: slideAnim }] }]}>
      <LinearGradient colors={Gradients.fineCard} style={fc.card}>
        {/* Top sheen */}
        <LinearGradient colors={Gradients.sheen} style={fc.topGlow} />

        {/* Header */}
        <View style={fc.header}>
          <View style={fc.headerIconWrap}>
            <LinearGradient colors={Gradients.blueGloss} style={StyleSheet.absoluteFill} />
            <MaterialCommunityIcons name="scale-balance" size={18} color={Colors.white} />
          </View>
          <View style={fc.headerText}>
            <Text style={fc.violationName}>{card.violation_name || card.violation_code}</Text>
            {sectionLabel && <Text style={fc.section}>{sectionLabel}</Text>}
          </View>
        </View>

        {/* Location */}
        <View style={fc.locationRow}>
          <Ionicons name="location" size={13} color={Colors.bluePale} />
          <Text style={fc.location}>
            {[card.city_name, card.state_code].filter(Boolean).join(', ')}
            {card.road_bucket && ` · ${card.road_bucket}`}
          </Text>
        </View>

        {/* Vehicle */}
        {card.vehicle_segment && (
          <View style={fc.vehicleRow}>
            <Ionicons name="car-sport" size={13} color={Colors.gray} />
            <Text style={fc.vehicle}>{String(card.vehicle_segment).replace(/_/g, ' ')}</Text>
          </View>
        )}

        {rows.length > 0 && (
          <>
            <View style={fc.divider} />
            <View style={fc.finesBlock}>
              <Text style={fc.finesTitle}>Details</Text>
              {rows.map((r, i) => (
                <FineRow
                  key={`${r.label}-${i}`}
                  label={r.label}
                  value={r.value}
                  highlight={HIGHLIGHT_LABELS.includes(r.label)}
                />
              ))}
            </View>
          </>
        )}

        {/* Notes (legacy) */}
        {card.notes && (
          <>
            <View style={fc.divider} />
            <Text style={fc.notes}>{card.notes}</Text>
          </>
        )}
      </LinearGradient>
    </Animated.View>
  );
}

/** Convert legacy structured fields into label/value rows. */
function buildLegacyRows(card: FineCardData): DetailRow[] {
  const out: DetailRow[] = [];
  if (card.fine_first != null)  out.push({ label: 'First Offence',  value: formatAmount(card.fine_first) });
  if (card.fine_repeat != null) out.push({ label: 'Repeat Offence', value: formatAmount(card.fine_repeat) });
  if (card.fine_max != null)    out.push({ label: 'Maximum Fine',   value: formatAmount(card.fine_max) });
  if (card.imprisonment_days != null)
    out.push({ label: 'Imprisonment', value: `Up to ${card.imprisonment_days} days` });
  if (card.license_action) out.push({ label: 'Licence Action', value: card.license_action });
  return out;
}

const fc = StyleSheet.create({
  wrap:    { marginVertical: Spacing.sm },
  card:    {
    borderRadius:    Radius.xl,
    borderWidth:     1,
    borderColor:     Colors.accent + '40',
    overflow:        'hidden',
    ...Shadows.card,
  },
  topGlow: {
    position: 'absolute',
    top:      0,
    left:     0,
    right:    0,
    height:   60,
  },
  header: {
    flexDirection: 'row',
    alignItems:    'flex-start',
    padding:       Spacing.base,
    gap:           Spacing.md,
  },
  headerIconWrap: {
    width:          34,
    height:         34,
    borderRadius:   Radius.md,
    alignItems:     'center',
    justifyContent: 'center',
    overflow:       'hidden',
  },
  headerText:       { flex: 1 },
  violationName: {
    fontSize:   Typography.md,
    fontWeight: Typography.bold,
    color:      Colors.white,
    lineHeight: Typography.md * 1.3,
  },
  section: {
    fontSize:  Typography.sm,
    color:     Colors.accent,
    marginTop: 2,
  },
  locationRow: {
    flexDirection: 'row',
    alignItems:    'center',
    paddingHorizontal: Spacing.base,
    paddingBottom: Spacing.sm,
    gap:           Spacing.xs,
  },
  locationIcon: { fontSize: 13 },
  location:     { fontSize: Typography.sm, color: Colors.grayLight },
  vehicleRow:   {
    flexDirection:  'row',
    alignItems:     'center',
    paddingHorizontal: Spacing.base,
    paddingBottom:  Spacing.md,
    gap:            Spacing.xs,
  },
  vehicleIcon:  { fontSize: 13 },
  vehicle:      { fontSize: Typography.sm, color: Colors.gray },

  divider:  { height: 1, backgroundColor: Colors.navyBorder, marginHorizontal: Spacing.base },

  finesBlock: { padding: Spacing.base, gap: Spacing.sm },
  finesTitle: {
    fontSize:   Typography.sm,
    fontWeight: Typography.semibold,
    color:      Colors.gray,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: Spacing.xs,
  },
  row:   { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  label: { fontSize: Typography.sm, color: Colors.grayLight },
  value: {
    fontSize:   Typography.base,
    fontWeight: Typography.semibold,
    color:      Colors.grayLight,
  },
  valueHighlight: {
    fontSize:  Typography.lg,
    fontWeight: Typography.extrabold,
    color:      Colors.accent,
  },

  notes: {
    fontSize:  Typography.sm,
    color:     Colors.gray,
    padding:   Spacing.base,
    fontStyle: 'italic',
    lineHeight: Typography.sm * 1.5,
  },
});
