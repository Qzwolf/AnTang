import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { ApiClient } from '../api/client';
import type {
  BeliefEventRecord,
  FearBehaviorLog,
  FearSummary,
  HypoglycemiaBeliefNode,
} from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';

/**
 * 行为与担心：把聊天里自然说出来的行为和念头摊开给自己看。
 *
 * 这一页没有输入框——数据全部来自对话本身（模型调用 `log_fear_behavior` /
 * `record_hypoglycemia_belief` 时落库），因为让患者每天手动打卡的行为数据几乎一定
 * 会断更，而「又测了一次」「今晚不敢跑步」这类话本来就会在对话里出现。
 *
 * 不显示分数、不给分级、不下判断：显性量表分在「沟通方式」那一页，这里只呈现
 * 次数与原话，两者的对照说明由后端给出（`discrepancyNote`），并且明确写成
 * 「一致不一致」，不是诊断。
 *
 * 下半部分是可逐条撤回的记录。汇总卡片只给次数，患者看着「夜间查血糖 6 次」却
 * 没法说「那次不算」，等于没给撤回权；所以这里列出单条记录、带上 id，删之前
 * 还要再确认一次（AGENTS.md 的要求，且这类记录删了就找不回来）。
 */

export const BEHAVIOR_LABELS: Record<string, string> = {
  extra_check: '额外查血糖',
  night_check: '夜间查血糖',
  comfort_meal: '因为担心而加餐',
  exercise_avoidance: '回避运动',
  insulin_hold: '自行减少胰岛素',
  insulin_change: '自行调整胰岛素剂量',
};

const BEHAVIOR_ORDER = [
  'extra_check',
  'night_check',
  'comfort_meal',
  'exercise_avoidance',
  'insulin_hold',
  'insulin_change',
];

export type FearBehaviorSectionProps = {
  api: Pick<
    ApiClient,
    | 'getFearSummary'
    | 'getFearBehaviorLogs'
    | 'getBeliefEvents'
    | 'deleteFearBehaviorLog'
    | 'deleteBeliefEvent'
  >;
  /**
   * 同一份数据按需求拆成三块：
   * - `all`：行为标签 + 反复出现的念头 + 可删记录（默认，保持旧行为）
   * - `beliefs`：只出「反复出现的念头」，放在沟通方式自评下面
   * - `records`：只出逐条记录与撤回
   * 拆开是因为两件事对患者的含义不同：念头是理解，记录是管理权。
   */
  mode?: 'all' | 'beliefs' | 'records';
};

type PendingDelete = { kind: 'behavior' | 'belief'; id: string };

