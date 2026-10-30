import { useMemo, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radii, spacing, typefaces } from './theme';

type Props = {
  /** 事件真实发生的时间；调用方负责把它转成带时区的 ISO。 */
  value: Date;
  onChange: (next: Date) => void;
  label?: string;
  accessibilityLabel?: string;
};

const QUICK_MINUTES = [0, 10, 30, 60, 120, 180];
const QUICK_LABELS: Record<number, string> = {
  0: '刚才',
  10: '10 分钟前',
  30: '30 分钟前',
  60: '1 小时前',
  120: '2 小时前',
  180: '3 小时前',
};

function pad(value: number): string {
  return `${value}`.padStart(2, '0');
}

function startOfDay(date: Date): Date {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

export function formatEventTime(value: Date): string {
  const now = new Date();
  const dayDelta = Math.round(
    (startOfDay(now).getTime() - startOfDay(value).getTime()) / 86_400_000,
  );
  const clock = `${pad(value.getHours())}:${pad(value.getMinutes())}`;
  if (dayDelta === 0) return `今天 ${clock}`;
  if (dayDelta === 1) return `昨天 ${clock}`;
  if (dayDelta === 2) return `前天 ${clock}`;
  return `${value.getMonth() + 1} 月 ${value.getDate()} 日 ${clock}`;
}

export function minutesAgo(from: Date, minutes: number): Date {
  return new Date(from.getTime() - minutes * 60_000);
}

/**
 * 「这件事是什么时候发生的」。患者经常是事后补记（中午吃的饭晚上才想起来写），
 * 所以不能只给「刚才 / 1 小时前」这种相对选项，必须能挑到具体某天的某个时刻。
 */
export function EventTimeField({
  accessibilityLabel = '发生时间',
  label = '发生时间',
  onChange,
  value,
}: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Date>(value);
  const summary = useMemo(() => formatEventTime(value), [value]);

  function openPicker() {
    setDraft(new Date(value));
    setOpen(true);
  }

  function setDay(offset: number) {
    const target = startOfDay(minutesAgo(new Date(), offset * 24 * 60));
    target.setHours(draft.getHours(), draft.getMinutes(), 0, 0);
    setDraft(target);
  }

  function shiftMinutes(minutes: number) {
    setDraft(new Date(draft.getTime() + minutes * 60_000));
  }

  function apply() {
    onChange(new Date(draft));
    setOpen(false);
  }

  return (
    <>
      <View style={styles.field}>
        <Text style={styles.label}>{label}</Text>
        <Pressable
          accessibilityLabel={`${accessibilityLabel}：${summary}，点按修改`}
          accessibilityRole="button"
          onPress={openPicker}
          style={styles.fieldButton}
        >
          <Text style={styles.fieldValue}>{summary}</Text>
          <Text style={styles.fieldEdit}>修改</Text>
        </Pressable>
      </View>

      <Modal
        animationType="fade"
        onRequestClose={() => setOpen(false)}
        transparent
        visible={open}
      >
        <View style={styles.overlay}>
          <Pressable
            accessibilityLabel="关闭时间选择"
            accessibilityRole="button"
            onPress={() => setOpen(false)}
            style={StyleSheet.absoluteFill}
          />
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle}>这件事是什么时候</Text>
            <Text style={styles.sheetHint}>
              现在选的是 {formatEventTime(draft)}，可以改到过去任意时刻。
            </Text>

            <View style={styles.chipRow}>
              {QUICK_MINUTES.map(minutes => (
                <Pressable
                  key={minutes}
                  accessibilityRole="button"
                  onPress={() => setDraft(minutesAgo(new Date(), minutes))}
                  style={styles.chip}
                >
                  <Text style={styles.chipText}>{QUICK_LABELS[minutes]}</Text>
                </Pressable>
              ))}
            </View>

            <View style={styles.chipRow}>
              <Pressable
                accessibilityRole="button"
                onPress={() => setDay(0)}
                style={styles.chip}
              >
                <Text style={styles.chipText}>今天</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={() => setDay(1)}
                style={styles.chip}
              >
                <Text style={styles.chipText}>昨天</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={() => setDay(2)}
                style={styles.chip}
              >
                <Text style={styles.chipText}>前天</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={() => setDay(3)}
                style={styles.chip}
              >
                <Text style={styles.chipText}>更早（−1 天）</Text>
              </Pressable>
            </View>

            <View style={styles.stepperRow}>
              <View style={styles.stepper}>
                <Pressable
                  accessibilityLabel="小时减一"
                  accessibilityRole="button"
                  onPress={() => shiftMinutes(-60)}
                  style={styles.stepperButton}
                >
                  <Text style={styles.stepperText}>−</Text>
                </Pressable>
                <Text style={styles.stepperValue}>{pad(draft.getHours())} 时</Text>
                <Pressable
                  accessibilityLabel="小时加一"
                  accessibilityRole="button"
                  onPress={() => shiftMinutes(60)}
                  style={styles.stepperButton}
                >
                  <Text style={styles.stepperText}>＋</Text>
                </Pressable>
              </View>
              <View style={styles.stepper}>
                <Pressable
                  accessibilityLabel="分钟减五"
                  accessibilityRole="button"
                  onPress={() => shiftMinutes(-5)}
                  style={styles.stepperButton}
                >
                  <Text style={styles.stepperText}>−</Text>
                </Pressable>
                <Text style={styles.stepperValue}>{pad(draft.getMinutes())} 分</Text>
                <Pressable
                  accessibilityLabel="分钟加五"
                  accessibilityRole="button"
                  onPress={() => shiftMinutes(5)}
                  style={styles.stepperButton}
                >
                  <Text style={styles.stepperText}>＋</Text>
                </Pressable>
              </View>
            </View>

            <Pressable
              accessibilityLabel="用这个时间"
              accessibilityRole="button"
              onPress={apply}
              style={styles.confirm}
            >
              <Text style={styles.confirmText}>用这个时间</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  field: { gap: 6 },
  label: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12.5,
  },
  fieldButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderRadius: radii.md,
    backgroundColor: colors.paperMuted,
    paddingHorizontal: spacing.sm,
    paddingVertical: 10,
  },
  fieldValue: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
    fontWeight: '600',
  },
  fieldEdit: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sans,
    fontSize: 12.5,
  },
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(23, 48, 46, 0.35)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: colors.paper,
    borderTopLeftRadius: radii.lg,
    borderTopRightRadius: radii.lg,
    padding: spacing.md,
    gap: spacing.sm,
  },
  sheetTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
    fontWeight: '700',
  },
  sheetHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12.5,
    lineHeight: 19,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    borderRadius: radii.pill,
    backgroundColor: colors.paperMuted,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  chipText: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 12.5,
  },
  stepperRow: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  stepper: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderRadius: radii.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    paddingHorizontal: 6,
    paddingVertical: 6,
  },
  stepperButton: {
    width: 34,
    height: 34,
    borderRadius: radii.pill,
    backgroundColor: colors.paperMuted,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepperText: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 16,
    fontWeight: '700',
  },
  stepperValue: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
    fontWeight: '600',
  },
  confirm: {
    marginTop: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
    paddingVertical: 13,
    alignItems: 'center',
  },
  confirmText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 14.5,
    fontWeight: '600',
  },
});
