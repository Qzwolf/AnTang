import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { colors, radii, spacing, typefaces } from "../ui/theme";

type Row = {
  label: string;
  hint?: string;
  onPress: () => void;
  tone?: "normal" | "danger";
};

type Props = {
  name: string;
  username: string;
  createdAt?: string | null;
  versionLabel: string;
  onOpenHealthProfile: () => void;
  onOpenHealthDashboard: () => void;
  onOpenPatientAccess: () => void;
  onExportData: () => void;
  onOpenCareSettings: () => void;
  onOpenSettings: () => void;
  onSignOut: () => void;
  onDeleteAccount: () => void;
};

function section(rows: Row[]) {
  return (
    <View style={styles.group}>
      {rows.map((row, index) => (
        <Pressable
          key={row.label}
          accessibilityRole="button"
          accessibilityLabel={row.label}
          onPress={row.onPress}
          style={[styles.row, index === rows.length - 1 ? styles.rowLast : null]}
        >
          <View style={styles.rowMain}>
            <Text style={[styles.rowLabel, row.tone === "danger" ? styles.rowDanger : null]}>
              {row.label}
            </Text>
            {row.hint === undefined ? null : <Text style={styles.rowSub}>{row.hint}</Text>}
          </View>
          <Text style={styles.chev}>›</Text>
        </Pressable>
      ))}
    </View>
  );
}

function registrationLine(createdAt: string | null | undefined): string | null {
  if (createdAt === null || createdAt === undefined) return null;
  const created = new Date(createdAt);
  if (Number.isNaN(created.getTime())) return null;
  const date = `${created.getFullYear()}-${`${created.getMonth() + 1}`.padStart(2, "0")}-${`${created.getDate()}`.padStart(2, "0")}`;
  const days = Math.max(
    0,
    Math.floor((Date.now() - created.getTime()) / (24 * 60 * 60 * 1000)),
  );
  return days <= 0 ? `注册于 ${date}` : `注册于 ${date} · 已使用 ${days} 天`;
}

/**
 * 「我的」页：账号与个人资料在这里，低频入口按「数据 / 隐私 / 设置 / 账号」四段排。
 *
 * 为什么账号沉到最底：退出与注销都是有后果的动作，放在手滑就能点到的地方不合适。
 * 为什么不再单独做「账号管理」页：那会让医护授权这类隐私入口同时出现在两处。
 */
export function ProfileScreen(props: Props) {
  const line = registrationLine(props.createdAt);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.body}>
      <View style={styles.card}>
        <View style={styles.identity}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{props.name.slice(0, 1).toUpperCase()}</Text>
          </View>
          <View style={styles.identityMain}>
            <Text style={styles.name}>{props.name}</Text>
            <Text style={styles.status}>@{props.username}</Text>
            {line === null ? null : <Text style={styles.status}>{line}</Text>}
          </View>
        </View>
      </View>

      <Text style={styles.groupTitle}>我的数据</Text>
      {section([
        { label: "健康档案", hint: "档案、手环与健康记录", onPress: props.onOpenHealthProfile },
        { label: "健康看板", hint: "查看近 30 天已保存的手环记录", onPress: props.onOpenHealthDashboard },
      ])}

      <Text style={styles.groupTitle}>隐私与分享</Text>
      {section([
        { label: "医护授权", hint: "指定医护只读查看，随时撤销", onPress: props.onOpenPatientAccess },
        { label: "导出我的数据", hint: "血糖、饮食、用药与记录，一次性导出", onPress: props.onExportData },
      ])}

      <Text style={styles.groupTitle}>设置</Text>
      {section([
        { label: "主动关怀", hint: "日常问候、提醒与回访的频率", onPress: props.onOpenCareSettings },
        { label: "检查更新", hint: props.versionLabel, onPress: props.onOpenSettings },
      ])}

      <Text style={styles.groupTitle}>账号</Text>
      {section([
        { label: "退出登录", hint: "聊天记录仍保存在当前账号中", onPress: props.onSignOut },
        {
          label: "注销账号",
          hint: "删除全部档案、对话、记录与陪伴记忆",
          onPress: props.onDeleteAccount,
          tone: "danger",
        },
      ])}

      <Text style={styles.footnote}>注销需要输入密码，删除后无法恢复。</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  body: {
    padding: spacing.md,
    paddingBottom: spacing.xxl,
    gap: spacing.xs,
  },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    padding: spacing.md,
  },
  identity: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
  },
  avatar: {
    width: 46,
    height: 46,
    borderRadius: 23,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 19,
    fontWeight: "700",
  },
  identityMain: {
    flex: 1,
    gap: 2,
  },
  name: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
    fontWeight: "700",
  },
  status: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  groupTitle: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
    letterSpacing: 0.4,
    marginTop: spacing.sm,
    marginBottom: 2,
    marginHorizontal: spacing.xs,
  },
  group: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    paddingHorizontal: spacing.md,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  rowLast: {
    borderBottomWidth: 0,
  },
  rowMain: {
    flex: 1,
    gap: 2,
  },
  rowLabel: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 14,
  },
  rowDanger: {
    color: colors.danger,
  },
  rowSub: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
    lineHeight: 16,
  },
  chev: {
    color: "#C3D1CD",
    fontSize: 15,
  },
  footnote: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
    lineHeight: 18,
    paddingHorizontal: spacing.xs,
    marginTop: spacing.xs,
  },
});
