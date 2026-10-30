import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { ApiClient } from '../api/client';
import type { GlucoseReading, GlucoseSummary } from '../api/types';
import { EventTimeField } from '../ui/event-time-field';
import { measuredTimeLabel } from './measured-time';

const CONTEXTS: { value: string; label: string }[] = [
  { value: 'fasting', label: '空腹' },
  { value: 'pre_meal', label: '餐前' },
  { value: 'post_meal', label: '餐后' },
  { value: 'bedtime', label: '睡前' },
  { value: 'night', label: '夜间' },
];

const CONTEXT_LABELS: Record<string, string> = Object.fromEntries(
  CONTEXTS.map(item => [item.value, item.label]),
);

const DAYS = 14;

// 数值从哪来。患者自己分的清指尖血和传感器，界面上就该写出来。
const SOURCE_LABELS: Record<string, string> = {
  fingerstick: '指尖血',
  cgm: '传感器',
  lab: '化验单',
  manual: '手动',
  report: '报告解读',
};

// 手填时能选的来源。传感器那条走导入，不在这里点。
const INPUT_SOURCES: { value: 'fingerstick' | 'lab' | 'manual'; label: string }[] = [
  { value: 'fingerstick', label: '指尖血' },
  { value: 'lab', label: '静脉血' },
  { value: 'manual', label: '其他' },
];

// 指尖血才问机器：同一滴血，不同血糖仪能差 0.5 上下，对不上化验单时
// 这条信息才有用。静脉血是医院的机器，问了反而多余。
const INPUT_DEVICES: { value: 'roche' | 'abbott' | 'sannuo' | 'other'; label: string }[] = [
  { value: 'roche', label: '罗氏' },
  { value: 'abbott', label: '雅培' },
  { value: 'sannuo', label: '三诺' },
  { value: 'other', label: '其它血糖仪' },
];

const DEVICE_LABELS: Record<string, string> = {
  roche: '罗氏',
  abbott: '雅培',
  sannuo: '三诺',
  other: '其它血糖仪',
};

/** 测量时刻和记录时刻是两件事：测完过一会儿才记、或者第二天补记，都常见。
    这里给的是「什么时候测的」，保存那一刻由系统记成记录时间，两条分开存。 */
function recordedLine(item: GlucoseReading): string {
  const measured = new Date(item.measuredAt).getTime();
  const recorded = new Date(item.createdAt).getTime();
  if (Number.isNaN(measured) || Number.isNaN(recorded)) return '';
  const late = Math.abs(recorded - measured) >= 5 * 60_000;
  return `记录于 ${formatDay(item.createdAt)} ${formatTime(item.createdAt)}${late ? '（补记）' : ''}`;
}

type Props = {
  api: Pick<
    ApiClient,
    | 'getGlucoseReadings'
    | 'getGlucoseSummary'
    | 'createGlucoseReading'
    | 'revokeGlucoseReading'
  >;
};

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const hours = `${date.getHours()}`.padStart(2, '0');
  const minutes = `${date.getMinutes()}`.padStart(2, '0');
  return `${hours}:${minutes}`;
}

