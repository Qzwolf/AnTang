import {
  ArrowRight01Icon,
  BloodIcon,
  FolderHeartIcon,
  Message01Icon,
  Settings04Icon,
  SmartWatch01Icon,  // 新增：手环图标
  ChartLineData01Icon,  // 新增：看板图标
} from '@hugeicons/core-free-icons';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';

export type ConversationAnchor = {
  group: string;
  id: string;
  preview: string;
  time: string;
};

type Props = {
  conversations: ConversationAnchor[];
  onClose: () => void;
  onOpenHealth: () => void;
  onOpenSettings: () => void;
  onOpenDashboard?: () => void;
  onOpenPatientAccess?: () => void;
  onOpenGlucose?: () => void;
  onSelectConversation: (messageId: string) => void;
  username: string;
  visible: boolean;
  // ===== 新增：Gadgetbridge 相关 props =====
  gadgetbridgeChecking?: boolean;
  gadgetbridgeConnected?: boolean;
  gadgetbridgeSyncing?: boolean;
  gadgetbridgeError?: string | null;
  gadgetbridgeProgress?: string | null;
  gadgetbridgeLastSyncedAt?: string | null;
  onGadgetbridgeConnect?: () => void;
  onGadgetbridgeSync?: () => void;
};

function formatLastSync(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}


