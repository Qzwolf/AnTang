import { HeartCheckIcon, Message01Icon } from '@hugeicons/core-free-icons';
import { useMemo } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { SafeAreaView } from 'react-native-safe-area-context';

import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';

export type ConversationAnchor = {
  group: string;
  id: string;
  title: string;
  time: string;
};

type Props = {
  conversations: ConversationAnchor[];
  activeConversationId?: string | null;
  onNewChat?: () => void;
  onClose: () => void;
  onOpenCare?: () => void;
  onOpenProfile?: () => void;
  onSelectConversation: (conversationId: string) => void;
  username: string;
  visible: boolean;
};

type GroupedAnchor = {
  anchor: ConversationAnchor;
  startsGroup: boolean;
};

function groupAnchors(anchors: ConversationAnchor[]): GroupedAnchor[] {
  const grouped: GroupedAnchor[] = [];
  let previous: string | null = null;
  for (const anchor of anchors) {
    grouped.push({ anchor, startsGroup: anchor.group !== previous });
    previous = anchor.group;
  }
  return grouped;
}

/**
 * 侧边栏只放对话：新开一段、以及过去每一段。
 * 健康档案、记录、设置这些入口统一在底部三个 tab 里，
 * 不再在侧边栏重复一遍（重复会把「打开 App 第一眼看到什么」弄乱）。
 */
export function AppMenu({
  conversations,
  activeConversationId = null,
  onNewChat,
  onClose,
  onOpenCare,
  onOpenProfile,
  onSelectConversation,
  username,
  visible,
}: Props) {
  const grouped = useMemo(() => groupAnchors(conversations), [conversations]);

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
            <Pressable
              accessibilityLabel="打开我的资料"
              accessibilityRole="button"
              onPress={() => {
                onClose();
                onOpenProfile?.();
              }}
              style={styles.avatar}
            >
              <Text style={styles.avatarText}>
                {username.slice(0, 1).toUpperCase()}
              </Text>
            </Pressable>
            <View style={styles.accountCopy}>
              <Text numberOfLines={1} style={styles.username}>
                {username}
              </Text>
              <Text style={styles.accountHint}>安糖心语账号</Text>
            </View>
          </View>

          <Pressable
            accessibilityLabel="开始新的对话"
            accessibilityRole="button"
            onPress={onNewChat}
            style={({ pressed }) => [styles.newChat, pressed && styles.pressed]}
          >
            <AppIcon color={colors.primaryPressed} icon={Message01Icon} size={18} />
            <Text style={styles.newChatText}>新的对话</Text>
          </Pressable>

          <Text style={styles.sectionTitle}>对话</Text>

          <ScrollView
            contentContainerStyle={styles.list}
            showsVerticalScrollIndicator={false}
          >
            {conversations.length === 0 ? (
              <Text style={styles.empty}>还没有别的对话。</Text>
            ) : null}
            {grouped.map(({ anchor, startsGroup }) => {
              const active = anchor.id === activeConversationId;
              return (
                <View key={anchor.id}>
                  {startsGroup ? (
                    <Text style={styles.groupTitle}>{anchor.group}</Text>
                  ) : null}
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`${anchor.title} ${anchor.time}`}
                    onPress={() => onSelectConversation(anchor.id)}
                    style={({ pressed }) => [
                      styles.conversation,
                      active && styles.conversationActive,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text
                      numberOfLines={1}
                      style={[
                        styles.conversationTitle,
                        active && styles.conversationTitleActive,
                      ]}
                    >
                      {anchor.title}
                    </Text>
                    <Text style={styles.conversationTime}>{anchor.time}</Text>
                  </Pressable>
                </View>
              );
            })}
          </ScrollView>

          {/* 主动关怀不属于任何一段对话，所以放在列表之外。 */}
          <Pressable
            accessibilityLabel="主动关怀"
            accessibilityRole="button"
            onPress={() => {
              onClose();
              onOpenCare?.();
            }}
            style={({ pressed }) => [styles.careRow, pressed && styles.pressed]}
          >
            <AppIcon color={colors.primaryPressed} icon={HeartCheckIcon} size={18} />
            <View style={styles.careCopy}>
              <Text style={styles.careTitle}>主动关怀</Text>
              <Text style={styles.careHint}>糖糖主动发来的关心，可改频率</Text>
            </View>
          </Pressable>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(10, 30, 28, 0.35)',
  },
  panel: {
    width: '84%',
    maxWidth: 360,
    height: '100%',
    paddingHorizontal: spacing.md,
    backgroundColor: colors.background,
  },
  accountRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.md,
  },
  avatar: {
    width: 46,
    height: 46,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  avatarText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 19,
    fontWeight: '700',
  },
  accountCopy: {
    flex: 1,
  },
  username: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
    fontWeight: '600',
  },
  accountHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  newChat: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    marginBottom: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.pill,
    backgroundColor: colors.primarySoft,
  },
  newChatText: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
    fontWeight: '600',
  },
  sectionTitle: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    letterSpacing: 0.4,
    marginBottom: spacing.xs,
  },
  list: {
    paddingBottom: spacing.xl,
  },
  empty: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 13,
    paddingVertical: spacing.sm,
  },
  groupTitle: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 12,
    marginTop: spacing.sm,
    marginBottom: spacing.xs,
  },
  conversation: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
    marginBottom: 6,
    minHeight: 46,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.md,
    backgroundColor: colors.paper,
  },
  conversationActive: {
    backgroundColor: colors.primarySoft,
  },
  conversationTitle: {
    flex: 1,
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 14,
  },
  conversationTitleActive: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontWeight: '600',
  },
  conversationTime: {
    width: 66,
    textAlign: 'right',
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 10.5,
  },
  pressed: {
    opacity: 0.72,
  },
  careRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.sm,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.md,
    backgroundColor: colors.primarySoft,
  },
  careCopy: {
    flex: 1,
  },
  careTitle: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
    fontWeight: '600',
  },
  careHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
});
