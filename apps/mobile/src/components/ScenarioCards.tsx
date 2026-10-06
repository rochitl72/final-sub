/**
 * ScenarioCards — one card per person in a multi-person / multi-offence story,
 * with each offence's fine, section and any jail / court / s.199A tags.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Colors, Spacing, Radius } from '../theme';

const inr = (n?: number | null) => (n == null ? '—' : '₹' + Number(n).toLocaleString('en-IN'));

export function ScenarioCards({ scenario }: { scenario: any }) {
  const people: any[] = scenario?.people || [];
  if (!people.length) return null;
  return (
    <View style={styles.wrap}>
      {people.map((p, i) => {
        const meta = [p.age != null ? `${p.age} yrs` : '', (p.roles || []).join(', ')].filter(Boolean).join(' · ');
        return (
          <View key={p.id || i} style={[styles.card, p.kind === 'victim' && styles.victim]}>
            <View style={styles.head}>
              <Text style={styles.name}>
                {p.label || 'Person'}
                {meta ? <Text style={styles.meta}>{'  ' + meta}</Text> : null}
              </Text>
              {p.total_first && (p.offences || []).length > 1 ? (
                <Text style={styles.total}>Total {inr(p.total_first)}</Text>
              ) : null}
            </View>
            {(p.offences || []).map((o: any, j: number) => {
              const sub = [o.section, o.imprisonment ? `jail: ${o.imprisonment}` : '',
                o.compoundable === false ? 'goes to court' : '', o.licence_action || ''].filter(Boolean).join(' · ');
              return (
                <View key={o.code + j} style={[styles.off, j > 0 && styles.offBorder]}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.oname}>{o.name}</Text>
                    <View style={styles.tags}>
                      {o.certainty === 'may_be_liable' && <Text style={[styles.tag, styles.tagWarn]}>may apply</Text>}
                      {(o.deemed || o.juvenile) && <Text style={[styles.tag, styles.tagInfo]}>s.199A</Text>}
                      {o.severity === 'criminal' && <Text style={[styles.tag, styles.tagCrim]}>criminal</Text>}
                    </View>
                    {sub ? <Text style={styles.sub}>{sub}</Text> : null}
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <Text style={styles.fine}>{o.fine_first ? inr(o.fine_first) : 'court'}</Text>
                    {o.fine_repeat && o.fine_repeat !== o.fine_first ? (
                      <Text style={styles.sub}>repeat {inr(o.fine_repeat)}</Text>
                    ) : null}
                  </View>
                </View>
              );
            })}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap:    { gap: Spacing.sm, marginTop: 6, alignSelf: 'stretch' },
  card:    { backgroundColor: Colors.navyLight, borderRadius: Radius.lg ?? 14, borderWidth: 1,
             borderColor: Colors.navyBorder, padding: 12 },
  victim:  { opacity: 0.85, borderStyle: 'dashed' },
  head:    { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 },
  name:    { color: Colors.white, fontWeight: '700', fontSize: 14, flexShrink: 1 },
  meta:    { color: Colors.gray, fontWeight: '400', fontSize: 12 },
  total:   { color: Colors.bluePale, fontWeight: '700', fontSize: 13 },
  off:     { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 7, gap: 10 },
  offBorder: { borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.07)' },
  oname:   { color: Colors.white, fontSize: 13.5 },
  sub:     { color: Colors.gray, fontSize: 11.5, marginTop: 2 },
  fine:    { color: Colors.white, fontWeight: '700', fontSize: 13.5 },
  tags:    { flexDirection: 'row', gap: 6, marginTop: 3 },
  tag:     { fontSize: 10.5, paddingHorizontal: 7, paddingVertical: 1, borderRadius: 99, borderWidth: 1, overflow: 'hidden' },
  tagWarn: { color: Colors.warning, borderColor: Colors.warning },
  tagInfo: { color: Colors.bluePale, borderColor: Colors.bluePale },
  tagCrim: { color: Colors.error, borderColor: Colors.error },
});
