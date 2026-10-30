import { useEffect, useState } from 'react';
import { Settings04Icon } from '@hugeicons/core-free-icons';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { errorMessage, type ApiClient } from '../api/client';
import type {
  ProactiveCareSettings,
  RoutineCareCadence,
} from '../api/types';
import type { PushPermission } from '../notifications/notifications';
import type { BackgroundHealthSyncStatus } from '../health-connect/background-health-sync';
import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';

type Props = {
  api: ApiClient;
  backgroundHealthDisabling: boolean;
  backgroundHealthEnabling: boolean;
  backgroundHealthError: string | null;
  backgroundHealthStatus: BackgroundHealthSyncStatus | 'checking';
  notificationPermission: PushPermission | null;
  notificationRegistrationFailed: boolean;
  notificationRequesting: boolean;
  onClose: () => void;
  onDisableBackgroundHealth: () => Promise<void>;
  onEnableBackgroundHealth: () => Promise<void>;
  onRequestNotificationPermission: () => Promise<unknown>;
  visible: boolean;
};

const cadenceOptions: { value: RoutineCareCadence; label: string }[] = [
  { value: 'disabled', label: '关闭' },
  { value: 'daily', label: '每天' },
  { value: 'every_3_days', label: '每三天（推荐）' },
  { value: 'weekly', label: '每周' },
];

const permissionLabels: Record<PushPermission, string> = {
  undetermined: '尚未选择',
  denied: '系统通知已关闭，请到 Android 设置中开启',
  granted: '已开启',
};

const backgroundHealthLabels: Record<
  BackgroundHealthSyncStatus | 'checking',
  string
> = {
  checking: '正在读取后台检查状态…',
  not_ready: '先连接手环并完成一次同步，才能开启后台检查。',
  disabled: '后台检查尚未开启',
  enabled: '后台检查已开启，Android 会根据电量和系统状态安排运行。',
  needs_foreground:
    '后台同步游标已失效。打开聊天页并授权读取历史健康数据，核对完成后会恢复后台检查。',
  unavailable: '这台设备暂不支持 Health Connect 后台读取。',
  permission_missing: '后台读取权限已经关闭，可以在下方停用已登记的检查。',
};

function deviceTimeZone(): string {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!timezone) throw new Error('无法读取设备时区');
  try {
    new Intl.DateTimeFormat('zh-CN', { timeZone: timezone }).format();
  } catch {
    throw new Error('设备返回的时区不是有效的 IANA 时区');
  }
  return timezone;
}

