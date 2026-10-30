import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import type { useGadgetbridge } from '../health-connect/useGadgetbridge';
import type { useHealthConnect } from '../health-connect/use-health-connect';
import { LiveHeartRatePanel } from '../live-heart-rate/LiveHeartRatePanel';
import { useLiveHeartRateControllerOrNull } from '../live-heart-rate/LiveHeartRateProvider';
import { colors, radii, spacing, typefaces } from '../ui/theme';

export type HealthConnectController = ReturnType<typeof useHealthConnect>;
export type GadgetbridgeController = ReturnType<typeof useGadgetbridge>;

type Props = {
  gadgetbridge: GadgetbridgeController;
  healthConnect: HealthConnectController;
};

/**
 * 「记录」页顶部的三条设备通道。
 *
 * 为什么放在记录页而不是聊天页：连接手环是为了拿到数据，数据属于记录；聊天的第一屏
 * 应该只有对话。为什么分成三条而不合并：它们是三条完全不同的路——Health Connect 是
 * 系统里的健康数据库（手机得先有它），导出文件是拿厂商 App 的离线包，实时心率是手环
 * 用蓝牙广播出来的即时值（不经过服务器）。混在一起时患者看不出失败的是哪一条。
 */
export function DeviceChannels({ gadgetbridge, healthConnect }: Props) {
  const live = useLiveHeartRateControllerOrNull();
  const liveBusy = live !== null && (live.phase === 'scanning' || live.phase === 'connecting');
  const liveRunning = live !== null && live.phase === 'streaming';
  const liveStatus =
    live === null || !live.supported
      ? '这个安装包没有编译进实时心率模块，需要重新生成 Android 构建'
      : live.message !== null
        ? live.message
        : live.phase === 'streaming'
          ? `${live.latest?.beatsPerMinute ?? '—'} bpm · 直接来自手环蓝牙广播`
          : live.phase === 'scanning'
            ? '正在搜索正在广播心率的手环…'
            : live.phase === 'connecting'
              ? '正在连接手环…'
              : '先在华为手环上开启「心率广播」，再点开始';
  const liveBroken = live !== null && live.phase === 'error';

  const healthConnectBusy = healthConnect.checking || healthConnect.syncing;
  const healthConnectStatus =
    healthConnect.error ??
    healthConnect.progress ??
    (healthConnect.connected
      ? '已连接，记录会同步到健康看板'
      : healthConnect.checking
        ? '正在检查手机上的健康服务…'
        : '还没连接');

  const gadgetbridgeBusy = gadgetbridge.checking || gadgetbridge.syncing;
  const gadgetbridgeStatus =
    gadgetbridge.error ??
    gadgetbridge.progress ??
    (gadgetbridge.connected
      ? `已导入 ${gadgetbridge.heartRateCount} 条心率记录`
      : '还没有导入过文件');

  return (
    <View style={styles.group}>
      <Text style={styles.groupTitle}>设备与手环</Text>

      <Pressable
        accessibilityLabel="连接 Health Connect"
        accessibilityRole="button"
        disabled={healthConnectBusy}
        onPress={() => {
          if (healthConnect.connected) {
            healthConnect.sync();
          } else {
            healthConnect.connect();
          }
        }}
        style={({ pressed }) => [
          styles.row,
          healthConnectBusy && styles.rowBusy,
          pressed && !healthConnectBusy && styles.rowPressed,
        ]}
      >
        <View style={styles.rowMain}>
          <View style={styles.rowTitleLine}>
            <Text style={styles.rowTitle}>Health Connect</Text>
            <Text style={styles.rowBadge}>系统健康数据</Text>
          </View>
          <Text
            numberOfLines={2}
            style={[styles.rowSub, healthConnect.error !== null && styles.rowError]}
          >
            {healthConnectStatus}
          </Text>
        </View>
        {healthConnectBusy ? (
          <ActivityIndicator color={colors.primary} size="small" />
        ) : (
          <Text style={styles.rowAction}>
            {healthConnect.connected ? '同步' : '去连接'}
          </Text>
        )}
      </Pressable>

      <View style={styles.divider} />

      <Pressable
        accessibilityLabel="导入华为手环导出文件"
        accessibilityRole="button"
        disabled={gadgetbridgeBusy}
        onPress={() => {
          if (gadgetbridge.connected) {
            gadgetbridge.sync();
          } else {
            gadgetbridge.connect();
          }
        }}
        style={({ pressed }) => [
          styles.row,
          gadgetbridgeBusy && styles.rowBusy,
          pressed && !gadgetbridgeBusy && styles.rowPressed,
        ]}
      >
        <View style={styles.rowMain}>
          <View style={styles.rowTitleLine}>
            <Text style={styles.rowTitle}>导出文件导入</Text>
            <Text style={styles.rowBadge}>华为手环 / 华为运动健康</Text>
          </View>
          <Text
            numberOfLines={2}
            style={[styles.rowSub, gadgetbridge.error !== null && styles.rowError]}
          >
            {gadgetbridgeStatus}
          </Text>
        </View>
        {gadgetbridgeBusy ? (
          <ActivityIndicator color={colors.primary} size="small" />
        ) : (
          <Text style={styles.rowAction}>
            {gadgetbridge.connected ? '再导入' : '选择文件'}
          </Text>
        )}
      </Pressable>

      <View style={styles.divider} />

      <Pressable
        accessibilityLabel="实时心率监测"
        accessibilityRole="button"
        disabled={live === null || !live.supported || live.busy}
        onPress={() => {
          if (live === null || !live.supported) return;
          if (live.phase === 'idle' || live.phase === 'error') {
            live.startScan();
          } else {
            live.disconnect();
          }
        }}
        style={({ pressed }) => [
          styles.row,
          (live === null || !live.supported) && styles.rowBusy,
          pressed && styles.rowPressed,
        ]}
      >
        <View style={styles.rowMain}>
          <View style={styles.rowTitleLine}>
            <Text style={styles.rowTitle}>实时心率</Text>
            <Text style={styles.rowBadge}>手环蓝牙广播</Text>
          </View>
          <Text numberOfLines={2} style={[styles.rowSub, liveBroken && styles.rowError]}>
            {liveStatus}
          </Text>
        </View>
        {liveBusy ? (
          <ActivityIndicator color={colors.primary} size="small" />
        ) : (
          <Text style={styles.rowAction}>
            {liveRunning || liveBusy ? '停止' : live === null || !live.supported ? '不可用' : '开始监测'}
          </Text>
        )}
      </Pressable>

      {live !== null && live.supported && live.phase !== 'idle' ? (
        <View style={styles.panelWrap}>
          <LiveHeartRatePanel controller={live} />
        </View>
      ) : null}

      <Text style={styles.footnote}>
        华为手机大多没有 Health Connect（它是安卓系统的健康数据中心），这条路要先装好它；没装就直接用导出文件。
        实时心率不经过服务器，停掉监测就从手机上消失。
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  group: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    marginHorizontal: spacing.sm,
    marginTop: spacing.sm,
    paddingTop: spacing.sm,
  },
  groupTitle: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
    marginBottom: spacing.xs,
    marginHorizontal: spacing.md,
  },
  row: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  rowBusy: { opacity: 0.6 },
  rowPressed: { backgroundColor: colors.primarySoft },
  panelWrap: { marginHorizontal: spacing.sm },
  rowMain: { flex: 1 },
  rowTitleLine: { alignItems: 'center', flexDirection: 'row', gap: spacing.xs },
  rowTitle: { color: colors.ink, fontFamily: typefaces.sansMedium, fontSize: 14 },
  rowBadge: {
    backgroundColor: colors.paperMuted,
    borderRadius: radii.sm,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 10,
    overflow: 'hidden',
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  rowSub: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 2,
  },
  rowError: { color: colors.danger },
  rowAction: { color: colors.primaryPressed, fontFamily: typefaces.sans, fontSize: 12 },
  divider: {
    backgroundColor: colors.line,
    height: StyleSheet.hairlineWidth,
    marginLeft: spacing.md,
  },
  footnote: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 10,
    lineHeight: 15,
    marginHorizontal: spacing.md,
    marginTop: spacing.xs,
    paddingBottom: spacing.sm,
  },
});
