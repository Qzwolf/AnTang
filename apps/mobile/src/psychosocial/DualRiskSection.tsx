import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import type { ApiClient } from '../api/client';
import type { DualRiskEntry, DualRiskLabel } from '../api/types';
import { measuredTimeLabel } from '../glucose/measured-time';
import { colors, radii, spacing, typefaces } from '../ui/theme';

/**
 * 「血糖多少」与「当时有没有感觉」并排放着（只读）。
 *
 * 为什么值得单独一块：「血糖 3.2 却毫无感觉」是四种处境里最危险的一种，也是最难
 * 自己发现的一种。把它和「慌得不行但血糖没事」并排放在他自己的页面上，比我们说
 * 十句「注意低血糖」有用。
 *
 * 所以这里刻意不做三件事：不汇总（「这个月 4 次」会变成要完成的指标）、不评分
 * （没有「合格/不合格」）、不给建议（要说什么在对话里说）。也不给编辑——要改，
 * 得他在对话里再说一次，跟念头卡片一致。
 */

const SILENT_COLOR = colors.danger;
const FELT_ONLY_COLOR = '#7FA7C9';
const BOTH_COLOR = '#8BA8A0';
const NEITHER_COLOR = colors.faint;

const LABEL_COLORS: Record<DualRiskLabel, string> = {
  silent: SILENT_COLOR,
  felt_only: FELT_ONLY_COLOR,
  both: BOTH_COLOR,
  neither: NEITHER_COLOR,
};

function mmolText(valueMgDl: number | null): string {
  if (valueMgDl === null) return '当时没记数值';
  return `${(valueMgDl / 18).toFixed(1)} mmol/L`;
}

export type DualRiskSectionProps = {
  api: Pick<ApiClient, 'getDualRiskEntries'>;
  /**
   * 点一行回到那天的记录。
   *
   * 这一块只把两件事并排放着，患者看完往往还想看当天到底还记了什么（吃过什么、
   * 打了多少）。不传就只是文字，面板上不许出现按不动的「查看」。
   */
  onOpenRecord?: (measuredAt: string) => void;
};

export function DualRiskSection({ api, onOpenRecord }: DualRiskSectionProps) {
  const [entries, setEntries] = useState<DualRiskEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void api
      .getDualRiskEntries()
      .then((next) => {
        if (!active) return;
        setEntries(next);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoadError(error instanceof Error ? error.message : '读取失败');
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api]);

  const hasEntries = (entries?.length ?? 0) > 0;

  return (
    <View style={styles.card}>
      <Text style={styles.title}>血糖和你的感觉</Text>
      <Text style={styles.lead}>有时候身体先报警，有时候你先知道。</Text>
      {loading ? <ActivityIndicator color={colors.primary} /> : null}
      {loadError !== null ? <Text style={styles.error}>{loadError}</Text> : null}
      {!loading && loadError === null && !hasEntries ? (
        <Text style={styles.empty}>
          还没有记下这样的对照。你在对话里说「刚测了 3.1，可我一点感觉都没有」，
          我就会记在这里。
        </Text>
      ) : null}
      {entries?.map((entry) => {
        const meta = `${measuredTimeLabel(entry.measuredAt, undefined)} · ${mmolText(entry.valueMgDl)}`;
        const row = (
          <>
            <View
              style={[styles.bar, { backgroundColor: LABEL_COLORS[entry.label] }]}
            />
            <View style={styles.rowBody}>
              <Text style={styles.rowText}>{entry.labelText}</Text>
              <Text style={styles.rowMeta}>{meta}</Text>
            </View>
          </>
        );
        if (onOpenRecord === undefined) {
          return (
            <View key={entry.id} style={styles.row}>
              {row}
            </View>
          );
        }
        return (
          <Pressable
            accessibilityHint="回到那天的记录"
            accessibilityLabel={`${entry.labelText}，${meta}`}
            accessibilityRole="button"
            key={entry.id}
            onPress={() => onOpenRecord(entry.measuredAt)}
            style={({ pressed }) => [styles.row, pressed ? styles.rowPressed : null]}
          >
            {row}
          </Pressable>
        );
      })}
      {hasEntries ? (
        <>
          <Text style={styles.legend}>
            橙色是「血糖偏了，你没觉得」；蓝色是「血糖不算偏，你却很难受」。
          </Text>
          <Text style={styles.footnote}>
            这里只把你当时的记录并排放着，不打分，也不给建议。要改，就在对话里再说一次。
          </Text>
          {onOpenRecord === undefined ? null : (
            <Text style={styles.footnote}>点一行，能回到那天的记录。</Text>
          )}
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: spacing.sm,
    padding: spacing.lg,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.serif,
    fontSize: 18,
  },
  lead: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 20,
  },
  empty: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 22,
  },
  error: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  row: {
    flexDirection: 'row',
    gap: spacing.md,
    paddingVertical: spacing.xs,
  },
  // 可按的时候给一点按压反馈：这一块自己是只读的，能点的地方要让手指知道。
  rowPressed: {
    opacity: 0.6,
  },
  bar: {
    alignSelf: 'stretch',
    borderRadius: 2,
    marginVertical: 3,
    width: 3,
  },
  rowBody: {
    flex: 1,
    gap: 3,
  },
  rowText: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 16,
    lineHeight: 23,
  },
  rowMeta: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  legend: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
  footnote: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
});
