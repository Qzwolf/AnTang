import { Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';

import { colors, radii, spacing, typefaces } from '../ui/theme';
import { installedVersion } from '../update/release';
import type { UpdateCheckState } from '../update/use-update-check';

type Props = {
  update: UpdateCheckState;
  onClose: () => void;
  onOpenCare: () => void;
};

function versionLabel(installed: { name: string; code: number }): string {
  return installed.code > 0 ? `${installed.name} · ${installed.code}` : installed.name;
}

function actionLabel(update: UpdateCheckState): string {
  if (update.checking) return '检查中…';
  if (update.release !== null) return '有新版本';
  if (update.lastResult === 'up-to-date') return '已是最新版本';
  return '检查';
}

function updateHint(update: UpdateCheckState): string {
  if (update.error !== null) return update.error;
  if (update.release !== null) return `新版本 ${update.release.versionName} 已就绪`;
  if (update.lastResult === 'up-to-date') return '当前就是最新版本';
  return '有新版本时会在打开 App 时提示';
}

/**
 * 设置页。
 *
 * 只管「陪伴」与「版本」两件事；账号（退出、注销）归「我的」页最底部，避免同一批
 * 有后果的动作在两处出现。更新安装复用 `downloadAndInstall`，不卸载、不扫码。
 */
export function SettingsScreen({ update, onClose, onOpenCare }: Props) {
  const installed = installedVersion();

  return (
    <View style={styles.screen}>
      <View style={styles.navbar}>
        <Pressable
          accessibilityLabel="返回"
          accessibilityRole="button"
          onPress={onClose}
          style={styles.back}
        >
          <Text style={styles.backText}>‹</Text>
        </Pressable>
        <Text style={styles.navTitle}>设置</Text>
        <View style={styles.back} />
      </View>

      <ScrollView contentContainerStyle={styles.body}>
        <Text style={styles.groupTitle}>陪伴</Text>
        <View style={styles.group}>
          <Pressable
            accessibilityLabel="打开主动关怀设置"
            accessibilityRole="button"
            onPress={onOpenCare}
            style={styles.row}
          >
            <View style={styles.rowMain}>
              <Text style={styles.rowTitle}>主动关怀</Text>
              <Text style={styles.rowSub}>日常问候、提醒与回访的频率</Text>
            </View>
            <Text style={styles.chev}>›</Text>
          </Pressable>
        </View>

        <Text style={styles.groupTitle}>版本与更新</Text>
        <View style={styles.group}>
          <View style={styles.row}>
            <View style={styles.rowMain}>
              <Text style={styles.rowTitle}>当前版本</Text>
              <Text style={styles.rowSub}>{versionLabel(installed)}</Text>
            </View>
          </View>

          <Pressable
            accessibilityLabel="检查更新"
            accessibilityRole="button"
            disabled={update.checking}
            onPress={() => void update.check({ manual: true })}
            style={styles.row}
          >
            <View style={styles.rowMain}>
              <Text style={styles.rowTitle}>检查更新</Text>
              <Text style={styles.rowSub}>{updateHint(update)}</Text>
            </View>
            <Text style={styles.rowAction}>{actionLabel(update)}</Text>
            <Text style={styles.chev}>›</Text>
          </Pressable>

          <View style={styles.row}>
            <View style={styles.rowMain}>
              <Text style={styles.rowTitle}>自动检查</Text>
              <Text style={styles.rowSub}>打开 App 或回到前台时检查一次，一天最多一次</Text>
            </View>
            <Switch
              accessibilityLabel="自动检查更新"
              onValueChange={(next) => void update.setAutoCheck(next)}
              thumbColor={colors.paper}
              trackColor={{ false: colors.disabled, true: colors.primary }}
              value={update.autoCheck}
            />
          </View>
        </View>

        <Text style={styles.footer}>
          装好新版本后聊天记录都在。第一次更新时系统会问一句「是否允许安装应用」，允许一次就好。
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  navbar: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
    backgroundColor: colors.paper,
  },
  back: { width: 24 },
  backText: { color: colors.ink, fontSize: 22, lineHeight: 26 },
  navTitle: {
    flex: 1,
    textAlign: 'center',
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 16,
  },
  body: { paddingBottom: spacing.xxl },
  groupTitle: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
    marginTop: spacing.md,
    marginBottom: spacing.xs,
    marginHorizontal: spacing.lg,
  },
  group: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    marginHorizontal: spacing.sm,
    overflow: 'hidden',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  rowMain: { flex: 1 },
  rowTitle: { color: colors.ink, fontFamily: typefaces.sansMedium, fontSize: 14 },
  rowSub: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 2,
  },
  rowAction: { color: colors.primaryPressed, fontFamily: typefaces.sans, fontSize: 12 },
  chev: { color: colors.faint, fontSize: 14 },
  danger: { color: colors.danger },
  footer: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
    lineHeight: 17,
    marginTop: spacing.md,
    marginHorizontal: spacing.lg,
  },
});