export function AppMenu({
  conversations,
  onClose,
  onOpenHealth,
  onOpenSettings,
  onOpenDashboard = () => { },
  onOpenPatientAccess,
  onOpenGlucose = () => { },
  onSelectConversation,
  username,
  visible,
  // ===== 新增：Gadgetbridge props 默认值 =====
  gadgetbridgeChecking = false,
  gadgetbridgeConnected = false,
  gadgetbridgeSyncing = false,
  gadgetbridgeError = null,
  gadgetbridgeProgress = null,
  gadgetbridgeLastSyncedAt = null,
  onGadgetbridgeConnect = () => { },
  onGadgetbridgeSync = () => { },
}: Props) {
  let previousGroup: string | null = null;

  // Gadgetbridge 状态文案
  const gadgetbridgeBusy = gadgetbridgeChecking || gadgetbridgeSyncing;
  const gadgetbridgeStatus = gadgetbridgeChecking
    ? '正在检查手环数据...'
    : gadgetbridgeProgress ??
    (gadgetbridgeLastSyncedAt === null
      ? gadgetbridgeConnected
        ? '手环数据已就绪'
        : '选择华为手环的 Gadgetbridge 导出文件'
      : `上次同步 ${formatLastSync(gadgetbridgeLastSyncedAt)}`);

  return (
    <Modal
      animationType="fade"
      onRequestClose={onClose}
      statusBarTranslucent
      transparent
      visible={visible}
    >
      <View style={styles.overlay}>
        <Pressable
          accessibilityLabel="关闭菜单"
          accessibilityRole="button"
          onPress={onClose}
          style={StyleSheet.absoluteFill}
        />
        <SafeAreaView edges={['top', 'bottom', 'left']} style={styles.panel}>
          <View style={styles.accountRow}>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>{username.slice(0, 1).toUpperCase()}</Text>
            </View>
            <View style={styles.accountCopy}>
              <Text numberOfLines={1} style={styles.username}>
                {username}
              </Text>
              <Text style={styles.accountHint}>安糖心语账号</Text>
            </View>
            <Pressable
              accessibilityLabel="打开设置"
              accessibilityRole="button"
              onPress={onOpenSettings}
              style={({ pressed }) => [
                styles.settingsButton,
                pressed && styles.pressed,
              ]}
            >
              <AppIcon icon={Settings04Icon} />
            </Pressable>
          </View>

          {onOpenPatientAccess === undefined ? null : (
            <Pressable accessibilityLabel="医护授权" accessibilityRole="button"
              onPress={onOpenPatientAccess} style={styles.healthEntry}>
              <View style={styles.healthCopy}>
                <Text style={styles.healthTitle}>医护授权</Text>
                <Text style={styles.healthHint}>指定医护只读查看，随时撤销</Text>
              </View>
              <AppIcon color={colors.muted} icon={ArrowRight01Icon} size={20} />
            </Pressable>
          )}
          {/* ===== 原有的健康档案入口 ===== */}
          <Pressable
            accessibilityLabel="健康档案"
            accessibilityRole="button"
            onPress={onOpenHealth}
            style={({ pressed }) => [
              styles.healthEntry,
              pressed && styles.healthEntryPressed,
            ]}
          >
            <View style={styles.healthIcon}>
              <AppIcon color={colors.primaryPressed} icon={FolderHeartIcon} size={26} />
            </View>
            <View style={styles.healthCopy}>
              <Text style={styles.healthTitle}>健康档案</Text>
              <Text style={styles.healthHint}>档案、手环与健康记录</Text>
            </View>
            <AppIcon color={colors.muted} icon={ArrowRight01Icon} size={20} />
          </Pressable>

          {/* ===== 新增：健康看板入口 ===== */}
          <Pressable
            accessibilityLabel="健康看板"
            accessibilityRole="button"
            onPress={onOpenDashboard}
            style={({ pressed }) => [
              styles.healthEntry,
              pressed && styles.healthEntryPressed,
            ]}
          >
            <View style={styles.healthIcon}>
              <AppIcon color={colors.primaryPressed} icon={ChartLineData01Icon} size={26} />
            </View>
            <View style={styles.healthCopy}>
              <Text style={styles.healthTitle}>健康看板</Text>
              <Text style={styles.healthHint}>查看近 30 天已保存的手环记录</Text>
            </View>
            <AppIcon color={colors.muted} icon={ArrowRight01Icon} size={20} />
          </Pressable>

          {/* ===== 新增：血糖记录入口 ===== */}
          <Pressable
            accessibilityLabel="血糖记录"
            accessibilityRole="button"
            onPress={onOpenGlucose}
            style={({ pressed }) => [
              styles.healthEntry,
              pressed && styles.healthEntryPressed,
            ]}
          >
            <View style={[styles.healthIcon, styles.glucoseIcon]}>
              <AppIcon color="#B25F2A" icon={BloodIcon} size={26} />
            </View>
            <View style={styles.healthCopy}>
              <Text style={styles.healthTitle}>血糖记录</Text>
              <Text style={styles.healthHint}>记一条血糖，看看近 7 天</Text>
            </View>
            <AppIcon color={colors.muted} icon={ArrowRight01Icon} size={20} />
          </Pressable>

          {/* ===== 新增：Gadgetbridge 独立入口 ===== */}
          <Pressable
            accessibilityLabel="华为手环导入"
            accessibilityRole="button"
            disabled={gadgetbridgeBusy}
            onPress={gadgetbridgeConnected ? onGadgetbridgeSync : onGadgetbridgeConnect}
            style={({ pressed }) => [
              styles.gadgetbridgeEntry,
              gadgetbridgeBusy && styles.gadgetbridgeEntryDisabled,
              pressed && !gadgetbridgeBusy && styles.gadgetbridgeEntryPressed,
            ]}
          >
            <View style={styles.gadgetbridgeIcon}>
              <AppIcon color="#0A7C6B" icon={SmartWatch01Icon} size={26} />
            </View>
            <View style={styles.gadgetbridgeCopy}>
              <View style={styles.gadgetbridgeTitleRow}>
                <Text style={styles.gadgetbridgeTitle}>华为手环导入</Text>
                {gadgetbridgeConnected ? (
                  <View style={styles.connectedBadge}>
                    <Text style={styles.connectedBadgeText}>已连接</Text>
                  </View>
                ) : (
                  <View style={styles.disconnectedBadge}>
                    <Text style={styles.disconnectedBadgeText}>未连接</Text>
                  </View>
                )}
              </View>
              <Text style={styles.gadgetbridgeHint} numberOfLines={1}>
                {gadgetbridgeBusy ? '⏳ 同步中...' : gadgetbridgeStatus}
              </Text>
              {gadgetbridgeError ? (
                <Text style={styles.gadgetbridgeError} numberOfLines={2}>
                  ⚠️ {gadgetbridgeError}
                </Text>
              ) : null}
            </View>
            <Text style={styles.gadgetbridgeAction}>
              {gadgetbridgeBusy ? '...' : gadgetbridgeConnected ? '同步' : '连接'}
            </Text>
          </Pressable>


          <View style={styles.sectionHeading}>
            <AppIcon color={colors.muted} icon={Message01Icon} size={19} />
            <Text style={styles.sectionTitle}>对话记录</Text>
          </View>

          <ScrollView
            contentContainerStyle={styles.conversationList}
            showsVerticalScrollIndicator={false}
          >
            {conversations.length === 0 ? (
              <Text style={styles.emptyHistory}>开始聊天后，对话位置会显示在这里。</Text>
            ) : (
              conversations.map((conversation) => {
                const showGroup = conversation.group !== previousGroup;
                previousGroup = conversation.group;
                return (
                  <View key={conversation.id}>
                    {showGroup ? (
                      <Text style={styles.groupTitle}>{conversation.group}</Text>
                    ) : null}
                    <Pressable
                      accessibilityLabel={`${conversation.time} ${conversation.preview}`}
                      accessibilityRole="menuitem"
                      onPress={() => onSelectConversation(conversation.id)}
                      style={({ pressed }) => [
                        styles.conversation,
                        pressed && styles.conversationPressed,
                      ]}
                    >
                      <Text style={styles.conversationTime}>
                        {conversation.time}
                      </Text>
                      <Text numberOfLines={2} style={styles.conversationPreview}>
                        {conversation.preview}
                      </Text>
                    </Pressable>
                  </View>
                );
              })
            )}
          </ScrollView>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    flexDirection: 'row',
    backgroundColor: 'rgba(10, 28, 25, 0.34)',
  },
  panel: {
    width: '86%',
    maxWidth: 400,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
    backgroundColor: colors.background,
    boxShadow: '8px 0 30px rgba(23, 48, 46, 0.16)',
  },
  accountRow: {
    minHeight: 64,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  avatar: {
    width: 48,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  avatarText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 19,
  },
  accountCopy: { flex: 1, gap: 2 },
  username: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 18,
  },
  accountHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  settingsButton: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    backgroundColor: colors.paper,
  },
  pressed: { opacity: 0.64 },

  // ===== 原有的健康档案样式 =====
  healthEntry: {
    minHeight: 82,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.lg,
    paddingHorizontal: spacing.md,
    borderWidth: 1,
    borderColor: '#CBEAE2',
    borderRadius: radii.lg,
    borderCurve: 'continuous',
    backgroundColor: colors.primarySoft,
  },
  healthEntryPressed: { opacity: 0.74, transform: [{ scale: 0.99 }] },
  healthIcon: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: colors.paper,
  },
  glucoseIcon: { backgroundColor: '#FBEEE2' },
  healthCopy: { flex: 1, gap: 3 },
  healthTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 16,
  },
  healthHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },

  // ===== 新增：Gadgetbridge 样式 =====
  gadgetbridgeEntry: {
    minHeight: 72,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.sm,
    paddingHorizontal: spacing.md,
    borderWidth: 1,
    borderColor: '#B8D9E8',
    borderRadius: radii.lg,
    borderCurve: 'continuous',
    backgroundColor: '#F0F8FC',
  },
  gadgetbridgeEntryPressed: { opacity: 0.74, transform: [{ scale: 0.99 }] },
  gadgetbridgeEntryDisabled: { opacity: 0.5 },
  gadgetbridgeIcon: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: colors.paper,
  },
  gadgetbridgeCopy: { flex: 1, gap: 2 },
  gadgetbridgeTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  gadgetbridgeTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  connectedBadge: {
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radii.pill,
    backgroundColor: '#15966A',
  },
  connectedBadgeText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 9,
  },
  disconnectedBadge: {
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radii.pill,
    backgroundColor: '#D1D5DB',
  },
  disconnectedBadgeText: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 9,
  },
  gadgetbridgeHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
  gadgetbridgeError: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 10,
  },
  gadgetbridgeAction: {
    color: '#0A7C6B',
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },

  sectionHeading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingTop: spacing.xl,
    paddingHorizontal: spacing.xs,
  },
  sectionTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  conversationList: { paddingTop: spacing.sm, paddingBottom: spacing.lg },
  emptyHistory: {
    padding: spacing.md,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 20,
  },
  groupTitle: {
    paddingHorizontal: spacing.xs,
    paddingTop: spacing.md,
    paddingBottom: spacing.xxs,
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    letterSpacing: 0.4,
  },
  conversation: {
    alignItems: 'flex-start',
    backgroundColor: colors.paper,
    borderRadius: radii.sm,
    flexDirection: 'row',
    gap: spacing.sm,
    marginBottom: 6,
    minHeight: 58,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  conversationPressed: { backgroundColor: colors.paperMuted },
  conversationTime: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
    fontVariant: ['tabular-nums'],
    lineHeight: 20,
    width: 40,
  },
  conversationPreview: {
    color: colors.text,
    flex: 1,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 20,
  },
});
