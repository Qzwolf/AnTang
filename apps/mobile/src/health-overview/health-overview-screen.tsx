import { randomUUID } from 'expo-crypto';
import { LinearGradient } from 'expo-linear-gradient';
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import Svg, { Circle, Line, Polyline } from 'react-native-svg';

import { ApiError, errorMessage, type ApiClient } from '../api/client';
import type {
  HealthFact,
  HeartRateTrendPoint,
  HealthProfileChange,
  HealthProfile,
  PersonalProfileField,
  PersonalProfileUnit,
  PersonalProfile,
  SleepStage,
  WearableLatest,
} from '../api/types';
import {
  HealthFactEditor,
  PersonalValueEditor,
  healthFactTypeLabels,
  type HealthFactValue,
  type PersonalValue,
} from '../health-profile/health-profile-inputs';
import { LiveHeartRatePanel } from '../live-heart-rate/LiveHeartRatePanel';
import { colors as brandColors, typefaces } from '../ui/theme';

export type HealthOverviewScreenProps = {
  api: Pick<
    ApiClient,
    | 'changeHealthProfile'
    | 'deleteBeliefEvent'
    | 'deleteFearBehaviorLog'
    | 'getBeliefEvents'
    | 'getFearBehaviorLogs'
    | 'getFearSummary'
    | 'getHealthProfile'
    | 'getPsychosocialAssessment'
    | 'getPsychosocialInstrument'
    | 'recordPsychosocialAssessment'
  >;
  onClose: () => void;
  onProfileChanged?: () => void;
  onSyncWearable?: () => Promise<void>;
  wearableSyncCompletedAt?: string | null;
};

const colors = {
  ink: brandColors.ink,
  mutedInk: brandColors.muted,
  paper: brandColors.background,
  card: brandColors.paper,
  pine: brandColors.primary,
  moss: '#7F9F89',
  sage: brandColors.primarySoft,
  clay: brandColors.coral,
  clayPale: brandColors.coralSoft,
  gold: '#C5A45F',
  line: brandColors.line,
  error: brandColors.danger,
  errorPale: brandColors.dangerSoft,
};

const wearableOrder: WearableLatest['recordType'][] = [
  'heart_rate',
  'oxygen_saturation',
  'sleep',
  'resting_heart_rate',
  'steps',
  'distance',
  'exercise',
  'respiratory_rate',
  'weight',
  'elevation_gained',
];

const temporalLabels: Record<HealthFact['temporalStatus'], string> = {
  current: '当前',
  past: '既往',
  unknown: '时间未注明',
};

const stageLabels: Record<SleepStage['stage'], string> = {
  unknown: '未分类',
  awake: '清醒',
  sleeping: '睡眠',
  out_of_bed: '离床',
  awake_in_bed: '卧床清醒',
  light: '浅睡',
  deep: '深睡',
  rem: '快速眼动',
};

const stageColors: Record<SleepStage['stage'], string> = {
  unknown: '#B9BDB7',
  awake: '#E6A67E',
  sleeping: '#91A99D',
  out_of_bed: '#D6C6A9',
  awake_in_bed: '#D6A18A',
  light: '#90AD9C',
  deep: '#315E52',
  rem: '#8B82A8',
};

function formatNumber(value: number, maximumFractionDigits = 1): string {
  return new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits,
  }).format(value);
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function formatDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number);
  return `${year}年${month}月${day}日`;
}

function formatDuration(milliseconds: number): string {
  const minutes = Math.round(milliseconds / 60_000);
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours === 0) return `${remainingMinutes}分钟`;
  if (remainingMinutes === 0) return `${hours}小时`;
  return `${hours}小时${remainingMinutes}分钟`;
}

/**
 * 观测来源包名 → 界面上给患者看的名称。
 *
 * 之前只判断 Zepp，其余一律显示「Health Connect」，于是 Gadgetbridge 直读
 * 手环进来的数据也被标成 Health Connect——华为机型根本没有这条通路，标注是错的。
 * 未知来源直接显示包名，不猜。
 */
const SOURCE_LABELS: Record<string, string> = {
  'com.huami.watch.hmwatchmanager': 'Zepp · Health Connect',
  'nodomain.freeyourgadget.gadgetbridge': 'Gadgetbridge',
};

export function sourceLabel(sourcePackage: string): string {
  return SOURCE_LABELS[sourcePackage] ?? sourcePackage;
}

type ProfileRow = {
  fieldName: PersonalProfileField;
  label: string;
  value: string | null;
  inputValue: string;
  unit?: PersonalProfileUnit;
  note?: string;
};

function profileRows(profile: PersonalProfile): ProfileRow[] {
  return [
    {
      fieldName: 'sex',
      label: '性别',
      value: profile.sex,
      inputValue: profile.sex ?? '',
    },
    {
      fieldName: 'age_years',
      label: '年龄',
      value: profile.ageYears === null ? null : `${profile.ageYears} 岁`,
      inputValue: profile.ageYears === null ? '' : String(profile.ageYears),
      unit: 'years',
      note:
        profile.ageAsOfDate === null
          ? undefined
          : `截至 ${formatDate(profile.ageAsOfDate)}`,
    },
    {
      fieldName: 'height_cm',
      label: '身高',
      value:
        profile.heightCm === null
          ? null
          : `${formatNumber(profile.heightCm)} cm`,
      inputValue: profile.heightCm === null ? '' : String(profile.heightCm),
      unit: 'cm',
    },
    {
      fieldName: 'weight_kg',
      label: '档案体重',
      value:
        profile.weightKg === null
          ? null
          : `${formatNumber(profile.weightKg)} kg`,
      inputValue: profile.weightKg === null ? '' : String(profile.weightKg),
      unit: 'kg',
    },
    {
      fieldName: 'resident_area',
      label: '常驻地区',
      value: profile.residentArea,
      inputValue: profile.residentArea ?? '',
    },
    {
      fieldName: 'schedule_type',
      label: '日常作息',
      value: profile.scheduleType,
      inputValue: profile.scheduleType ?? '',
    },
    {
      fieldName: 'occupation',
      label: '职业',
      value: profile.occupation,
      inputValue: profile.occupation ?? '',
    },
  ];
}

type ProfileEditor =
  | { kind: 'personal'; fieldName: PersonalProfileField }
  | { kind: 'personal_clear'; fieldName: PersonalProfileField }
  | { kind: 'fact_add' }
  | { kind: 'fact'; factId: string }
  | { kind: 'fact_retract'; factId: string };

type WithoutClientAction<T> = T extends unknown
  ? Omit<T, 'clientActionId'>
  : never;
type HealthProfileChangeDraft = WithoutClientAction<HealthProfileChange>;
type PendingProfileChange = {
  key: string;
  clientActionId: string;
  change: HealthProfileChangeDraft;
  applied: boolean;
};

function profileChangeIntentKey(change: HealthProfileChangeDraft): string {
  const { expectedRevision: _expectedRevision, ...intent } = change;
  return JSON.stringify(intent);
}