export function FearBehaviorSection({
  api,
  mode = 'all',
}: FearBehaviorSectionProps) {
  const [summary, setSummary] = useState<FearSummary | null>(null);
  const [logs, setLogs] = useState<FearBehaviorLog[]>([]);
  const [events, setEvents] = useState<BeliefEventRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingDelete | null>(null);
  const [deleting, setDeleting] = useState(false);
  // 念头默认收起：摊开「低血糖会死」这类原话对刚从低血糖里缓过来的人是加码，
  // 想看的自己点开，不想看的不用被一次全灌进来。
  const [showBeliefs, setShowBeliefs] = useState(false);

  useEffect(() => {
    let active = true;
    // 三个接口一起要：汇总给次数，两个列表给可删的条目。setState 全部放在
    // then/catch 回调里，不在 effect 体里同步调用（react-hooks 规则）。
    void Promise.all([
      api.getFearSummary(),
      api.getFearBehaviorLogs(),
      api.getBeliefEvents(),
    ])
      .then(([nextSummary, nextLogs, nextEvents]) => {
        if (!active) return;
        setSummary(nextSummary);
        setLogs(nextLogs);
        setEvents(nextEvents);
        setLoadError(null);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setLoadError(errorMessage(reason));
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api]);

  const remove = useCallback(
    async (target: PendingDelete): Promise<void> => {
      setDeleting(true);
      try {
        if (target.kind === 'behavior') {
          await api.deleteFearBehaviorLog(target.id);
          setLogs((current) => current.filter((log) => log.id !== target.id));
        } else {
          await api.deleteBeliefEvent(target.id);
          setEvents((current) =>
            current.filter((event) => event.id !== target.id),
          );
        }
        setActionError(null);
        setPending(null);
        // 汇总的数字要跟着变，否则刚删掉的那几条还挂在「6 次」里。
        setSummary(await api.getFearSummary());
      } catch (reason) {
        setActionError(errorMessage(reason));
      } finally {
        setDeleting(false);
      }
    },
    [api],
  );

  return (
    <View style={styles.card}>
      <View style={styles.kickerPill}>
        <Text style={styles.kicker}>
          {mode === 'records' ? '记录' : mode === 'beliefs' ? '念头' : '行为记录'}
        </Text>
      </View>
      <Text style={styles.title}>
        {mode === 'records'
          ? '我的记录与撤回'
          : mode === 'beliefs'
            ? '反复出现的念头'
            : '你最近在做什么'}
      </Text>

      {loading ? (
        <View style={styles.loadingRow}>
          <ActivityIndicator color={colors.primary} size="small" />
          <Text style={styles.loadingText}>正在读取…</Text>
        </View>
      ) : loadError !== null ? (
        <Text style={styles.errorText}>{loadError}</Text>
      ) : mode === 'records' || summary === null ? null : isEmpty(summary) ? (
        <Text style={styles.description}>
          还没有记录。聊天时顺口提到的「刚才又测了一次」「今晚不敢跑步」这类事，糖糖会记在这里，下次就记得了。
        </Text>
      ) : (
        <View>
          {mode === 'all' ? (
            <>
              <Text style={styles.description}>
                {mentionSentence(summary)}这些是聊天里顺口提到的，不是量表，也不打分。
              </Text>

              {/* 行为改成可换行的小标签：原来是「标签 ─ 次数」两列，次数撤掉后
                  右侧会空出一大块，看着像排版坏了。 */}
              <View style={styles.chips}>
                {BEHAVIOR_ORDER.filter(
                  (kind) => (summary.behaviorCounts[kind] ?? 0) > 0,
                ).map((kind) => (
                  <View key={kind} style={styles.chip}>
                    <View style={styles.rowDot} />
                    <Text style={styles.rowLabel}>{behaviorLabel(kind)}</Text>
                  </View>
                ))}
              </View>
            </>
          ) : (
            <Text style={styles.description}>
              这些是你在聊天里说过的想法，原话留着。不想看可以收起，想删可以到下面「我的记录与撤回」里删。
            </Text>
          )}

          {summary.beliefs.length > 0 ? (
            <View style={styles.beliefBlock}>
              <Pressable
                accessibilityLabel={
                  showBeliefs ? '收起反复出现的念头' : '展开反复出现的念头'
                }
                accessibilityRole="button"
                onPress={() => setShowBeliefs((current) => !current)}
                style={styles.sectionHead}
              >
                <View style={[styles.sectionBar, styles.sectionBarBelief]} />
                <Text style={styles.subtitle}>反复出现的念头</Text>
                <Text style={styles.sectionAction}>
                  {showBeliefs ? '收起' : '展开'}
                </Text>
              </Pressable>
              {showBeliefs
                ? summary.beliefs.map((belief) => (
                    <BeliefRow belief={belief} key={belief.beliefCode} />
                  ))
                : null}
            </View>
          ) : null}
        </View>
      )}

      {actionError !== null ? (
        <Text style={styles.errorText}>{actionError}</Text>
      ) : null}

      {mode !== 'beliefs' &&
      !loading &&
      loadError === null &&
      (logs.length > 0 || events.length > 0) ? (
        <View style={styles.recordsBlock}>
          <View style={styles.sectionHead}>
            <View style={[styles.sectionBar, styles.sectionBarRecords]} />
            <Text style={styles.subtitle}>记录</Text>
          </View>
          <Text style={styles.description}>
            每一条都是聊天里你自己说的。删掉就从你的记录里消失，所以先确认一次。
          </Text>
          {logs.map((log) => (
            <RecordRow
              accent="behavior"
              body={log.note}
              deleting={deleting}
              key={log.id}
              label={log.label}
              onCancel={() => setPending(null)}
              onConfirm={() => void remove({ kind: 'behavior', id: log.id })}
              onRequest={() => setPending({ kind: 'behavior', id: log.id })}
              pending={
                pending?.kind === 'behavior' && pending.id === log.id
              }
              when={formatWhen(log.occurredAt)}
            />
          ))}
          {events.map((event) => (
            <RecordRow
              accent="belief"
              body={`「${event.quote}」`}
              deleting={deleting}
              key={event.id}
              label={event.label}
              onCancel={() => setPending(null)}
              onConfirm={() => void remove({ kind: 'belief', id: event.id })}
              onRequest={() => setPending({ kind: 'belief', id: event.id })}
              pending={pending?.kind === 'belief' && pending.id === event.id}
              when={formatWhen(event.detectedAt)}
            />
          ))}
        </View>
      ) : null}

      {mode === 'beliefs' ? null : mode === 'records' ? (
        <Text style={styles.noteText}>
          这里只列你自己说过的事，不是量表分数。删掉就从你的记录里消失，删之前会再问一次。
        </Text>
      ) : (
        <Text style={styles.noteText}>
          这些是聊天里你自己说的事，不是量表分数，也不用来给你打分。不想要的那条，上面可以直接删。
        </Text>
      )}
    </View>
  );
}

type RecordRowProps = {
  accent: 'behavior' | 'belief';
  label: string;
  body: string | null;
  when: string;
  pending: boolean;
  deleting: boolean;
  onRequest: () => void;
  onCancel: () => void;
  onConfirm: () => void;
};

function RecordRow({
  accent,
  label,
  body,
  when,
  pending,
  deleting,
  onRequest,
  onCancel,
  onConfirm,
}: RecordRowProps) {
  return (
    <View
      style={[
        styles.recordRow,
        accent === 'behavior' ? styles.recordRowBehavior : styles.recordRowBelief,
      ]}
    >
      <View style={styles.beliefHead}>
        <Text style={styles.rowLabel}>{label}</Text>
        <Text style={styles.rowTag}>{when}</Text>
      </View>
      {body !== null && body.length > 0 ? (
        <Text style={styles.quoteText}>{body}</Text>
      ) : null}
      {pending ? (
        <View style={styles.confirmRow}>
          <Text style={styles.confirmText}>删掉这条？</Text>
          <Pressable
            accessibilityLabel="确认删除"
            disabled={deleting}
            onPress={onConfirm}
            style={styles.confirmDelete}
          >
            <Text style={styles.confirmDeleteText}>
              {deleting ? '删除中…' : '删掉'}
            </Text>
          </Pressable>
          <Pressable
            accessibilityLabel="取消删除"
            disabled={deleting}
            onPress={onCancel}
            style={styles.confirmCancel}
          >
            <Text style={styles.confirmCancelText}>取消</Text>
          </Pressable>
        </View>
      ) : (
        <Pressable
          accessibilityLabel={`删除${label}`}
          onPress={onRequest}
          style={styles.deleteLink}
        >
          <Text style={styles.deleteLinkText}>删除</Text>
        </Pressable>
      )}
    </View>
  );
}

function BeliefRow({ belief }: { belief: HypoglycemiaBeliefNode }) {
  return (
    <View style={styles.beliefRow}>
      <View style={styles.beliefHead}>
        <Text style={styles.rowLabel}>{belief.label}</Text>
        <Text style={styles.rowTag}>
          {belief.resolved ? '最近松动了' : `提到 ${belief.mentions} 次`}
        </Text>
      </View>
      <Text style={styles.quoteText}>「{belief.latestQuote}」</Text>
    </View>
  );
}

function isEmpty(summary: FearSummary): boolean {
  return summary.behaviorTotal === 0 && summary.beliefs.length === 0;
}

function behaviorLabel(kind: string): string {
  return BEHAVIOR_LABELS[kind] ?? kind;
}

function windowDays(summary: FearSummary): number {
  const start = new Date(summary.windowStart).getTime();
  const end = new Date(summary.windowEnd).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) return 28;
  return Math.round((end - start) / (24 * 60 * 60 * 1000));
}

/**
 * 用一句话说明「最近提到过什么」，但不报次数。
 *
 * 大号「5 次」读起来像审计：本来只是记录，患者会当成「我又多测了」的证据。
 * 所以这里只说有哪几类事，次数留在医生视图和原始记录里。
 */
function mentionSentence(summary: FearSummary): string {
  const kinds = BEHAVIOR_ORDER.filter(
    (kind) => (summary.behaviorCounts[kind] ?? 0) > 0,
  ).map(behaviorLabel);
  if (kinds.length === 0) {
    return `最近 ${windowDays(summary)} 天，你在聊天里提到过一些和担心低血糖有关的事。`;
  }
  return `最近 ${windowDays(summary)} 天，你在聊天里提到过${kinds.join('、')}。`;
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (part: number): string => String(part).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error && reason.message.length > 0
    ? reason.message
    : '读取失败，请稍后再试';
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    marginBottom: spacing.md,
    padding: spacing.md,
  },
  kickerPill: {
    alignSelf: 'flex-start',
    backgroundColor: colors.primarySoft,
    borderRadius: radii.pill,
    marginBottom: spacing.xs,
    paddingHorizontal: spacing.xs,
    paddingVertical: 3,
  },
  kicker: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
    letterSpacing: 0.8,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 18,
    marginBottom: spacing.xs,
  },
  sectionHead: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
    marginBottom: spacing.xs,
    marginTop: spacing.md,
  },
  sectionBar: {
    borderRadius: radii.pill,
    height: 14,
    width: 3,
  },
  sectionBarBelief: { backgroundColor: '#E0A43C' },
  sectionBarRecords: { backgroundColor: colors.primary },
  subtitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  description: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 21,
  },
  loadingRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
    paddingVertical: spacing.sm,
  },
  loadingText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
  },
  errorText: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 20,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xxs,
  },
  chip: {
    alignItems: 'center',
    backgroundColor: colors.primarySoft,
    borderRadius: radii.sm,
    flexDirection: 'row',
    gap: 6,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  rowDot: {
    backgroundColor: colors.primary,
    borderRadius: radii.pill,
    height: 6,
    width: 6,
  },
  rowLabel: {
    color: colors.ink,
    flexShrink: 1,
    fontFamily: typefaces.sans,
    fontSize: 15,
  },
  sectionAction: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  rowTag: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  beliefBlock: {
    backgroundColor: '#FBF4E8',
    borderRadius: radii.md,
    marginTop: spacing.sm,
    padding: spacing.sm,
  },
  recordsBlock: {
    borderTopColor: colors.line,
    borderTopWidth: StyleSheet.hairlineWidth,
    marginTop: spacing.sm,
  },
  beliefRow: {
    borderBottomColor: 'rgba(224, 164, 60, 0.24)',
    borderBottomWidth: StyleSheet.hairlineWidth,
    paddingVertical: spacing.xs,
  },
  recordRow: {
    borderRadius: radii.sm,
    borderWidth: 1,
    marginTop: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  recordRowBehavior: {
    backgroundColor: colors.paper,
    borderColor: '#CBEAE2',
    borderLeftColor: colors.primary,
    borderLeftWidth: 3,
  },
  recordRowBelief: {
    backgroundColor: '#FBF4E8',
    borderColor: '#F0DCBC',
    borderLeftColor: '#E0A43C',
    borderLeftWidth: 3,
  },
  beliefHead: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  quoteText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 19,
    marginTop: spacing.xxs,
  },
  deleteLink: {
    alignSelf: 'flex-start',
    paddingVertical: spacing.xxs,
  },
  deleteLinkText: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  confirmRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
    marginTop: spacing.xxs,
  },
  confirmText: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  confirmDelete: {
    backgroundColor: colors.danger,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xxs,
  },
  confirmDeleteText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  confirmCancel: {
    borderColor: colors.line,
    borderRadius: radii.sm,
    borderWidth: 1,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xxs,
  },
  confirmCancelText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  noteText: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
    marginTop: spacing.sm,
  },
});
