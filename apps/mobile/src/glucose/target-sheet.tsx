import { useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import type { GlucoseTarget } from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';

/** 目标范围按 0.1 mmol/L 调；库里存 mg/dL，所以一步是 2 mg/dL。 */
export const TARGET_STEP_MG_DL = 2;

function mmol(value: number): string {
  return (value / 18).toFixed(1);
}

type Props = {
  onCancel: () => void;
  onSave: (target: GlucoseTarget) => void;
  target: GlucoseTarget;
  visible: boolean;
};

/**
 * 目标范围是患者（或他的医生）给的，不是我们替他定的。
 *
 * 老年人常被放宽、孕妇要更严，所以这里只提供上下限两个加减和一个保存，
 * 不做「推荐值」——推荐就变成我们替他定了标准。
 *
 * 草稿用 null 表示「没动过」：没动过就显示外面的真数据，动过才用草稿，
 * 取消时把草稿丢掉。这样不需要在打开时用 effect 往回拷值。
 */
export function GlucoseTargetSheet({ onCancel, onSave, target, visible }: Props) {
  const [draft, setDraft] = useState<GlucoseTarget | null>(null);
  const low = draft?.lowMgDl ?? target.lowMgDl;
  const high = draft?.highMgDl ?? target.highMgDl;
  const setLow = (next: number) => setDraft({ highMgDl: high, lowMgDl: next });
  const setHigh = (next: number) => setDraft({ highMgDl: next, lowMgDl: low });
  const broken = low >= high;

  const cancel = () => {
    setDraft(null);
    onCancel();
  };

  return (
    <Modal
      animationType="slide"
      onRequestClose={cancel}
      presentationStyle="pageSheet"
      statusBarTranslucent
      transparent={false}
      visible={visible}
    >
      <SafeAreaView style={styles.safe}>
        <View style={styles.header}>
          <Pressable
            accessibilityLabel="取消修改目标范围"
            onPress={cancel}
            style={styles.headerButton}
          >
            <Text style={styles.headerButtonText}>取消</Text>
          </Pressable>
          <Text style={styles.headerTitle}>血糖目标范围</Text>
          <View style={styles.headerButton} />
        </View>

        <View style={styles.body}>
          <Text style={styles.hint}>
            照医生给你的范围填。填完统计和对话都按这个范围算高低，不再用通用的
            3.9–10.0。
          </Text>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>下限</Text>
            <View style={styles.stepperRow}>
              <Stepper
                label="下限减 0.1"
                onPress={() => setLow(Math.max(36, low - TARGET_STEP_MG_DL))}
                text="−"
              />
              <Text style={styles.value}>{`${mmol(low)} mmol/L`}</Text>
              <Stepper
                label="下限加 0.1"
                onPress={() =>
                  setLow(Math.min(high - TARGET_STEP_MG_DL, low + TARGET_STEP_MG_DL))
                }
                text="+"
              />
            </View>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>上限</Text>
            <View style={styles.stepperRow}>
              <Stepper
                label="上限减 0.1"
                onPress={() =>
                  setHigh(Math.max(low + TARGET_STEP_MG_DL, high - TARGET_STEP_MG_DL))
                }
                text="−"
              />
              <Text style={styles.value}>{`${mmol(high)} mmol/L`}</Text>
              <Stepper
                label="上限加 0.1"
                onPress={() => setHigh(Math.min(360, high + TARGET_STEP_MG_DL))}
                text="+"
              />
            </View>
          </View>

          {broken ? <Text style={styles.error}>下限要小于上限。</Text> : null}

          <Pressable
            accessibilityLabel="保存目标范围"
            disabled={broken}
            onPress={() => {
              setDraft(null);
              onSave({ highMgDl: high, lowMgDl: low });
            }}
            style={[styles.primaryButton, broken ? styles.primaryButtonOff : null]}
          >
            <Text style={styles.primaryButtonText}>保存</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </Modal>
  );
}

function Stepper({
  label,
  onPress,
  text,
}: {
  label: string;
  onPress: () => void;
  text: string;
}) {
  return (
    <Pressable
      accessibilityLabel={label}
      onPress={onPress}
      style={styles.stepper}
    >
      <Text style={styles.stepperText}>{text}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  safe: { backgroundColor: colors.background, flex: 1 },
  header: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
  },
  headerButton: { minWidth: 52, paddingVertical: 6 },
  headerButtonText: { color: colors.primary, fontSize: 15 },
  headerTitle: { color: colors.ink, fontSize: 15.5, fontWeight: '600' },
  body: { gap: spacing.sm, paddingHorizontal: spacing.md },
  hint: { color: colors.muted, fontSize: 12.5, lineHeight: 19 },
  card: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    padding: spacing.md,
  },
  cardTitle: { color: colors.ink, fontSize: 13.5, fontWeight: '600' },
  stepperRow: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.sm,
  },
  stepper: {
    alignItems: 'center',
    backgroundColor: colors.background,
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    height: 34,
    justifyContent: 'center',
    width: 44,
  },
  stepperText: { color: colors.ink, fontSize: 17 },
  value: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 16,
  },
  error: { color: colors.danger, fontSize: 12.5 },
  primaryButton: {
    alignItems: 'center',
    backgroundColor: colors.primary,
    borderRadius: radii.md,
    marginTop: spacing.sm,
    paddingVertical: 13,
  },
  primaryButtonOff: { backgroundColor: colors.muted },
  primaryButtonText: { color: colors.white, fontSize: 15, fontWeight: '600' },
});