export function CareSettingsModal({
  api,
  backgroundHealthDisabling,
  backgroundHealthEnabling,
  backgroundHealthError,
  backgroundHealthStatus,
  notificationPermission,
  notificationRegistrationFailed,
  notificationRequesting,
  onClose,
  onDisableBackgroundHealth,
  onEnableBackgroundHealth,
  onRequestNotificationPermission,
  visible,
}: Props) {
  const [settings, setSettings] = useState<ProactiveCareSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    void api
      .getProactiveCareSettings()
      .then(
        (value) => {
          if (active) setSettings(value);
        },
        (reason) => {
          if (active) setError(errorMessage(reason));
        },
      )
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, loadAttempt, visible]);

  function update(value: Partial<ProactiveCareSettings>) {
    setSettings((current) => (current === null ? null : { ...current, ...value }));
  }

  async function save() {
    if (settings === null) return;
    if (
      !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(settings.quietHoursStart) ||
      !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(settings.quietHoursEnd)
    ) {
      setError('免打扰时间必须使用 24 小时制 HH:MM，例如 22:00。');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await api.putProactiveCareSettings({
        routineCadence: settings.routineCadence,
        planFollowUpEnabled: settings.planFollowUpEnabled,
        healthEventsEnabled: settings.healthEventsEnabled,
        timezone: deviceTimeZone(),
        quietHoursStart: settings.quietHoursStart,
        quietHoursEnd: settings.quietHoursEnd,
        healthNotificationPreviewEnabled:
          settings.healthNotificationPreviewEnabled,
      });
      setSettings(saved);
      onClose();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  async function requestNotifications() {
    setError(null);
    try {
      await onRequestNotificationPermission();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function enableBackgroundHealth() {
    setError(null);
    try {
      await onEnableBackgroundHealth();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function disableBackgroundHealth() {
    setError(null);
    try {
      await onDisableBackgroundHealth();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  const disabled = loading || saving;

  return (
    <Modal
      onRequestClose={onClose}
      presentationStyle="pageSheet"
      visible={visible}
    >
      <SafeAreaView edges={['top', 'bottom']} style={styles.screen}>
        <View style={styles.header}>
          <View style={styles.headerIdentity}>
            <View style={styles.headerIcon}>
              <AppIcon color={colors.primary} icon={Settings04Icon} size={22} />
            </View>
            <View>
              <Text style={styles.title}>设置</Text>
              <Text style={styles.subtitle}>主动关怀、设备与通知</Text>
            </View>
          </View>
          <Pressable
            accessibilityRole="button"
            onPress={onClose}
            style={({ pressed }) => [
              styles.closeButton,
              pressed && styles.buttonPressed,
            ]}
          >
            <Text style={styles.close}>关闭</Text>
          </Pressable>
        </View>

        {loading && settings === null ? (
          <View style={styles.loading}>
            <ActivityIndicator color={colors.primary} />
          </View>
        ) : (
          <ScrollView
            contentContainerStyle={styles.content}
            contentInsetAdjustmentBehavior="automatic"
            keyboardShouldPersistTaps="handled"
          >
            {error === null ? null : (
              <Text selectable style={styles.error}>{error}</Text>
            )}

            {settings === null ? (
              <Pressable
                accessibilityRole="button"
                onPress={() => {
                  setSettings(null);
                  setError(null);
                  setLoading(true);
                  setLoadAttempt((attempt) => attempt + 1);
                }}
                style={styles.primaryButton}
              >
                <Text style={styles.primaryButtonText}>重新加载</Text>
              </Pressable>
            ) : (
              <>
                <View style={styles.section}>
                  <Text style={styles.sectionTitle}>日常问候</Text>
                  <Text style={styles.description}>
                    选择你舒服的节奏。每次聊天后会重新计时，也会避开免打扰时间和刚聊完的时候。
                  </Text>
                  <View style={styles.cadenceOptions}>
                    {cadenceOptions.map((option) => {
                      const selected = settings.routineCadence === option.value;
                      return (
                        <Pressable
                          accessibilityLabel={`日常关怀频率：${option.label}`}
                          accessibilityRole="button"
                          accessibilityState={{ selected }}
                          disabled={disabled}
                          key={option.value}
                          onPress={() => update({ routineCadence: option.value })}
                          style={[styles.choice, selected && styles.choiceSelected]}
                        >
                          <Text
                            style={[
                              styles.choiceText,
                              selected && styles.choiceTextSelected,
                            ]}
                          >
                            {option.label}
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                  <Text selectable style={styles.description}>
                    本周已主动联系 {settings.weeklyContactCount} 次
                    {settings.weeklyContactLimit === null
                      ? '，上限按沟通方式自评来算。'
                      : ` · 上限 ${settings.weeklyContactLimit} 次。`}
                  </Text>
                  {settings.autoPaused ? (
                    <View style={styles.pausedCard}>
                      <Text style={styles.pausedText}>
                        连着几次问候你没回，我先停下来了，不想在你忙的时候反复弹消息。想继续就说一声。
                      </Text>
                      <Pressable
                        accessibilityLabel="继续接收日常问候"
                        accessibilityRole="button"
                        disabled={disabled}
                        onPress={() => {
                          update({ routineCadence: 'daily' });
                          void api
                            .putProactiveCareSettings({
                              routineCadence: 'daily',
                              planFollowUpEnabled: settings.planFollowUpEnabled,
                              healthEventsEnabled: settings.healthEventsEnabled,
                              timezone: deviceTimeZone(),
                              quietHoursStart: settings.quietHoursStart,
                              quietHoursEnd: settings.quietHoursEnd,
                              healthNotificationPreviewEnabled:
                                settings.healthNotificationPreviewEnabled,
                            })
                            .then(
                              (saved) => {
                                setSettings(saved);
                              },
                              (reason) => {
                                setError(errorMessage(reason));
                              },
                            );
                        }}
                        style={styles.pausedButton}
                      >
                        <Text style={styles.pausedButtonText}>继续接收</Text>
                      </Pressable>
                    </View>
                  ) : null}
                </View>

                <View style={styles.section}>
                  <SettingSwitch
                    disabled={disabled}
                    label="计划回访"
                    onValueChange={(value) => update({ planFollowUpEnabled: value })}
                    value={settings.planFollowUpEnabled}
                  />
                  <Text style={styles.description}>
                    只在聊天中明确约定的时间复盘你同意的计划，不会变成通用闹钟或服药提醒。
                  </Text>
                </View>

                <View style={styles.section}>
                  <SettingSwitch
                    disabled={disabled}
                    label="心率关怀"
                    onValueChange={(value) => update({ healthEventsEnabled: value })}
                    value={settings.healthEventsEnabled}
                  />
                  <Text style={styles.description}>
                    同步到近期、持续偏快或偏慢，且不属于运动或睡眠的心率记录后，安糖会在合适时间发起关怀。这不是实时监护，也不能代替医疗判断。
                  </Text>
                  <View style={styles.divider} />
                  <Text style={styles.sectionTitle}>手环后台检查</Text>
                  <View style={styles.statusCard}>
                    <View
                      style={[
                        styles.statusDot,
                        backgroundHealthStatus === 'enabled' &&
                          styles.statusDotEnabled,
                      ]}
                    />
                    <Text selectable style={styles.notificationStatus}>
                      {backgroundHealthLabels[backgroundHealthStatus]}
                    </Text>
                  </View>
                  {backgroundHealthError === null ? null : (
                    <Text selectable style={styles.inlineError}>
                      {backgroundHealthError}
                    </Text>
                  )}
                  <Text style={styles.description}>
                    Android 最短按 15 分钟申请一次，实际运行时间可能更晚。
                  </Text>
                  {backgroundHealthStatus === 'disabled' ? (
                    <Pressable
                      accessibilityLabel="开启后台周期检查"
                      accessibilityRole="button"
                      disabled={backgroundHealthEnabling}
                      onPress={() => void enableBackgroundHealth()}
                      style={styles.secondaryButton}
                    >
                      <Text style={styles.secondaryButtonText}>
                        {backgroundHealthEnabling ? '正在开启…' : '开启后台周期检查'}
                      </Text>
                    </Pressable>
                  ) : null}
                  {backgroundHealthStatus === 'enabled' ||
                  backgroundHealthStatus === 'permission_missing' ||
                  backgroundHealthStatus === 'needs_foreground' ||
                  backgroundHealthStatus === 'unavailable' ? (
                    <Pressable
                      accessibilityLabel="关闭后台周期检查"
                      accessibilityRole="button"
                      disabled={backgroundHealthDisabling}
                      onPress={() => void disableBackgroundHealth()}
                      style={styles.secondaryButton}
                    >
                      <Text style={styles.secondaryButtonText}>
                        {backgroundHealthDisabling ? '正在关闭…' : '关闭后台周期检查'}
                      </Text>
                    </Pressable>
                  ) : null}
                  <View style={styles.divider} />
                  <SettingSwitch
                    disabled={disabled}
                    label="健康消息显示完整正文"
                    onValueChange={(value) =>
                      update({ healthNotificationPreviewEnabled: value })
                    }
                    value={settings.healthNotificationPreviewEnabled}
                  />
                  <Text style={styles.description}>
                    关闭时，锁屏只显示一条通用提示；开启后可显示关怀消息正文。
                  </Text>
                </View>

                <View style={styles.section}>
                  <Text style={styles.sectionTitle}>免打扰</Text>
                  <View style={styles.timeRow}>
                    <View style={styles.timeField}>
                      <Text style={styles.timeLabel}>开始</Text>
                      <TextInput
                        accessibilityLabel="免打扰开始时间"
                        autoCapitalize="none"
                        editable={!disabled}
                        keyboardType="numbers-and-punctuation"
                        maxLength={5}
                        onChangeText={(value) => update({ quietHoursStart: value })}
                        placeholder="22:00"
                        style={styles.timeInput}
                        value={settings.quietHoursStart}
                      />
                    </View>
                    <Text style={styles.timeSeparator}>至</Text>
                    <View style={styles.timeField}>
                      <Text style={styles.timeLabel}>结束</Text>
                      <TextInput
                        accessibilityLabel="免打扰结束时间"
                        autoCapitalize="none"
                        editable={!disabled}
                        keyboardType="numbers-and-punctuation"
                        maxLength={5}
                        onChangeText={(value) => update({ quietHoursEnd: value })}
                        placeholder="08:00"
                        style={styles.timeInput}
                        value={settings.quietHoursEnd}
                      />
                    </View>
                  </View>
                  <Text selectable style={styles.description}>
                    保存时会使用这台手机当前所在的时区。
                  </Text>
                </View>

                <View style={styles.section}>
                  <Text style={styles.sectionTitle}>系统通知</Text>
                  <Text selectable style={styles.notificationStatus}>
                    {notificationRegistrationFailed &&
                    notificationPermission === 'granted'
                      ? '系统通知已开启，但推送暂未连接'
                      : notificationPermission === null
                        ? '正在读取系统通知状态…'
                        : permissionLabels[notificationPermission]}
                  </Text>
                  {notificationPermission === 'granted' &&
                  !notificationRegistrationFailed &&
                  !notificationRequesting ? null : (
                    <Pressable
                      accessibilityLabel="申请系统通知权限"
                      accessibilityRole="button"
                      disabled={notificationRequesting}
                      onPress={() => void requestNotifications()}
                      style={styles.secondaryButton}
                    >
                      <Text style={styles.secondaryButtonText}>
                        {notificationRequesting
                          ? '正在检查…'
                          : notificationRegistrationFailed
                            ? '重新连接推送'
                          : notificationPermission === 'denied'
                            ? '重新检查系统通知'
                            : '开启系统通知'}
                      </Text>
                    </Pressable>
                  )}
                </View>

                <Pressable
                  accessibilityLabel="保存关怀设置"
                  accessibilityRole="button"
                  disabled={disabled}
                  onPress={() => void save()}
                  style={[styles.primaryButton, disabled && styles.buttonDisabled]}
                >
                  <Text style={styles.primaryButtonText}>
                    {saving ? '正在保存…' : '保存设置'}
                  </Text>
                </Pressable>
              </>
            )}
          </ScrollView>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function SettingSwitch({
  disabled,
  label,
  onValueChange,
  value,
}: {
  disabled: boolean;
  label: string;
  onValueChange: (value: boolean) => void;
  value: boolean;
}) {
  return (
    <View style={styles.switchRow}>
      <Text style={styles.switchLabel}>{label}</Text>
      <Switch
        accessibilityLabel={label}
        disabled={disabled}
        onValueChange={onValueChange}
        trackColor={{ false: colors.disabled, true: colors.primary }}
        thumbColor={value ? colors.paper : colors.white}
        value={value}
      />
    </View>
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
  headerIdentity: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  headerIcon: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: colors.primarySoft,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 20,
    fontWeight: '700',
  },
  subtitle: {
    paddingTop: 2,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
  closeButton: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.paperMuted,
  },
  close: {
    color: colors.primary,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  buttonPressed: { opacity: 0.65 },
  loading: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  content: {
    width: '100%',
    maxWidth: 680,
    alignSelf: 'center',
    gap: spacing.sm,
    padding: spacing.md,
    paddingBottom: spacing.xxl,
  },
  error: {
    padding: spacing.sm,
    borderRadius: radii.md,
    color: colors.danger,
    fontFamily: typefaces.sans,
    backgroundColor: colors.dangerSoft,
  },
  section: {
    gap: spacing.sm,
    padding: spacing.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.xl,
    borderCurve: 'continuous',
    backgroundColor: colors.paper,
  },
  sectionTitle: {
    color: colors.text,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  description: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
  cadenceOptions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  choice: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radii.pill,
  },
  choiceSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primarySoft,
  },
  choiceText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  choiceTextSelected: {
    color: colors.primary,
    fontFamily: typefaces.sansMedium,
  },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  switchLabel: {
    flex: 1,
    color: colors.text,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: colors.line,
  },
  statusCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    padding: spacing.sm,
    borderRadius: radii.md,
    backgroundColor: colors.paperMuted,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.disabled,
  },
  statusDotEnabled: { backgroundColor: colors.primary },
  pausedCard: {
    gap: spacing.xs,
    marginTop: spacing.xs,
    padding: spacing.sm,
    borderRadius: radii.md,
    backgroundColor: colors.paperMuted,
  },
  pausedText: {
    fontSize: 12.5,
    lineHeight: 19,
    color: colors.muted,
  },
  pausedButton: {
    alignSelf: 'flex-start',
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.primarySoft,
  },
  pausedButtonText: {
    fontSize: 12.5,
    fontWeight: '600',
    color: colors.primaryPressed,
  },
  timeRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: spacing.sm,
  },
  timeField: { flex: 1, gap: spacing.xxs },
  timeLabel: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
  timeInput: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radii.md,
    color: colors.text,
    fontFamily: typefaces.sans,
    fontSize: 16,
    fontVariant: ['tabular-nums'],
    backgroundColor: colors.background,
  },
  timeSeparator: {
    paddingBottom: 10,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  notificationStatus: {
    flex: 1,
    color: colors.text,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 18,
  },
  inlineError: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 17,
  },
  primaryButton: {
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
    paddingVertical: 13,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  primaryButtonText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  secondaryButton: {
    alignSelf: 'flex-start',
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.primarySoft,
  },
  secondaryButtonText: {
    color: colors.primary,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
  },
  buttonDisabled: { opacity: 0.55 },
});
