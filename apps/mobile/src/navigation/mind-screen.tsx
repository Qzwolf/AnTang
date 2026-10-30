import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type { ApiClient } from "../api/client";
import { FearBehaviorSection } from "../fear/FearBehaviorSection";
import { PsychosocialSection } from "../psychosocial/PsychosocialSection";
import { colors, radii, spacing, typefaces } from "../ui/theme";

type Props = {
  api: Pick<
    ApiClient,
    | "getPsychosocialAssessment"
    | "getPsychosocialInstrument"
    | "recordPsychosocialAssessment"
    | "getFearSummary"
    | "getFearBehaviorLogs"
    | "getBeliefEvents"
    | "deleteFearBehaviorLog"
    | "deleteBeliefEvent"
  >;
  onOpenCareSettings: () => void;
  /** 切到「心理」时重建一次，刚在对话里记下的念头要立刻能看到。 */
  visible?: boolean;
};

/**
 * 「心理」tab：把和「怎么说话、最近在怕什么」有关的三块收在一处。
 *
 * 为什么从健康档案里搬出来：档案回答的是「身体记录了什么」，这三块回答的是
 * 「这个人现在需要哪种说话方式」，混在一页会把量表题埋在一堆指标下面。
 *
 * 为什么主动关怀也放在这里：它决定的是「糖糖什么时候来联系你」，跟沟通方式
 * 是同一件事的两面（频率 + 措辞），放在「我的」页会和账号设置混在一起。
 */
export function MindScreen({ api, onOpenCareSettings, visible = true }: Props) {
  // 用 key 让切回来时重新拉一次：量表上次填写时间、念头条数都会变。
  return (
    <ScrollView
      contentContainerStyle={styles.body}
      key={visible ? "mind-visible" : "mind-hidden"}
      style={styles.screen}
    >
      <View style={styles.head}>
        <Text style={styles.kicker}>心理</Text>
        <Text style={styles.title}>你最近在担心什么</Text>
        <Text style={styles.lead}>
          这里的记录全部来自你主动说过的内容，不用打卡，也不打分。
        </Text>
      </View>

      <PsychosocialSection api={api} />

      <FearBehaviorSection api={api} mode="all" />

      <View style={styles.group}>
        <Pressable
          accessibilityLabel="主动关怀"
          accessibilityRole="button"
          onPress={onOpenCareSettings}
          style={styles.row}
        >
          <View style={styles.rowMain}>
            <Text style={styles.rowLabel}>主动关怀</Text>
            <Text style={styles.rowSub}>日常问候、提醒与回访的频率</Text>
          </View>
          <Text style={styles.chev}>›</Text>
        </Pressable>
      </View>

      <Text style={styles.footnote}>
        这几道题是我们自己写的，不是标准化量表，只用来决定沟通方式，不作为诊断依据。
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    backgroundColor: colors.background,
    flex: 1,
  },
  body: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
  },
  head: {
    gap: spacing.xs,
  },
  kicker: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    fontWeight: "600",
    letterSpacing: 1,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 26,
    fontWeight: "700",
  },
  lead: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 20,
  },
  group: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: "hidden",
  },
  row: {
    alignItems: "center",
    flexDirection: "row",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
  },
  rowMain: {
    flex: 1,
    gap: 2,
  },
  rowLabel: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  rowSub: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  chev: {
    color: colors.faint,
    fontSize: 22,
  },
  footnote: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
});
