/**
 * MultiSelect — vertical checkbox MCQ with a compact Proceed button.
 */
import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors, Typography, Spacing, Radius } from '../theme';
import { Chip } from '../services/api';

const OTHER_ID = 'clarify:other';
const NONE_ID  = 'clarify:none';

interface Props {
  options:     Chip[];
  mode?:       'single' | 'multi';
  allowOther?: boolean;
  onSubmit:    (selectedIds: string[], otherText?: string) => void;
  onProceed?:  () => void;
}

export function MultiSelect({
  options,
  mode = 'multi',
  allowOther = true,
  onSubmit,
  onProceed,
}: Props) {
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [otherText, setOtherText] = useState('');
  const [done, setDone] = useState(false);

  const isSingle = mode === 'single';
  const otherOn  = !!selected[OTHER_ID];

  const toggle = (id: string) => {
    if (done) return;
    if (isSingle) {
      // Radio question: one tap answers it — no separate Proceed press.
      setSelected({ [id]: true });
      if (id !== OTHER_ID) {
        setDone(true);
        onProceed?.();
        onSubmit([id]);
      }
      return;
    }
    if (id === NONE_ID) {
      setSelected({ [NONE_ID]: !selected[NONE_ID] });
      return;
    }
    setSelected((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      delete next[NONE_ID];
      return next;
    });
  };

  const chosenIds = options.filter((o) => selected[o.id]).map((o) => o.id);
  const trimmedOther = otherText.trim();

  const canProceed = (() => {
    if (done) return false;
    // Always require at least one selection — prevents accidental empty submissions
    // in multi-select mode which would auto-fire the topic_router on the backend.
    return chosenIds.length > 0;
  })();

  const proceed = () => {
    if (!canProceed) return;
    setDone(true);
    onProceed?.();
    onSubmit(
      chosenIds,
      otherOn && trimmedOther ? trimmedOther : undefined,
    );
  };

  return (
    <View style={s.panel}>
      {options.map((opt) => {
        const isOn = !!selected[opt.id];
        const isOther = opt.id === OTHER_ID;
        return (
          <View key={opt.id}>
            <TouchableOpacity
              style={[s.row, isOn && s.rowOn, done && s.rowDone]}
              activeOpacity={0.85}
              disabled={done}
              onPress={() => toggle(opt.id)}
            >
              <View style={[s.box, isOn && s.boxOn]}>
                {isOn && (
                  <Ionicons name="checkmark" size={13} color={Colors.white} />
                )}
              </View>
              <Text style={[s.label, isOn && s.labelOn]} numberOfLines={3}>
                {opt.label}
              </Text>
            </TouchableOpacity>

            {isOther && isOn && allowOther && (
              <TextInput
                style={s.otherInput}
                placeholder="Add your details here…"
                placeholderTextColor={Colors.gray}
                value={otherText}
                onChangeText={setOtherText}
                editable={!done}
                multiline
                maxLength={500}
              />
            )}
          </View>
        );
      })}

      {!done && !canProceed && (
        <Text style={s.hintText}>{isSingle ? 'Tap an option to answer' : 'Select at least one option above'}</Text>
      )}
      {(!isSingle || otherOn) && (
      <TouchableOpacity
        style={[s.proceedBtn, !canProceed && s.proceedDisabled, done && s.proceedDone]}
        activeOpacity={0.9}
        disabled={!canProceed || done}
        onPress={proceed}
      >
        <Text style={s.proceedText}>
          {done ? 'Sent' : 'Proceed'}
        </Text>
        {!done && canProceed && (
          <Ionicons name="arrow-forward" size={14} color={Colors.white} />
        )}
      </TouchableOpacity>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  panel: {
    gap:             Spacing.sm,
    paddingVertical: Spacing.xs,
    maxWidth:        '100%',
  },
  row: {
    flexDirection:     'row',
    alignItems:        'flex-start',
    gap:               Spacing.sm,
    paddingVertical:   10,
    paddingHorizontal: Spacing.md,
    borderRadius:      Radius.md,
    borderWidth:       1,
    borderColor:       Colors.navyBorder,
    backgroundColor:   Colors.surfaceRaised,
  },
  rowOn: {
    borderColor:     Colors.blueVibrant,
    backgroundColor: 'rgba(37,99,235,0.10)',
  },
  rowDone: { opacity: 0.65 },
  box: {
    width:          20,
    height:         20,
    borderRadius:   5,
    borderWidth:    1.5,
    borderColor:    Colors.gray,
    alignItems:     'center',
    justifyContent: 'center',
    marginTop:      2,
  },
  boxOn: {
    borderColor:     Colors.blueVibrant,
    backgroundColor: Colors.blueVibrant,
  },
  label: {
    flex:       1,
    fontSize:   Typography.sm,
    color:      Colors.grayLight,
    lineHeight: Typography.sm * 1.45,
  },
  labelOn: { color: Colors.white, fontWeight: Typography.semibold },

  otherInput: {
    marginTop:         2,
    marginBottom:      Spacing.sm,
    marginLeft:        Spacing.md + 20 + Spacing.sm,
    marginRight:       Spacing.md,
    padding:           Spacing.sm,
    borderRadius:      Radius.md,
    borderWidth:       1,
    borderColor:       Colors.navyBorder,
    backgroundColor:   Colors.navyMid,
    color:             Colors.white,
    fontSize:          Typography.sm,
    minHeight:         64,
    textAlignVertical: 'top',
  },

  proceedBtn: {
    alignSelf:         'flex-end',
    flexDirection:     'row',
    alignItems:        'center',
    gap:               6,
    paddingVertical:   8,
    paddingHorizontal: 18,
    borderRadius:      Radius.full,
    backgroundColor:   Colors.blueVibrant,
    marginTop:         Spacing.xs,
  },
  proceedDisabled: { opacity: 0.4 },
  proceedDone:     { backgroundColor: Colors.navyMid },
  proceedText: {
    fontSize:   Typography.sm,
    fontWeight: Typography.bold,
    color:      Colors.white,
  },
  hintText: {
    fontSize:  Typography.xs,
    color:     Colors.gray,
    textAlign: 'center' as const,
    marginBottom: 4,
    fontStyle: 'italic' as const,
  },
});