function formatDay(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

function groupByDay(readings: GlucoseReading[]): [string, GlucoseReading[]][] {
  const groups = new Map<string, GlucoseReading[]>();
  for (const reading of readings) {
    const key = formatDay(reading.measuredAt);
    const bucket = groups.get(key);
    if (bucket === undefined) groups.set(key, [reading]);
    else bucket.push(reading);
  }
  return [...groups.entries()];
}

export function GlucoseScreen({ api }: Props) {
  const [readings, setReadings] = useState<GlucoseReading[]>([]);
  const [summary, setSummary] = useState<GlucoseSummary | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [value, setValue] = useState('');
  const [context, setContext] = useState('fasting');
  const [source, setSource] = useState<'fingerstick' | 'lab' | 'manual'>('fingerstick');
  const [device, setDevice] = useState<'roche' | 'abbott' | 'sannuo' | 'other' | null>(null);
  // 测量时间：默认现在，可以往前改（测完过一会儿才记、或者翻到昨天补记）。
  const [measuredAt, setMeasuredAt] = useState<Date>(() => new Date());
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, brief] = await Promise.all([
        api.getGlucoseReadings(DAYS),
        api.getGlucoseSummary(7),
      ]);
      setReadings(list);
      setSummary(brief);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '血糖记录读取失败');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      await load();
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const save = useCallback(async () => {
    const parsed = Number(value.trim());
    if (!Number.isInteger(parsed) || parsed < 20 || parsed > 600) {
      setError('请输入 20 到 600 之间的整数血糖值（mg/dL）');
      return;
    }
    if (measuredAt.getTime() > Date.now() + 2 * 60_000) {
      setError('测量时间不能是将来。请把时间改到现在或更早。');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.createGlucoseReading({
        valueMgDl: parsed,
        measuredAt: measuredAt.toISOString(),
        context,
        source,
        sourceDevice: source === 'fingerstick' && device !== null ? device : undefined,
        note: note.trim() === '' ? null : note.trim(),
      });
      setValue('');
      setNote('');
      setMeasuredAt(new Date());
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '这条血糖没有保存成功');
    } finally {
      setSaving(false);
    }
  }, [api, context, device, source, load, note, value, measuredAt]);

  const revoke = useCallback(
    async (reading: GlucoseReading) => {
      setError(null);
      try {
        await api.revokeGlucoseReading(reading.id);
        await load();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '撤回没有成功');
      }
    },
    [api, load],
  );

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.eyebrow}>血糖记录</Text>
      <Text style={styles.title}>记一条血糖</Text>
      <Text style={styles.lead}>
        数值和时刻是我们最需要的，备注可以写也可以不写。
      </Text>

      <View style={styles.card}>
        <View style={styles.valueRow}>
          <TextInput
            value={value}
            onChangeText={setValue}
            placeholder="0"
            keyboardType="number-pad"
            maxLength={3}
            style={styles.valueInput}
            accessibilityLabel="血糖数值"
          />
          <Text style={styles.unit}>mg/dL</Text>
        </View>

        <View style={styles.chipRow}>
          {CONTEXTS.map(item => {
            const active = item.value === context;
            return (
              <Pressable
                key={item.value}
                onPress={() => setContext(item.value)}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                style={[styles.chip, active && styles.chipActive]}
              >
                <Text style={[styles.chipText, active && styles.chipTextActive]}>
                  {item.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {/* 来源要分清楚：指尖血是即时自测，静脉血是医院化验，两者数值口径不同。 */}
        <Text style={styles.sourceLabel}>数值来自</Text>
        <View style={styles.chipRow}>
          {INPUT_SOURCES.map(item => {
            const active = item.value === source;
            return (
              <Pressable
                key={item.value}
                onPress={() => setSource(item.value)}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`血糖来源 ${item.label}`}
                style={[styles.chip, active && styles.chipActive]}
              >
                <Text style={[styles.chipText, active && styles.chipTextActive]}>
                  {item.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {source === 'fingerstick' ? (
          <>
            <Text style={styles.sourceLabel}>用的哪台血糖仪</Text>
            <View style={styles.chipRow}>
              {INPUT_DEVICES.map(item => {
                const active = item.value === device;
                return (
                  <Pressable
                    key={item.value}
                    onPress={() => setDevice(active ? null : item.value)}
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    accessibilityLabel={`血糖仪 ${item.label}`}
                    style={[styles.chip, active && styles.chipActive]}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>
                      {item.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
            <Text style={styles.deviceHint}>
              可以不选。选上是给自己留个对照：同一滴血不同机器能差一点，跟化验单对不上时能看出是不是机器的事。
            </Text>
          </>
        ) : null}

        {/* 测量时间可以往前改：测完过一会儿才记、翻到昨天补记，都常见。
            记录时间由系统在保存那一刻写下，两者分开存、列表里也分开写。 */}
        <View style={styles.timeBlock}>
          <EventTimeField
            accessibilityLabel="测量时间"
            label="这次测量是什么时候"
            onChange={setMeasuredAt}
            value={measuredAt}
          />
          <Text style={styles.deviceHint}>
            记录时间会用你点保存的那一刻，不写进这里。
          </Text>
        </View>

        <TextInput
          value={note}
          onChangeText={setNote}
          placeholder="比如：刚吃了两碗饭（可不写）"
          maxLength={200}
          style={styles.noteInput}
          accessibilityLabel="血糖备注"
        />

        <Pressable
          onPress={() => void save()}
          disabled={saving}
          accessibilityRole="button"
          accessibilityLabel="保存这条血糖记录"
          style={[styles.saveButton, saving && styles.saveButtonDisabled]}
        >
          {saving ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <Text style={styles.saveText}>保存这条记录</Text>
          )}
        </Pressable>

        {error === null ? null : <Text style={styles.errorText}>{error}</Text>}
      </View>

      {summary === null ? null : (
        <Pressable
          onPress={() => setSummaryOpen(open => !open)}
          accessibilityRole="button"
          style={styles.summaryCard}
        >
          <Text style={styles.summaryTitle}>
            {summaryOpen ? '收起近 7 天概况' : '看看近 7 天概况'}
          </Text>
          {summaryOpen ? (
            <View style={styles.summaryBody}>
              <Text style={styles.summaryLine}>记录 {summary.count} 条</Text>
              <Text style={styles.summaryLine}>
                平均{' '}
                {summary.averageMgDl === null ? '—' : Math.round(summary.averageMgDl)}
                {'  '}最低{' '}
                {summary.minimumMgDl === null ? '—' : Math.round(summary.minimumMgDl)}
                {'  '}最高{' '}
                {summary.maximumMgDl === null ? '—' : Math.round(summary.maximumMgDl)}
              </Text>
              <Text style={styles.summaryLine}>
                低于 {(summary.targetLowMgDl / 18).toFixed(1)} 的{" "}
                {summary.belowTargetCount} 条
              </Text>
            </View>
          ) : null}
        </Pressable>
      )}

      <Text style={styles.sectionTitle}>最近 {DAYS} 天</Text>

      {loading ? (
        <ActivityIndicator style={styles.loading} color="#0F766E" />
      ) : readings.length === 0 ? (
        <Text style={styles.empty}>
          还没有记录。测完顺手记一条，下次聊天的时候糖糖就能看到。
        </Text>
      ) : (
        groupByDay(readings).map(([day, items]) => (
          <View key={day} style={styles.dayGroup}>
            <Text style={styles.dayLabel}>{day}</Text>
            {items.map(item => {
              const revoked = item.revokedAt !== null;
              return (
                <View key={item.id} style={styles.row}>
                  <View style={styles.rowMain}>
                    <Text style={[styles.rowValue, revoked && styles.revokedText]}>
                      {Math.round(item.valueMgDl)}
                      <Text style={styles.rowUnit}> mg/dL</Text>
                    </Text>
                    <Text style={styles.rowMeta}>
                      测量 {measuredTimeLabel(item.measuredAt, item.measuredPrecision)} ·{' '}
                      {SOURCE_LABELS[item.source] ?? '手动'}
                      {item.sourceDevice
                        ? `（${DEVICE_LABELS[item.sourceDevice] ?? item.sourceDevice}）`
                        : ''}{' '}
                      ·{' '}
                      {CONTEXT_LABELS[item.context] ?? '其他'}
                      {item.note === null ? '' : ` · ${item.note}`}
                    </Text>
                    <Text style={styles.rowHint}>{recordedLine(item)}</Text>
                  </View>
                  {revoked ? (
                    <Text style={styles.revokedTag}>已撤回</Text>
                  ) : (
                    <Pressable
                      onPress={() => void revoke(item)}
                      accessibilityRole="button"
                      accessibilityLabel={`撤回 ${Math.round(item.valueMgDl)} 这条记录`}
                      style={styles.revokeButton}
                    >
                      <Text style={styles.revokeText}>撤回</Text>
                    </Pressable>
                  )}
                </View>
              );
            })}
          </View>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#F4F6F5' },
  content: { paddingHorizontal: 20, paddingTop: 24, paddingBottom: 48 },
  eyebrow: { fontSize: 12, letterSpacing: 1, color: '#0F766E', fontWeight: '600' },
  title: { marginTop: 6, fontSize: 26, fontWeight: '700', color: '#12211D' },
  lead: { marginTop: 8, fontSize: 13, lineHeight: 20, color: '#5A6B66' },
  card: {
    marginTop: 16,
    padding: 16,
    borderRadius: 18,
    backgroundColor: '#FFFFFF',
  },
  valueRow: { flexDirection: 'row', alignItems: 'flex-end' },
  valueInput: {
    flex: 1,
    fontSize: 44,
    fontWeight: '700',
    color: '#12211D',
    padding: 0,
  },
  unit: { marginBottom: 8, marginLeft: 8, fontSize: 14, color: '#7A8B86' },
  chipRow: { marginTop: 12, flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  sourceLabel: { marginTop: 14, fontSize: 12, color: '#7A8B86' },
  chip: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: '#EEF3F1',
  },
  chipActive: { backgroundColor: '#0F766E' },
  chipText: { fontSize: 13, color: '#41524D' },
  chipTextActive: { color: '#FFFFFF', fontWeight: '600' },
  deviceHint: { marginTop: 6, fontSize: 11.5, lineHeight: 16, color: '#8B9A96' },
  timeBlock: { marginTop: 12 },
  noteInput: {
    marginTop: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 12,
    backgroundColor: '#F4F6F5',
    fontSize: 13,
    color: '#12211D',
  },
  saveButton: {
    marginTop: 14,
    paddingVertical: 14,
    borderRadius: 14,
    backgroundColor: '#0F766E',
    alignItems: 'center',
  },
  saveButtonDisabled: { opacity: 0.6 },
  saveText: { fontSize: 15, fontWeight: '600', color: '#FFFFFF' },
  errorText: { marginTop: 10, fontSize: 12, color: '#B42318' },
  summaryCard: {
    marginTop: 14,
    padding: 16,
    borderRadius: 18,
    backgroundColor: '#FFFFFF',
  },
  summaryTitle: { fontSize: 14, fontWeight: '600', color: '#0F766E' },
  summaryBody: { marginTop: 10, gap: 4 },
  summaryLine: { fontSize: 13, lineHeight: 20, color: '#41524D' },
  sectionTitle: {
    marginTop: 24,
    marginBottom: 8,
    fontSize: 14,
    fontWeight: '600',
    color: '#41524D',
  },
  loading: { marginTop: 12 },
  empty: { marginTop: 8, fontSize: 13, lineHeight: 20, color: '#7A8B86' },
  dayGroup: { marginTop: 12 },
  dayLabel: { fontSize: 12, color: '#7A8B86', marginBottom: 6 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 14,
    backgroundColor: '#FFFFFF',
    marginBottom: 8,
  },
  rowMain: { flex: 1 },
  rowValue: { fontSize: 18, fontWeight: '700', color: '#12211D' },
  rowUnit: { fontSize: 12, fontWeight: '400', color: '#7A8B86' },
  rowMeta: { marginTop: 4, fontSize: 12, color: '#7A8B86' },
  rowHint: { marginTop: 2, fontSize: 11, color: '#9AA9A4' },
  revokedText: { color: '#B0BCB8' },
  revokedTag: { fontSize: 12, color: '#B0BCB8' },
  revokeButton: { paddingHorizontal: 10, paddingVertical: 6 },
  revokeText: { fontSize: 12, color: '#B42318' },
});