function effectivePeriod(fact: HealthFact): string | null {
  if (fact.effectiveStart !== null && fact.effectiveEnd !== null) {
    return `${formatDate(fact.effectiveStart)} — ${formatDate(fact.effectiveEnd)}`;
  }
  if (fact.effectiveStart !== null) {
    return `${formatDate(fact.effectiveStart)}起`;
  }
  if (fact.effectiveEnd !== null) {
    return `截至 ${formatDate(fact.effectiveEnd)}`;
  }
  return null;
}

export function HealthOverviewScreen({
  api,
  onClose,
  onProfileChanged,
  onSyncWearable,
  wearableSyncCompletedAt,
}: HealthOverviewScreenProps) {
  const [profile, setProfile] = useState<HealthProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [editor, setEditor] = useState<ProfileEditor | null>(null);
  const [mutating, setMutating] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [savedRefreshError, setSavedRefreshError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const pendingChangeRef = useRef<PendingProfileChange | null>(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    requestRef.current = controller;
    let active = true;
    void api
      .getHealthProfile(controller.signal)
      .then(
        (value) => {
          if (active) {
            setProfile(value);
            setError(null);
          }
        },
        (reason) => {
          if (active && !controller.signal.aborted) {
            setError(errorMessage(reason));
          }
        },
      )
      .finally(() => {
        if (!active) return;
        if (requestRef.current === controller) requestRef.current = null;
        setLoading(false);
      });

    return () => {
      active = false;
      mountedRef.current = false;
      const request = requestRef.current;
      requestRef.current = null;
      request?.abort();
      if (request !== controller) controller.abort();
    };
  }, [api, loadAttempt, wearableSyncCompletedAt]);

  const orderedWearable = [...(profile?.wearableLatest ?? [])].sort(
    (left, right) =>
      wearableOrder.indexOf(left.recordType) -
      wearableOrder.indexOf(right.recordType),
  );
  const latestObservation = orderedWearable.reduce<string | null>(
    (latest, item) =>
      latest === null || Date.parse(item.observedAt) > Date.parse(latest)
        ? item.observedAt
        : latest,
    null,
  );

  function requestRefresh() {
    if (requestRef.current !== null) return;
    if (profile === null) {
      setError(null);
      setLoading(true);
      setLoadAttempt((attempt) => attempt + 1);
      return;
    }
    void refreshProfile();
  }

  async function refreshProfile(syncWearable = false) {
    if (requestRef.current !== null) return;
    const controller = new AbortController();
    requestRef.current = controller;
    setError(null);
    setRefreshing(true);
    try {
      if (syncWearable) await onSyncWearable?.();
      if (controller.signal.aborted) return;
      const value = await api.getHealthProfile(controller.signal);
      if (mountedRef.current && requestRef.current === controller) {
        setProfile(value);
      }
    } catch (reason) {
      if (
        mountedRef.current &&
        requestRef.current === controller &&
        !controller.signal.aborted
      ) {
        setError(errorMessage(reason));
      }
    } finally {
      if (mountedRef.current) {
        if (requestRef.current === controller) requestRef.current = null;
        setRefreshing(false);
      }
    }
  }

  async function refreshAppliedChange(pending: PendingProfileChange) {
    try {
      const latest = await api.getHealthProfile();
      if (mountedRef.current && pendingChangeRef.current === pending) {
        pendingChangeRef.current = null;
        setProfile(latest);
        setEditor(null);
        setSavedRefreshError(null);
        onProfileChanged?.();
      }
    } catch (reason) {
      if (mountedRef.current && pendingChangeRef.current === pending) {
        setSavedRefreshError(errorMessage(reason));
      }
    }
  }

  async function retrySavedProfileRefresh() {
    const pending = pendingChangeRef.current;
    if (mutating || pending === null || !pending.applied) return;
    setMutating(true);
    setSavedRefreshError(null);
    try {
      await refreshAppliedChange(pending);
    } finally {
      if (mountedRef.current) setMutating(false);
    }
  }

  async function submitChange(change: HealthProfileChangeDraft) {
    if (mutating) return;
    const key = profileChangeIntentKey(change);
    let pending = pendingChangeRef.current;
    if (pending === null || pending.key !== key) {
      pending = {
        key,
        clientActionId: randomUUID(),
        change,
        applied: false,
      };
      pendingChangeRef.current = pending;
    }

    setMutating(true);
    setMutationError(null);
    setSavedRefreshError(null);
    try {
      if (!pending.applied) {
        try {
          await api.changeHealthProfile({
            ...pending.change,
            clientActionId: pending.clientActionId,
          } as HealthProfileChange);
          pending.applied = true;
        } catch (reason) {
          if (reason instanceof ApiError && reason.status === 409) {
            if (pendingChangeRef.current === pending) {
              pendingChangeRef.current = null;
            }
            try {
              const latest = await api.getHealthProfile();
              if (mountedRef.current) {
                setProfile(latest);
                setMutationError('档案刚更新，已刷新，请确认后再保存一次。');
                onProfileChanged?.();
              }
            } catch (refreshReason) {
              if (mountedRef.current) {
                setMutationError(errorMessage(refreshReason));
              }
            }
            return;
          }

          if (
            pendingChangeRef.current === pending &&
            reason instanceof ApiError &&
            reason.status >= 400 &&
            reason.status < 500
          ) {
            pendingChangeRef.current = null;
          }
          if (mountedRef.current) setMutationError(errorMessage(reason));
          return;
        }
      }

      await refreshAppliedChange(pending);
    } finally {
      if (mountedRef.current) setMutating(false);
    }
  }

  function setPersonalValue(
    fieldName: PersonalProfileField,
    expectedRevision: number,
    value: PersonalValue,
  ) {
    void submitChange({
      expectedRevision,
      targetType: 'personal_profile',
      operation: 'set',
      fieldName,
      ...value,
    });
  }

  function clearPersonalValue(
    fieldName: PersonalProfileField,
    expectedRevision: number,
  ) {
    void submitChange({
      expectedRevision,
      targetType: 'personal_profile',
      operation: 'clear',
      fieldName,
    });
  }

  function saveFact(value: HealthFactValue, existing?: HealthFact) {
    void submitChange(
      existing === undefined
        ? {
            expectedRevision: null,
            targetType: 'health_fact',
            operation: 'add',
            ...value,
          }
        : {
            expectedRevision: existing.revision,
            targetType: 'health_fact',
            operation: 'update',
            targetId: existing.id,
            ...value,
          },
    );
  }

  function retractFact(fact: HealthFact) {
    void submitChange({
      expectedRevision: fact.revision,
      targetType: 'health_fact',
      operation: 'retract',
      targetId: fact.id,
      factType: fact.factType,
    });
  }

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.content}
        contentInsetAdjustmentBehavior="automatic"
        refreshControl={
          <RefreshControl
            colors={[colors.pine]}
            enabled={profile !== null}
            onRefresh={() => void refreshProfile(true)}
            refreshing={refreshing}
            tintColor={colors.pine}
          />
        }
        showsVerticalScrollIndicator={false}
      >
        <LinearGradient
          colors={['#133A30', '#245347', '#486955']}
          end={{ x: 1, y: 1 }}
          start={{ x: 0, y: 0 }}
          style={styles.hero}
        >
          <View pointerEvents="none" style={styles.heroOrbLarge} />
          <View pointerEvents="none" style={styles.heroOrbSmall} />
          <View style={styles.heroTopRow}>
            <Text style={styles.eyebrow}>健康记录</Text>
            <View style={styles.heroActions}>
              <Pressable
                accessibilityLabel="同步 Health Connect 数据"
                accessibilityRole="button"
                accessibilityState={{
                  busy: refreshing,
                  disabled: loading || refreshing,
                }}
                disabled={loading || refreshing}
                hitSlop={4}
                onPress={() => void refreshProfile(true)}
                style={({ pressed }) => [
                  styles.heroAction,
                  pressed && styles.heroActionPressed,
                ]}
              >
                {refreshing ? (
                  <ActivityIndicator color="#FFFDF7" size="small" />
                ) : (
                  <Text style={styles.heroActionText}>同步</Text>
                )}
              </Pressable>
              <Pressable
                accessibilityLabel="关闭我的健康"
                accessibilityRole="button"
                hitSlop={4}
                onPress={onClose}
                style={({ pressed }) => [
                  styles.closeButton,
                  pressed && styles.heroActionPressed,
                ]}
              >
                <Text style={styles.closeText}>×</Text>
              </Pressable>
            </View>
          </View>

          <Text selectable style={styles.heroTitle}>
            我的健康
          </Text>
          <Text style={styles.heroDescription}>
            已写入的档案，以及设备同步到服务器的最近记录。点“同步”可读取手机中的新数据。
          </Text>
          <View style={styles.heroMetaRow}>
            <View style={styles.historyPill}>
              <View style={styles.historyDot} />
              <Text style={styles.historyPillText}>历史记录 · 非实时</Text>
            </View>
            {latestObservation === null ? null : (
              <Text selectable style={styles.latestText}>
                最近观测 {formatDateTime(latestObservation)}
              </Text>
            )}
            {wearableSyncCompletedAt === null || wearableSyncCompletedAt === undefined ? null : (
              <Text selectable style={styles.latestText}>
                上次检查 Health Connect {formatDateTime(wearableSyncCompletedAt)}
              </Text>
            )}
          </View>
        </LinearGradient>

        {loading && profile === null ? (
          <LoadingState />
        ) : profile === null ? (
          <ErrorState
            error={error ?? '暂时无法读取健康记录'}
            onRetry={requestRefresh}
          />
        ) : (
          <>
            {error === null ? null : (
              <InlineError error={error} onRetry={requestRefresh} />
            )}

            {mutationError === null ? null : (
              <View accessibilityRole="alert" style={styles.mutationError}>
                <Text selectable style={styles.mutationErrorText}>
                  {mutationError}
                </Text>
              </View>
            )}

            {savedRefreshError === null ? null : (
              <View accessibilityRole="alert" style={styles.savedRefreshNotice}>
                <View style={styles.savedRefreshCopy}>
                  <Text style={styles.savedRefreshTitle}>修改已经保存</Text>
                  <Text selectable style={styles.savedRefreshText}>
                    最新档案暂时没有加载出来：{savedRefreshError}
                  </Text>
                </View>
                <Pressable
                  accessibilityLabel="重新加载已保存档案"
                  accessibilityRole="button"
                  disabled={mutating}
                  onPress={() => void retrySavedProfileRefresh()}
                  style={({ pressed }) => [
                    styles.savedRefreshButton,
                    mutating && styles.buttonDisabled,
                    pressed && !mutating && styles.actionPressed,
                  ]}
                >
                  {mutating ? (
                    <ActivityIndicator color={colors.card} size="small" />
                  ) : (
                    <Text style={styles.savedRefreshButtonText}>重新加载</Text>
                  )}
                </Pressable>
              </View>
            )}

            <ProfileSection
              editor={editor}
              mutating={mutating}
              onCancel={() => setEditor(null)}
              onClear={(fieldName) =>
                clearPersonalValue(fieldName, profile.personalProfile.revision)
              }
              onEdit={(fieldName) =>
                setEditor({ kind: 'personal', fieldName })
              }
              onRequestClear={(fieldName) =>
                setEditor({ kind: 'personal_clear', fieldName })
              }
              onSave={(fieldName, value) =>
                setPersonalValue(
                  fieldName,
                  profile.personalProfile.revision,
                  value,
                )
              }
              profile={profile.personalProfile}
            />
            <FactsSection
              editor={editor}
              facts={profile.healthFacts}
              mutating={mutating}
              onAdd={() => setEditor({ kind: 'fact_add' })}
              onCancel={() => setEditor(null)}
              onEdit={(factId) => setEditor({ kind: 'fact', factId })}
              onRequestRetract={(factId) =>
                setEditor({ kind: 'fact_retract', factId })
              }
              onRetract={retractFact}
              onSave={saveFact}
            />

            <LiveHeartRatePanel />

            {/* 沟通方式与行为/念头已搬到「心理」tab；健康档案只留资料、事实与设备观测。 */}

            <SectionHeader
              description="心率展示最近 6 小时趋势，其他项目为服务器保存的最近观测。"
              kicker="设备数据"
              title="最近观测"
            />
            {orderedWearable.length === 0 ? (
              <EmptyState
                description="完成 Health Connect 同步后，最近的心率、睡眠、步数等记录会出现在这里。"
                title="还没有设备记录"
                visual="bars"
              />
            ) : (
              <View style={styles.metricsGrid}>
                {orderedWearable.map((observation) => (
                  <WearableCard
                    heartRateTrend={profile.heartRateTrend}
                    key={observation.recordType}
                    observation={observation}
                  />
                ))}
              </View>
            )}

            <View style={styles.noteCard}>
              <View style={styles.noteRule} />
              <Text style={styles.noteTitle}>关于这些数据</Text>
              <Text style={styles.noteText}>
                这里展示的是已写入档案和历史观测，不能替代医疗设备，也不用于紧急判断。
              </Text>
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

function SectionHeader({
  description,
  kicker,
  title,
}: {
  description: string;
  kicker: string;
  title: string;
}) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionKicker}>{kicker}</Text>
      <Text style={styles.sectionTitle}>{title}</Text>
      <Text style={styles.sectionDescription}>{description}</Text>
    </View>
  );
}

