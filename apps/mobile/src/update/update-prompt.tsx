import { useState } from 'react';
import { ActivityIndicator, Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { formatSize, type ReleaseInfo } from './release';
import { downloadAndInstall } from './install';
import { colors, radii, spacing, typefaces } from '../ui/theme';

type Props = {
  release: ReleaseInfo;
  onDismiss: () => void;
};

/**
 * 「有新版本」弹窗。
 *
 * 安装走 `downloadAndInstall`：下载到缓存、核对字节数、再用 content:// 拉起系统
 * 安装器。系统安装器那一屏我们改不了，也做不到静默安装——这两条是 Android 的
 * 边界，不是我们的取舍。
 */
export function UpdatePrompt({ release, onDismiss }: Props) {
  const [installing, setInstalling] = useState(false);
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);

  async function install() {
    setError(null);
    setInstalling(true);
    setPercent(0);
    try {
      await downloadAndInstall(release, (progress) => setPercent(progress.percent));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '下载没完成，再试一次。');
    } finally {
      setInstalling(false);
    }
  }

  return (
    <Modal animationType="fade" onRequestClose={onDismiss} transparent visible>
      <View style={styles.backdrop}>
        <View style={styles.dialog}>
          <Text style={styles.title}>{`有新版本 ${release.versionName}`}</Text>
          <Text style={styles.subtitle}>
            {`约 ${formatSize(release.sizeBytes)} · 装好之后聊天记录都在`}
          </Text>

          {release.notes.trim().length > 0 ? (
            <View style={styles.notesBox}>
              <Text style={styles.notes}>{release.notes}</Text>
            </View>
          ) : null}

          {error === null ? null : <Text style={styles.error}>{error}</Text>}

          {installing ? (
            <View>
              <View style={styles.barTrack}>
                <View style={[styles.barFill, { width: `${percent}%` }]} />
              </View>
              <View style={styles.progressRow}>
                <ActivityIndicator color={colors.primary} size="small" />
                <Text style={styles.progress}>
                  {percent === 0 ? '正在下载…' : `已下载 ${percent}%`}
                </Text>
              </View>
            </View>
          ) : (
            <Pressable
              accessibilityLabel="下载并安装"
              accessibilityRole="button"
              onPress={() => void install()}
              style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}
            >
              <Text style={styles.primaryText}>下载并安装</Text>
            </Pressable>
          )}

          <Pressable
            accessibilityLabel="今天先不更新"
            accessibilityRole="button"
            disabled={installing}
            onPress={onDismiss}
            style={styles.ghost}
          >
            <Text style={styles.ghostText}>今天先不更新</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(23, 48, 46, 0.32)',
    justifyContent: 'center',
    padding: spacing.md,
  },
  dialog: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
    lineHeight: 24,
  },
  subtitle: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
    marginTop: spacing.xxs,
    marginBottom: spacing.sm,
  },
  notesBox: {
    backgroundColor: colors.paperMuted,
    borderRadius: radii.md,
    padding: spacing.sm,
    marginBottom: spacing.sm,
  },
  notes: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 20,
  },
  error: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
    marginBottom: spacing.xs,
  },
  barTrack: {
    height: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.paperMuted,
    overflow: 'hidden',
    marginTop: spacing.xxs,
  },
  barFill: { height: '100%', backgroundColor: colors.primary },
  progressRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  progress: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
  primaryButton: {
    backgroundColor: colors.primary,
    borderRadius: radii.pill,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    marginTop: spacing.xxs,
  },
  pressed: { opacity: 0.85 },
  primaryText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  ghost: { alignItems: 'center', paddingVertical: spacing.sm },
  ghostText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
});
