import { Pressable, StyleSheet, Text, View } from "react-native";
import Svg, { Circle, Path, Polyline } from "react-native-svg";

import { colors, spacing, typefaces } from "../ui/theme";

export type AppTabKey = "chat" | "records" | "mind" | "profile";

export const APP_TABS: { key: AppTabKey; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "records", label: "记录" },
  { key: "mind", label: "心理" },
  { key: "profile", label: "我的" },
];

type Props = {
  active: AppTabKey;
  onChange: (key: AppTabKey) => void;
};

const ICON_SIZE = 22;

/**
 * 四个 tab 的图标：都与「健康」有关但形状要拉开距离——
 * 对话是气泡、记录是纸笔、心理是心电图里的心、我的是人。
 * 线条一律描边不填充，选中态靠颜色区分，避免四个实心块糊在一起。
 */
function TabIcon({ tab, color }: { tab: AppTabKey; color: string }) {
  const common = {
    width: ICON_SIZE,
    height: ICON_SIZE,
    viewBox: "0 0 24 24",
    fill: "none" as const,
  };
  const stroke = {
    stroke: color,
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };

  if (tab === "chat") {
    return (
      <Svg {...common}>
        <Path
          d="M4.5 6.5a2.5 2.5 0 0 1 2.5-2.5h10a2.5 2.5 0 0 1 2.5 2.5v7a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.2a.6.6 0 0 1-1-.5V14"
          {...stroke}
        />
        <Circle cx="8.6" cy="10" r="0.9" fill={color} />
        <Circle cx="12" cy="10" r="0.9" fill={color} />
        <Circle cx="15.4" cy="10" r="0.9" fill={color} />
      </Svg>
    );
  }

  if (tab === "records") {
    return (
      <Svg {...common}>
        <Path d="M6 3.8h8.6a1.4 1.4 0 0 1 1.4 1.4v13a1.4 1.4 0 0 1-1.4 1.4H6a1.4 1.4 0 0 1-1.4-1.4V5.2A1.4 1.4 0 0 1 6 3.8Z" {...stroke} />
        <Polyline points="7.6,8.4 12.4,8.4" {...stroke} />
        <Polyline points="7.6,11.6 12.4,11.6" {...stroke} />
        <Polyline points="7.6,14.8 10.4,14.8" {...stroke} />
        <Path d="M16.4 8.6l3.1 3.1a1.1 1.1 0 0 1 0 1.5l-4.6 4.6-2.6.6.6-2.6 4.6-4.6a1.1 1.1 0 0 1 1.5 0Z" {...stroke} />
      </Svg>
    );
  }

  if (tab === "mind") {
    return (
      <Svg {...common}>
        <Path
          d="M12 20.2s-7.2-4.3-7.2-9.4A4.3 4.3 0 0 1 12 7.9a4.3 4.3 0 0 1 7.2 2.9c0 5.1-7.2 9.4-7.2 9.4Z"
          {...stroke}
        />
        <Polyline points="5.4,12.6 8.6,12.6 10.2,10 11.8,14.4 13.4,11.4 14.8,12.6 18.6,12.6" {...stroke} />
      </Svg>
    );
  }

  return (
    <Svg {...common}>
      <Circle cx="12" cy="8" r="3.6" {...stroke} />
      <Path d="M4.8 20.2c.7-3.7 3.6-5.8 7.2-5.8s6.5 2.1 7.2 5.8" {...stroke} />
    </Svg>
  );
}

/** 底部四个 tab：对话 / 记录 / 心理 / 我的。 */
export function AppTabBar({ active, onChange }: Props) {
  return (
    <View style={styles.bar} accessibilityRole="tablist">
      {APP_TABS.map((tab) => {
        const selected = tab.key === active;
        const color = selected ? colors.primaryPressed : colors.faint;
        return (
          <Pressable
            key={tab.key}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            accessibilityLabel={`${tab.label}标签页`}
            onPress={() => onChange(tab.key)}
            style={styles.item}
          >
            <TabIcon color={color} tab={tab.key} />
            <Text style={[styles.label, selected ? styles.labelActive : null]}>
              {tab.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
    backgroundColor: colors.paper,
    paddingTop: spacing.xs,
    paddingBottom: spacing.sm,
  },
  item: {
    flex: 1,
    alignItems: "center",
    gap: 2,
    paddingVertical: spacing.xs,
  },
  label: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
  labelActive: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontWeight: "600",
  },
});