function MetricCard({
  accent,
  children,
  hint,
  observedAt,
  sourcePackage,
  title,
  unit,
  value,
  wide = false,
}: {
  accent: string;
  children?: ReactNode;
  hint: string;
  observedAt: string;
  sourcePackage: string;
  title: string;
  unit?: string;
  value: string;
  wide?: boolean;
}) {
  const compactValue = value.length > 8;
  return (
    <View style={[styles.metricCard, wide && styles.metricCardWide]}>
      <View style={styles.metricTopRow}>
        <Text style={styles.metricLabel}>{title}</Text>
        <View style={[styles.metricAccent, { backgroundColor: accent }]} />
      </View>
      <View style={styles.metricValueRow}>
        <Text
          selectable
          style={[styles.metricValue, compactValue && styles.metricValueCompact]}
        >
          {value}
        </Text>
        {unit === undefined ? null : (
          <Text style={styles.metricUnit}>{unit}</Text>
        )}
      </View>
      <Text style={styles.metricHint}>{hint}</Text>
      {children}
      <View style={styles.metricFooter}>
        <Text selectable style={styles.metricTime}>
          观测于 {formatDateTime(observedAt)}
        </Text>
        <Text style={styles.metricSource}>{sourceLabel(sourcePackage)}</Text>
      </View>
    </View>
  );
}

