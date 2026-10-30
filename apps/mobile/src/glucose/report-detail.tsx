import { Alert, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import type { GlucoseReport } from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';

/** 报告上「低血糖风险」的档位，说成患者能听懂的话。 */
export const LBGI_LABELS: Record<string, string> = {
  high: '高',
  moderate: '中',
  low: '低',
  lowest: '最低',
};

type Props = {
  onClose: () => void;
  onDelete: () => void;
  /** 打开患者当初发进对话的那份 PDF；报告没有原文件时传 undefined。 */
  onOpenOriginal?: () => void;
  report: GlucoseReport;
};

function shortDay(iso: string): string {
  const parts = iso.split('-');
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : iso;
}

function fullDay(iso: string): string {
  const parts = iso.split('-');
  return parts.length === 3 ? `${parts[0]}-${parts[1]}-${parts[2]}` : iso;
}

function storedDay(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  const hour = `${date.getHours()}`.padStart(2, '0');
  const minute = `${date.getMinutes()}`.padStart(2, '0');
  return `${month}-${day} ${hour}:${minute}`;
}

/** 一行指标：左边是名字，右边是数值；没有那项就整行不出现。 */
function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metricRow}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

export function GlucoseReportDetail({ onClose, onDelete, onOpenOriginal, report }: Props) {
  const percent = (value: number | null): string | null =>
    value === null ? null : `${value}%`;

  const bands: [string, number | null][] = [
    ['正常（3.9–10.0）', report.normalPercent],
    ['偏低（3.0–3.9）', report.lowPercent],
    ['很低（<3.0）', report.veryLowPercent],
    ['偏高（10.0–13.9）', report.highPercent],
    ['很高（>13.9）', report.veryHighPercent],
  ];
  const shownBands = bands.filter(([, value]) => value !== null);

  const control: [string, string | null][] = [
    ['预估糖化（eHbA1c）', percent(report.ehba1cPercent)],
    ['平均葡萄糖', report.meanMmol === null ? null : `${report.meanMmol} mmol/L`],
    ['标准差（SD）', report.sdMmol === null ? null : `${report.sdMmol} mmol/L`],
    ['变异系数（CV）', percent(report.cvPercent)],
  ];
  const shownControl = control.filter(([, value]) => value !== null);

  const totals: [string, string | null][] = [
    ['目标范围内（TIR）', percent(report.tirPercent)],
    ['低于目标（TBR）', percent(report.tbrPercent)],
    ['高于目标（TAR）', percent(report.tarPercent)],
  ];
  const shownTotals = totals.filter(([, value]) => value !== null);

  const askDelete = (): void => {
    Alert.alert('删掉这份报告速览？', '删掉之后这条就不再出现在血糖记录里。', [
      { text: '先留着', style: 'cancel' },
      { text: '删掉', style: 'destructive', onPress: onDelete },
    ]);
  };

  const device = report.device.trim();
  const serial = report.deviceSerial.trim();

  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible>
      <SafeAreaView edges={['top', 'bottom']} style={styles.screen}>
        <View style={styles.header}>
          <View>
            <Text style={styles.eyebrow}>血糖记录 · 传感器</Text>
            <Text style={styles.headerTitle}>报告速览</Text>
          </View>
          <Pressable accessibilityLabel="关闭报告速览" onPress={onClose} style={styles.headerAction}>
            <Text style={styles.headerActionText}>关闭</Text>
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={styles.body}>
          <View style={styles.card}>
            <Text style={styles.cardTitle}>
              {`${shortDay(report.periodStart)} 至 ${shortDay(report.periodEnd)}`}
            </Text>
            <Text style={styles.cardBody}>
              {`监测区间 ${fullDay(report.periodStart)} 至 ${fullDay(report.periodEnd)}`}
            </Text>
            {device.length === 0 ? null : (
              <Text style={styles.cardBody}>
                {serial.length === 0 ? `设备 ${device}` : `设备 ${device} · 序列号 ${serial}`}
              </Text>
            )}
            {report.reportGeneratedAt === null ? null : (
              <Text style={styles.cardBody}>
                {`报告生成于 ${fullDay(report.reportGeneratedAt)}`}
              </Text>
            )}
            {report.note === null ? null : (
              <Text style={styles.cardHint}>{report.note}</Text>
            )}
          </View>

          {onOpenOriginal === undefined ? null : (
            <Pressable
              accessibilityLabel="打开原报告"
              accessibilityRole="button"
              onPress={onOpenOriginal}
              style={styles.openCard}
            >
              <View style={styles.openCopy}>
                <Text style={styles.openTitle}>打开原报告</Text>
                <Text style={styles.cardHint}>
                  就是你当时发进来的那份 PDF，用手机的打开方式看原始图谱。
                </Text>
              </View>
              <Text style={styles.openAction}>打开</Text>
            </Pressable>
          )}

          {shownControl.length === 0 ? null : (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>整体水平</Text>
              {shownControl.map(([label, value]) => (
                <MetricRow key={label} label={label} value={value ?? ''} />
              ))}
            </View>
          )}

          {shownTotals.length === 0 ? null : (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>时间花在哪</Text>
              {shownTotals.map(([label, value]) => (
                <MetricRow key={label} label={label} value={value ?? ''} />
              ))}
              {shownBands.length === 0 ? null : (
                <>
                  <View style={styles.divider} />
                  {shownBands.map(([label, value]) => (
                    <MetricRow key={label} label={label} value={`${value ?? 0}%`} />
                  ))}
                </>
              )}
            </View>
          )}

          {report.lbgiLevel.length === 0 ? null : (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>低血糖风险</Text>
              <MetricRow
                label="报告写的档位"
                value={LBGI_LABELS[report.lbgiLevel] ?? report.lbgiLevel}
              />
              <Text style={styles.cardHint}>
                这是报告按 LBGI 预测出来的档位，不是我们算的。
              </Text>
            </View>
          )}

          <Text style={styles.footnote}>
            这些数字都来自那份报告本身，不是我们重新算的；要看原始图谱，翻一翻当时的报告文件。
          </Text>
          {storedDay(report.createdAt).length === 0 ? null : (
            <Text style={styles.footnote}>
              {`存于 ${storedDay(report.createdAt)}`}
            </Text>
          )}

          <Pressable
            accessibilityLabel="删除这份报告速览"
            accessibilityRole="button"
            onPress={askDelete}
            style={styles.deleteButton}
          >
            <Text style={styles.deleteText}>删除这份报告速览</Text>
          </Pressable>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
    backgroundColor: colors.paper,
  },
  eyebrow: {
    color: colors.primary,
    fontSize: 11.5,
    fontFamily: typefaces.sansMedium,
  },
  headerTitle: {
    color: colors.ink,
    fontSize: 18,
    fontFamily: typefaces.sansMedium,
    marginTop: 2,
  },
  headerAction: { paddingHorizontal: spacing.sm, paddingVertical: 6 },
  headerActionText: {
    color: colors.primary,
    fontSize: 14,
    fontFamily: typefaces.sansMedium,
  },
  body: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.xl,
    gap: spacing.sm,
  },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  cardTitle: {
    color: colors.ink,
    fontSize: 15,
    fontFamily: typefaces.sansMedium,
  },
  cardBody: {
    color: colors.ink,
    fontSize: 12.5,
    lineHeight: 18,
    marginTop: 4,
  },
  cardHint: {
    color: colors.faint,
    fontSize: 11.5,
    lineHeight: 16,
    marginTop: 6,
  },
  openCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  openCopy: { flex: 1 },
  openTitle: {
    color: colors.ink,
    fontSize: 15,
    fontFamily: typefaces.sansMedium,
  },
  openAction: {
    color: colors.primary,
    fontSize: 13,
    fontFamily: typefaces.sansMedium,
  },
  metricRow: {    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: spacing.sm,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  metricLabel: {
    color: colors.faint,
    fontSize: 12.5,
    flexShrink: 1,
  },
  metricValue: {
    color: colors.ink,
    fontSize: 13.5,
    fontFamily: typefaces.sansMedium,
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: colors.line,
    marginVertical: 6,
  },
  footnote: {
    color: colors.faint,
    fontSize: 11.5,
    lineHeight: 17,
    paddingHorizontal: 2,
  },
  deleteButton: {
    marginTop: spacing.sm,
    alignItems: 'center',
    paddingVertical: 12,
  },
  deleteText: { color: colors.danger, fontSize: 13 },
});