export function WearableCard({
  heartRateTrend,
  observation,
}: {
  heartRateTrend: HeartRateTrendPoint[];
  observation: WearableLatest;
}) {
  switch (observation.recordType) {
    case 'heart_rate': {
      const samples = observation.data.samples;
      const latest = samples[samples.length - 1];
      const values = heartRateTrend.map((item) => item.beatsPerMinute);
      const minimum = values.length === 0 ? null : Math.min(...values);
      const maximum = values.length === 0 ? null : Math.max(...values);
      return (
        <MetricCard
          accent={colors.clay}
          hint={minimum === null || maximum === null
            ? `近 6 小时无新采样 · 末次采样 ${formatDateTime(latest.time)}`
            : `近 6 小时 ${heartRateTrend.length} 个采样点 · 范围 ${minimum}–${maximum} 次/分 · 末次采样 ${formatDateTime(latest.time)}`}
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="心率"
          unit="次/分"
          value={String(latest.beatsPerMinute)}
          wide
        >
          {heartRateTrend.length >= 2 ? <HeartRateTrend points={heartRateTrend} /> : null}
        </MetricCard>
      );
    }
    case 'oxygen_saturation':
      return (
        <MetricCard
          accent="#6F91A3"
          hint="最近一条血氧记录"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="血氧"
          unit="%"
          value={formatNumber(observation.data.percentage)}
        />
      );
    case 'sleep': {
      const duration = observation.data.stages.reduce(
        (total, stage) =>
          total + Date.parse(stage.endTime) - Date.parse(stage.startTime),
        0,
      );
      return (
        <MetricCard
          accent="#706A91"
          hint={
            observation.data.stages.length === 0
              ? '这条记录没有睡眠阶段明细'
              : `${observation.data.stages.length} 段合计记录时长，不等同睡眠总时长`
          }
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="睡眠阶段"
          value={
            observation.data.stages.length === 0
              ? '暂无明细'
              : formatDuration(duration)
          }
          wide
        >
          <SleepBand stages={observation.data.stages} />
        </MetricCard>
      );
    }
    case 'resting_heart_rate':
      return (
        <MetricCard
          accent="#B96758"
          hint="最近一条静息心率记录"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="静息心率"
          unit="次/分"
          value={String(observation.data.beatsPerMinute)}
        />
      );
    case 'steps':
      return (
        <MetricCard
          accent={colors.gold}
          hint="单条步数记录，并非今日总量"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="步数片段"
          unit="步"
          value={formatNumber(observation.data.count, 0)}
        />
      );
    case 'distance': {
      const kilometers = observation.data.meters >= 1_000;
      return (
        <MetricCard
          accent="#668B78"
          hint="最近一条距离记录"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="距离片段"
          unit={kilometers ? 'km' : 'm'}
          value={formatNumber(
            kilometers
              ? observation.data.meters / 1_000
              : observation.data.meters,
          )}
        />
      );
    }
    case 'exercise':
      return (
        <MetricCard
          accent="#CC835D"
          hint="最近一条运动记录"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="运动"
          value={observation.data.title ?? '已记录'}
        />
      );
    case 'respiratory_rate':
      return (
        <MetricCard
          accent="#61919A"
          hint="最近一条呼吸频率记录"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="呼吸频率"
          unit="次/分"
          value={formatNumber(observation.data.breathsPerMinute)}
        />
      );
    case 'weight':
      return (
        <MetricCard
          accent="#8D7C65"
          hint="设备记录，与基础档案分开保存"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="设备体重"
          unit="kg"
          value={formatNumber(observation.data.kilograms)}
        />
      );
    case 'elevation_gained':
      return (
        <MetricCard
          accent="#7B8B60"
          hint="最近一条爬升记录"
          observedAt={observation.observedAt}
          sourcePackage={observation.sourcePackage}
          title="爬升片段"
          unit="m"
          value={formatNumber(observation.data.meters)}
        />
      );
  }
}

function HeartRateTrend({ points }: { points: HeartRateTrendPoint[] }) {
  const values = points.map((point) => point.beatsPerMinute);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const firstTime = Date.parse(points[0].observedAt);
  const lastTime = Date.parse(points[points.length - 1].observedAt);
  const timeRange = Math.max(1, lastTime - firstTime);
  const valueRange = Math.max(1, maximum - minimum);
  const path = points
    .map((point) => {
      const x = 8 + ((Date.parse(point.observedAt) - firstTime) / timeRange) * 284;
      const y = 58 - ((point.beatsPerMinute - minimum) / valueRange) * 48;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const lastCoordinates = path.split(' ').at(-1)?.split(',') ?? ['292', '34'];
  return (
    <View
      accessibilityLabel={`最近 6 小时心率趋势：共 ${points.length} 个采样点，范围 ${minimum}–${maximum} 次/分`}
      accessible
      style={styles.heartTrend}
    >
      <Svg height={68} viewBox="0 0 300 68" width="100%">
        <Line stroke="#ECE6DC" strokeWidth={1} x1={8} x2={292} y1={10} y2={10} />
        <Line stroke="#ECE6DC" strokeWidth={1} x1={8} x2={292} y1={34} y2={34} />
        <Line stroke="#ECE6DC" strokeWidth={1} x1={8} x2={292} y1={58} y2={58} />
        {points.length > 1 ? (
          <Polyline
            fill="none"
            points={path}
            stroke={colors.clay}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={3}
          />
        ) : null}
        <Circle
          cx={Number(lastCoordinates[0])}
          cy={Number(lastCoordinates[1])}
          fill={colors.card}
          r={4}
          stroke={colors.clay}
          strokeWidth={3}
        />
      </Svg>
      <View style={styles.heartTrendLabels}>
        <Text style={styles.heartTrendLabel}>{formatTrendTime(points[0].observedAt)}</Text>
        <Text style={styles.heartTrendRange}>近 6 小时</Text>
        <Text style={styles.heartTrendLabel}>{formatTrendTime(points[points.length - 1].observedAt)}</Text>
      </View>
    </View>
  );
}

function formatTrendTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function SleepBand({ stages }: { stages: SleepStage[] }) {
  if (stages.length === 0) return null;
  const uniqueStages = [...new Set(stages.map((stage) => stage.stage))];
  const segments: (
    | { key: string; duration: number; stage: SleepStage['stage'] }
    | { key: string; duration: number; stage: null }
  )[] = [];
  let cursor = Date.parse(stages[0].startTime);
  for (const [index, stage] of stages.entries()) {
    const start = Date.parse(stage.startTime);
    const end = Date.parse(stage.endTime);
    if (start > cursor) {
      segments.push({
        key: `gap-${index}`,
        duration: start - cursor,
        stage: null,
      });
    }
    segments.push({
      key: `${stage.startTime}-${index}`,
      duration: end - start,
      stage: stage.stage,
    });
    cursor = end;
  }
  const hasGap = segments.some((segment) => segment.stage === null);
  return (
    <View style={styles.sleepVisual}>
      <Text style={styles.sleepAxisLabel}>阶段时间轴</Text>
      <View
        accessibilityLabel={`睡眠阶段时间轴：${stages
          .map((stage) => stageLabels[stage.stage])
          .join('、')}${hasGap ? '，包含未记录间隔' : ''}`}
        accessible
        style={styles.sleepBand}
      >
        {segments.map((segment) => (
          <View
            key={segment.key}
            style={{
              flexBasis: 0,
              flexGrow: segment.duration,
              backgroundColor:
                segment.stage === null
                  ? '#E8E5DE'
                  : stageColors[segment.stage],
            }}
          />
        ))}
      </View>
      <View style={styles.sleepLegend}>
        {uniqueStages.map((stage) => (
          <View key={stage} style={styles.sleepLegendItem}>
            <View
              style={[
                styles.sleepLegendDot,
                { backgroundColor: stageColors[stage] },
              ]}
            />
            <Text style={styles.sleepLegendText}>{stageLabels[stage]}</Text>
          </View>
        ))}
        {hasGap ? (
          <View style={styles.sleepLegendItem}>
            <View
              style={[styles.sleepLegendDot, { backgroundColor: '#E8E5DE' }]}
            />
            <Text style={styles.sleepLegendText}>未记录</Text>
          </View>
        ) : null}
      </View>
    </View>
  );
}

function ProfileSection({
  editor,
  mutating,
  onCancel,
  onClear,
  onEdit,
  onRequestClear,
  onSave,
  profile,
}: {
  editor: ProfileEditor | null;
  mutating: boolean;
  onCancel: () => void;
  onClear: (fieldName: PersonalProfileField) => void;
  onEdit: (fieldName: PersonalProfileField) => void;
  onRequestClear: (fieldName: PersonalProfileField) => void;
  onSave: (fieldName: PersonalProfileField, value: PersonalValue) => void;
  profile: PersonalProfile;
}) {
  const rows = profileRows(profile);
  return (
    <>
      <SectionHeader
        description="可以直接填写或修改；设备同步的原始数据不会在这里被改动。"
        kicker="个人档案"
        title="关于我"
      />
      <View style={styles.profileCard}>
        {rows.map((row, index) => {
          const editing =
            editor?.kind === 'personal' && editor.fieldName === row.fieldName;
          const clearing =
            editor?.kind === 'personal_clear' &&
            editor.fieldName === row.fieldName;
          return (
            <View key={row.fieldName}>
              <View style={styles.profileRow}>
                <View style={styles.profileLabelGroup}>
                  <Text style={styles.profileLabel}>{row.label}</Text>
                  <Text selectable style={styles.profileValue}>
                    {row.value ?? '未填写'}
                  </Text>
                  {row.note === undefined ? null : (
                    <Text selectable style={styles.profileNote}>
                      {row.note}
                    </Text>
                  )}
                </View>
                <View style={styles.rowActions}>
                  {row.value === null ? null : (
                    <Pressable
                      accessibilityLabel={`清除${row.label}`}
                      accessibilityRole="button"
                      disabled={mutating}
                      onPress={() => onRequestClear(row.fieldName)}
                      style={({ pressed }) => [
                        styles.textAction,
                        pressed && styles.actionPressed,
                      ]}
                    >
                      <Text style={styles.destructiveActionText}>清除</Text>
                    </Pressable>
                  )}
                  <Pressable
                    accessibilityLabel={`${row.value === null ? '填写' : '编辑'}${row.label}`}
                    accessibilityRole="button"
                    disabled={mutating}
                    onPress={() => onEdit(row.fieldName)}
                    style={({ pressed }) => [
                      styles.textAction,
                      pressed && styles.actionPressed,
                    ]}
                  >
                    <Text style={styles.editActionText}>
                      {row.value === null ? '填写' : '编辑'}
                    </Text>
                  </Pressable>
                </View>
              </View>

              {editing ? (
                <View style={styles.inlineEditorWrap}>
                  <PersonalValueEditor
                    disabled={mutating}
                    fieldName={row.fieldName}
                    initialUnit={row.unit}
                    initialValue={row.inputValue}
                    key={row.fieldName}
                    onCancel={onCancel}
                    onSubmit={(value) => onSave(row.fieldName, value)}
                  />
                </View>
              ) : clearing ? (
                <ConfirmDelete
                  confirmLabel="确认清除"
                  disabled={mutating}
                  message={`确定清除“${row.label}”吗？`}
                  onCancel={onCancel}
                  onConfirm={() => onClear(row.fieldName)}
                />
              ) : null}

              {index === rows.length - 1 ? null : (
                <View style={styles.profileDivider} />
              )}
            </View>
          );
        })}
        <Text selectable style={styles.profileUpdated}>
          档案更新于 {formatDateTime(profile.updatedAt)}
        </Text>
      </View>
    </>
  );
}

function FactsSection({
  editor,
  facts,
  mutating,
  onAdd,
  onCancel,
  onEdit,
  onRequestRetract,
  onRetract,
  onSave,
}: {
  editor: ProfileEditor | null;
  facts: HealthFact[];
  mutating: boolean;
  onAdd: () => void;
  onCancel: () => void;
  onEdit: (factId: string) => void;
  onRequestRetract: (factId: string) => void;
  onRetract: (fact: HealthFact) => void;
  onSave: (value: HealthFactValue, existing?: HealthFact) => void;
}) {
  return (
    <>
      <View style={styles.sectionHeaderWithAction}>
        <SectionHeader
          description="记录病史、过敏、严重低血糖和治疗情况。"
          kicker="健康情况"
          title="我的健康事实"
        />
        <Pressable
          accessibilityLabel="新增健康情况"
          accessibilityRole="button"
          disabled={mutating}
          onPress={onAdd}
          style={({ pressed }) => [
            styles.addButton,
            mutating && styles.buttonDisabled,
            pressed && !mutating && styles.actionPressed,
          ]}
        >
          <Text style={styles.addButtonText}>＋ 新增</Text>
        </Pressable>
      </View>

      {editor?.kind === 'fact_add' ? (
        <View style={styles.factEditorWrap}>
          <HealthFactEditor
            disabled={mutating}
            onCancel={onCancel}
            onSubmit={(value) => onSave(value)}
          />
        </View>
      ) : null}

      {facts.length === 0 ? (
        <EmptyState
          description="当前没有已写入的健康情况，也可以在聊天中直接告诉安糖。"
          title="这里还是空的"
          visual="profile"
        />
      ) : (
        <View style={styles.factList}>
          {facts.map((fact) => {
            const period = effectivePeriod(fact);
            return (
              <View key={fact.id} style={styles.factCard}>
                <View style={styles.factHeading}>
                  <View style={styles.factTypePill}>
                    <Text style={styles.factTypeText}>
                      {healthFactTypeLabels[fact.factType]}
                    </Text>
                  </View>
                  <Text style={styles.factStatus}>
                    {fact.assertion === 'present' ? '已记录' : '明确否认'} ·{' '}
                    {temporalLabels[fact.temporalStatus]}
                  </Text>
                </View>
                <Text selectable style={styles.factStatement}>
                  {fact.statement}
                </Text>
                <View style={styles.factActions}>
                  <Pressable
                    accessibilityLabel={`删除健康情况${fact.statement}`}
                    accessibilityRole="button"
                    disabled={mutating}
                    onPress={() => onRequestRetract(fact.id)}
                    style={({ pressed }) => [
                      styles.textAction,
                      pressed && styles.actionPressed,
                    ]}
                  >
                    <Text style={styles.destructiveActionText}>删除</Text>
                  </Pressable>
                  <Pressable
                    accessibilityLabel={`编辑健康情况${fact.statement}`}
                    accessibilityRole="button"
                    disabled={mutating}
                    onPress={() => onEdit(fact.id)}
                    style={({ pressed }) => [
                      styles.textAction,
                      pressed && styles.actionPressed,
                    ]}
                  >
                    <Text style={styles.editActionText}>编辑</Text>
                  </Pressable>
                </View>

                {editor?.kind === 'fact' && editor.factId === fact.id ? (
                  <HealthFactEditor
                    disabled={mutating}
                    fixedFactType={fact.factType}
                    initialValue={{
                      factType: fact.factType,
                      statement: fact.statement,
                      assertion: fact.assertion,
                      temporalStatus: fact.temporalStatus,
                    }}
                    onCancel={onCancel}
                    onSubmit={(value) => onSave(value, fact)}
                  />
                ) : editor?.kind === 'fact_retract' &&
                  editor.factId === fact.id ? (
                  <ConfirmDelete
                    disabled={mutating}
                    message="确定删除这条健康情况吗？删除后不会继续作为当前档案展示。"
                    onCancel={onCancel}
                    onConfirm={() => onRetract(fact)}
                  />
                ) : null}

                <View style={styles.factFooter}>
                  {period === null ? null : (
                    <Text selectable style={styles.factPeriod}>
                      {period}
                    </Text>
                  )}
                  <Text selectable style={styles.factUpdated}>
                    更新于 {formatDateTime(fact.updatedAt)}
                  </Text>
                </View>
              </View>
            );
          })}
        </View>
      )}
    </>
  );
}

function ConfirmDelete({
  confirmLabel = '确认删除',
  disabled,
  message,
  onCancel,
  onConfirm,
}: {
  confirmLabel?: string;
  disabled: boolean;
  message: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <View style={styles.deleteConfirm}>
      <Text selectable style={styles.deleteConfirmText}>
        {message}
      </Text>
      <View style={styles.deleteConfirmActions}>
        <Pressable
          accessibilityRole="button"
          disabled={disabled}
          onPress={onCancel}
          style={styles.textAction}
        >
          <Text style={styles.editActionText}>取消</Text>
        </Pressable>
        <Pressable
          accessibilityLabel={confirmLabel}
          accessibilityRole="button"
          disabled={disabled}
          onPress={onConfirm}
          style={({ pressed }) => [
            styles.deleteButton,
            disabled && styles.buttonDisabled,
            pressed && !disabled && styles.actionPressed,
          ]}
        >
          {disabled ? (
            <ActivityIndicator color={colors.card} size="small" />
          ) : (
            <Text style={styles.deleteButtonText}>{confirmLabel}</Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}

function LoadingState() {
  return (
    <View
      accessibilityLabel="正在读取健康记录"
      accessibilityLiveRegion="polite"
      style={styles.loadingState}
    >
      <View style={styles.loadingHeader}>
        <ActivityIndicator color={colors.pine} />
        <Text style={styles.loadingText}>正在读取健康记录…</Text>
      </View>
      <View style={styles.skeletonWide} />
      <View style={styles.skeletonRow}>
        <View style={styles.skeletonHalf} />
        <View style={styles.skeletonHalf} />
      </View>
      <View style={styles.skeletonList} />
    </View>
  );
}

function ErrorState({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <View
      accessibilityLiveRegion="polite"
      accessibilityRole="alert"
      style={styles.errorState}
    >
      <View style={styles.errorMark}>
        <Text style={styles.errorMarkText}>!</Text>
      </View>
      <Text style={styles.errorTitle}>健康记录没有加载成功</Text>
      <Text selectable style={styles.errorMessage}>
        {error}
      </Text>
      <Pressable
        accessibilityLabel="重新加载健康记录"
        accessibilityRole="button"
        hitSlop={4}
        onPress={onRetry}
        style={({ pressed }) => [
          styles.retryButton,
          pressed && styles.buttonPressed,
        ]}
      >
        <Text style={styles.retryButtonText}>重新加载</Text>
      </Pressable>
    </View>
  );
}

function InlineError({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <View
      accessibilityLiveRegion="polite"
      accessibilityRole="alert"
      style={styles.inlineError}
    >
      <View style={styles.inlineErrorCopy}>
        <Text style={styles.inlineErrorTitle}>刷新失败</Text>
        <Text selectable style={styles.inlineErrorText}>
          {error}
        </Text>
      </View>
      <Pressable
        accessibilityLabel="重新刷新健康数据"
        accessibilityRole="button"
        hitSlop={4}
        onPress={onRetry}
        style={styles.inlineRetry}
      >
        <Text style={styles.inlineRetryText}>重试</Text>
      </Pressable>
    </View>
  );
}

function EmptyState({
  description,
  title,
  visual,
}: {
  description: string;
  title: string;
  visual: 'bars' | 'profile';
}) {
  return (
    <View style={styles.emptyCard}>
      {visual === 'bars' ? (
        <View style={styles.emptyBars}>
          <View style={[styles.emptyBar, { height: 13 }]} />
          <View style={[styles.emptyBar, { height: 28 }]} />
          <View style={[styles.emptyBar, { height: 20 }]} />
          <View style={[styles.emptyBar, { height: 35 }]} />
          <View style={[styles.emptyBar, { height: 17 }]} />
        </View>
      ) : (
        <View style={styles.emptyProfile}>
          <View style={styles.emptyProfileDot} />
          <View style={styles.emptyProfileLines}>
            <View style={styles.emptyProfileLineLong} />
            <View style={styles.emptyProfileLineShort} />
          </View>
        </View>
      )}
      <View style={styles.emptyCopy}>
        <Text style={styles.emptyTitle}>{title}</Text>
        <Text style={styles.emptyDescription}>{description}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.paper },
  content: { paddingBottom: 44 },
  hero: {
    minHeight: 286,
    overflow: 'hidden',
    paddingHorizontal: 22,
    paddingTop: 20,
    paddingBottom: 24,
    borderBottomLeftRadius: 32,
    borderBottomRightRadius: 32,
    borderCurve: 'continuous',
  },
  heroOrbLarge: {
    position: 'absolute',
    top: -75,
    right: -55,
    width: 210,
    height: 210,
    borderRadius: 105,
    backgroundColor: 'rgba(236, 214, 168, 0.10)',
  },
  heroOrbSmall: {
    position: 'absolute',
    right: 70,
    bottom: -50,
    width: 126,
    height: 126,
    borderRadius: 63,
    backgroundColor: 'rgba(210, 133, 95, 0.15)',
  },
  heroTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  eyebrow: {
    color: '#DDE9DF',
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 2.2,
  },
  heroActions: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  heroAction: {
    minWidth: 64,
    minHeight: 48,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 14,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: 'rgba(255, 255, 255, 0.28)',
    borderRadius: 22,
    backgroundColor: 'rgba(255, 255, 255, 0.10)',
  },
  heroActionPressed: { opacity: 0.68 },
  heroActionText: {
    color: '#FFFDF7',
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '700',
  },
  closeButton: {
    width: 48,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 24,
    backgroundColor: 'rgba(255, 255, 255, 0.14)',
  },
  closeText: {
    marginTop: -2,
    color: '#FFFDF7',
    fontFamily: typefaces.sans,
    fontSize: 29,
    fontWeight: '300',
  },
  heroTitle: {
    paddingTop: 34,
    color: '#FFFDF7',
    fontFamily: typefaces.serif,
    fontSize: 42,
    fontWeight: '400',
    letterSpacing: -1,
  },
  heroDescription: {
    maxWidth: 310,
    paddingTop: 12,
    color: '#DCE7E1',
    fontFamily: typefaces.sans,
    fontSize: 15,
    lineHeight: 23,
  },
  heroMetaRow: {
    paddingTop: 25,
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 10,
  },
  historyPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    paddingHorizontal: 11,
    paddingVertical: 7,
    borderRadius: 16,
    backgroundColor: 'rgba(245, 239, 222, 0.14)',
  },
  historyDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#E3B37D',
  },
  historyPillText: {
    color: '#FFF4E5',
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
    fontWeight: '700',
  },
  latestText: {
    color: '#CAD9D1',
    fontFamily: typefaces.sans,
    fontSize: 11,
    fontVariant: ['tabular-nums'],
  },
  sectionHeader: {
    paddingHorizontal: 20,
    paddingTop: 34,
    paddingBottom: 14,
  },
  sectionKicker: {
    color: colors.clay,
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.8,
  },
  sectionTitle: {
    paddingTop: 6,
    color: colors.ink,
    fontFamily: typefaces.serif,
    fontSize: 27,
    fontWeight: '400',
    letterSpacing: -0.4,
  },
  sectionDescription: {
    maxWidth: 330,
    paddingTop: 7,
    color: colors.mutedInk,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 20,
  },
  mutationError: {
    marginHorizontal: 16,
    marginTop: 18,
    padding: 13,
    borderRadius: 16,
    backgroundColor: colors.errorPale,
  },
  mutationErrorText: {
    color: colors.error,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 19,
  },
  savedRefreshNotice: {
    marginHorizontal: 16,
    marginTop: 18,
    padding: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#BFDCD4',
    borderRadius: 16,
    backgroundColor: colors.sage,
  },
  savedRefreshCopy: { flex: 1, gap: 3 },
  savedRefreshTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '700',
  },
  savedRefreshText: {
    color: colors.mutedInk,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
  savedRefreshButton: {
    minWidth: 82,
    minHeight: 38,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 12,
    borderRadius: 19,
    backgroundColor: colors.pine,
  },
  savedRefreshButtonText: {
    color: colors.card,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    fontWeight: '700',
  },
  sectionHeaderWithAction: { position: 'relative' },
  addButton: {
    position: 'absolute',
    top: 48,
    right: 20,
    minHeight: 38,
    justifyContent: 'center',
    paddingHorizontal: 14,
    borderRadius: 19,
    backgroundColor: colors.pine,
  },
  addButtonText: {
    color: colors.card,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '700',
  },
  metricsGrid: {
    paddingHorizontal: 16,
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  metricCard: {
    flexGrow: 1,
    flexBasis: '46%',
    minWidth: 150,
    minHeight: 202,
    padding: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#E1DDD2',
    borderRadius: 24,
    borderCurve: 'continuous',
    backgroundColor: colors.card,
    boxShadow: '0 6px 22px rgba(48, 55, 49, 0.055)',
  },
  metricCardWide: { flexBasis: '100%', minHeight: 218 },
  metricTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  metricLabel: {
    color: colors.mutedInk,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
  metricAccent: { width: 18, height: 4, borderRadius: 2 },
  metricValueRow: {
    minHeight: 50,
    paddingTop: 13,
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 5,
  },
  metricValue: {
    flexShrink: 1,
    color: colors.ink,
    fontFamily: typefaces.serif,
    fontSize: 35,
    fontWeight: '400',
    letterSpacing: -1.1,
    fontVariant: ['tabular-nums'],
  },
  metricValueCompact: { fontSize: 24, letterSpacing: -0.3 },
  metricUnit: {
    color: colors.mutedInk,
    fontFamily: typefaces.sans,
    fontSize: 12,
    fontWeight: '600',
  },
  metricHint: {
    minHeight: 36,
    paddingTop: 4,
    color: '#7B857F',
    fontFamily: typefaces.sans,
    fontSize: 11,
    lineHeight: 17,
  },
  metricFooter: {
    marginTop: 'auto',
    paddingTop: 13,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#E5E0D6',
    gap: 3,
  },
  metricTime: {
    color: '#5B6963',
    fontFamily: typefaces.sans,
    fontSize: 10,
    fontVariant: ['tabular-nums'],
  },
  metricSource: {
    color: '#949B96',
    fontFamily: typefaces.sans,
    fontSize: 9,
  },
  heartTrend: { marginTop: 9, marginBottom: 9 },
  heartTrendLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 2,
  },
  heartTrendLabel: {
    color: colors.mutedInk,
    fontFamily: typefaces.sans,
    fontSize: 9,
    fontVariant: ['tabular-nums'],
  },
  heartTrendRange: {
    color: colors.clay,
    fontFamily: typefaces.sansMedium,
    fontSize: 9,
    fontWeight: '700',
  },
  sleepVisual: { paddingTop: 12, paddingBottom: 12, gap: 9 },
  sleepAxisLabel: {
    color: '#858D88',
    fontFamily: typefaces.sans,
    fontSize: 9,
  },
  sleepBand: {
    height: 12,
    overflow: 'hidden',
    flexDirection: 'row',
    borderRadius: 6,
    backgroundColor: '#E8E5DE',
  },
  sleepLegend: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  sleepLegendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  sleepLegendDot: { width: 6, height: 6, borderRadius: 3 },
  sleepLegendText: {
    color: '#737E78',
    fontFamily: typefaces.sans,
    fontSize: 9,
  },
  profileCard: {
    marginHorizontal: 16,
    paddingHorizontal: 17,
    paddingTop: 7,
    paddingBottom: 13,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#E1DDD2',
    borderRadius: 24,
    borderCurve: 'continuous',
    backgroundColor: colors.card,
  },
  profileRow: {
    minHeight: 66,
    paddingVertical: 13,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 18,
  },
  profileLabelGroup: { flex: 1, alignItems: 'flex-start', gap: 4 },
  profileLabel: {
    color: colors.mutedInk,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '600',
  },
  profileValue: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
    fontWeight: '600',
    textAlign: 'left',
    fontVariant: ['tabular-nums'],
  },
  profileNote: {
    color: '#8A928E',
    fontFamily: typefaces.sans,
    fontSize: 10,
    textAlign: 'left',
    fontVariant: ['tabular-nums'],
  },
  profileDivider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.line },
  rowActions: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  textAction: {
    minHeight: 38,
    justifyContent: 'center',
    paddingHorizontal: 9,
  },
  editActionText: {
    color: colors.pine,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '700',
  },
  destructiveActionText: {
    color: colors.error,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  actionPressed: { opacity: 0.65 },
  buttonDisabled: { opacity: 0.5 },
  inlineEditorWrap: { paddingBottom: 14 },
  profileUpdated: {
    alignSelf: 'flex-end',
    paddingTop: 10,
    color: '#949B96',
    fontFamily: typefaces.sans,
    fontSize: 10,
    fontVariant: ['tabular-nums'],
  },
  factList: { paddingHorizontal: 16, gap: 10 },
  factEditorWrap: { paddingHorizontal: 16, paddingBottom: 10 },
  factCard: {
    padding: 17,
    gap: 13,
    borderLeftWidth: 3,
    borderLeftColor: colors.clay,
    borderRadius: 20,
    borderCurve: 'continuous',
    backgroundColor: colors.card,
  },
  factHeading: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 9,
  },
  factTypePill: {
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: 12,
    backgroundColor: colors.clayPale,
  },
  factTypeText: {
    color: '#8A4F3D',
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
    fontWeight: '800',
  },
  factStatus: {
    color: '#77817C',
    fontFamily: typefaces.sans,
    fontSize: 10,
  },
  factStatement: {
    color: colors.ink,
    fontFamily: typefaces.serif,
    fontSize: 16,
    fontWeight: '400',
    lineHeight: 24,
  },
  factActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 4,
  },
  factFooter: {
    paddingTop: 2,
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    gap: 7,
  },
  factPeriod: {
    color: '#6C7771',
    fontFamily: typefaces.sans,
    fontSize: 10,
    fontVariant: ['tabular-nums'],
  },
  factUpdated: {
    color: '#9A9F9C',
    fontFamily: typefaces.sans,
    fontSize: 10,
    fontVariant: ['tabular-nums'],
  },
  deleteConfirm: {
    gap: 10,
    padding: 13,
    borderRadius: 16,
    backgroundColor: colors.errorPale,
  },
  deleteConfirmText: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 19,
  },
  deleteConfirmActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: 6,
  },
  deleteButton: {
    minWidth: 92,
    minHeight: 38,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 14,
    borderRadius: 19,
    backgroundColor: colors.error,
  },
  deleteButtonText: {
    color: colors.card,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '700',
  },
  noteCard: {
    marginHorizontal: 20,
    marginTop: 36,
    paddingTop: 15,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#CECABE',
  },
  noteRule: {
    width: 32,
    height: 3,
    marginBottom: 11,
    borderRadius: 2,
    backgroundColor: colors.clay,
  },
  noteTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    fontWeight: '800',
  },
  noteText: {
    maxWidth: 330,
    paddingTop: 6,
    color: colors.mutedInk,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 19,
  },
  loadingState: { padding: 20, gap: 12 },
  loadingHeader: {
    paddingVertical: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  loadingText: {
    color: colors.mutedInk,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  skeletonWide: {
    height: 190,
    borderRadius: 24,
    backgroundColor: '#E7E2D8',
  },
  skeletonRow: { flexDirection: 'row', gap: 10 },
  skeletonHalf: {
    flex: 1,
    height: 180,
    borderRadius: 24,
    backgroundColor: '#E9E4DA',
  },
  skeletonList: {
    height: 150,
    borderRadius: 24,
    backgroundColor: '#E7E2D8',
  },
  errorState: {
    margin: 20,
    padding: 25,
    alignItems: 'flex-start',
    borderRadius: 24,
    borderCurve: 'continuous',
    backgroundColor: colors.card,
  },
  errorMark: {
    width: 36,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 18,
    backgroundColor: colors.errorPale,
  },
  errorMarkText: {
    color: colors.error,
    fontFamily: typefaces.sansMedium,
    fontSize: 19,
    fontWeight: '800',
  },
  errorTitle: {
    paddingTop: 17,
    color: colors.ink,
    fontFamily: typefaces.serif,
    fontSize: 21,
    fontWeight: '400',
  },
  errorMessage: {
    paddingTop: 8,
    color: colors.error,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 20,
  },
  retryButton: {
    minHeight: 48,
    marginTop: 20,
    paddingHorizontal: 17,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 20,
    backgroundColor: colors.pine,
  },
  retryButtonText: {
    color: '#FFFDF7',
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    fontWeight: '700',
  },
  buttonPressed: { opacity: 0.72 },
  inlineError: {
    marginHorizontal: 16,
    marginTop: 18,
    padding: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderRadius: 18,
    backgroundColor: colors.errorPale,
  },
  inlineErrorCopy: { flex: 1, gap: 3 },
  inlineErrorTitle: {
    color: colors.error,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    fontWeight: '800',
  },
  inlineErrorText: {
    color: '#815650',
    fontFamily: typefaces.sans,
    fontSize: 11,
    lineHeight: 16,
  },
  inlineRetry: {
    minHeight: 48,
    minWidth: 52,
    paddingHorizontal: 13,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 16,
    backgroundColor: '#FFF8F3',
  },
  inlineRetryText: {
    color: colors.error,
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
    fontWeight: '800',
  },
  emptyCard: {
    marginHorizontal: 16,
    minHeight: 132,
    padding: 18,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 18,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#DED9CD',
    borderRadius: 24,
    borderCurve: 'continuous',
    backgroundColor: 'rgba(255, 252, 246, 0.72)',
  },
  emptyBars: {
    width: 66,
    height: 48,
    paddingHorizontal: 8,
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 4,
  },
  emptyBar: {
    flex: 1,
    borderRadius: 4,
    backgroundColor: '#AABDAF',
  },
  emptyProfile: {
    width: 66,
    height: 52,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderRadius: 16,
    backgroundColor: colors.sage,
  },
  emptyProfileDot: {
    width: 13,
    height: 13,
    borderRadius: 7,
    backgroundColor: colors.moss,
  },
  emptyProfileLines: { alignItems: 'center', gap: 3 },
  emptyProfileLineLong: {
    width: 28,
    height: 3,
    borderRadius: 2,
    backgroundColor: colors.moss,
  },
  emptyProfileLineShort: {
    width: 18,
    height: 3,
    borderRadius: 2,
    backgroundColor: '#A7B9AA',
  },
  emptyCopy: { flex: 1, gap: 5 },
  emptyTitle: {
    color: colors.ink,
    fontFamily: typefaces.serif,
    fontSize: 15,
    fontWeight: '600',
  },
  emptyDescription: {
    color: colors.mutedInk,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
});
