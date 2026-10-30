import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { ApiClient } from "../api/client";
import type {
  CgmSample,
  GlucoseReading,
  GlucoseReport,
  GlucoseSummary,
  GlucoseTarget,
  MealLog,
  MedicationLog,
  WearableDashboard,
} from "../api/types";
import { DEFAULT_GLUCOSE_TARGET } from "../api/types";
import { colors, radii, spacing, typefaces } from "../ui/theme";
import { DeviceChannels } from "./device-channels";
import type {
  GadgetbridgeController,
  HealthConnectController,
} from "./device-channels";
import { EventTimeField } from "../ui/event-time-field";
import { GlucoseReportDetail } from "../glucose/report-detail";
import { GlucoseTargetSheet } from "../glucose/target-sheet";
import { openOriginalReport } from "../glucose/open-original";
import { measuredTimeLabel } from "../glucose/measured-time";
import Svg, { Circle, Line, Path, Polyline, Rect } from "react-native-svg";

/**
 * 五个分类各给一个小图标。
 *
 * 全部自绘而不是引图标库：这五个形状简单，自绘能保证未选中时线条也清楚，
 * 也省得为一排 chip 去多认一套图标命名。
 */
function RecordTabIcon({ tab, active }: { tab: RecordTabKey; active: boolean }) {
  const stroke = active ? colors.paper : colors.muted;
  const line = {
    fill: "none" as const,
    stroke,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.6,
  };
  return (
    <Svg height={15} viewBox="0 0 24 24" width={15}>
      {tab === "glucose" ? (
        <Path d="M12 3.5c2.9 4.4 5.6 7.5 5.6 10.3a5.6 5.6 0 1 1-11.2 0C6.4 11 9.1 7.9 12 3.5Z" {...line} />
      ) : null}
      {tab === "meals" ? (
        <Path d="M4 12.5h16a8 8 0 0 1-16 0Zm8-8.5v3" {...line} />
      ) : null}
      {tab === "medication" ? (
        <Path d="M8.5 8.5h7a3.75 3.75 0 0 1 0 7.5h-7a3.75 3.75 0 0 1 0-7.5Zm.5 0 6 7.5" {...line} />
      ) : null}
      {tab === "activity" ? (
        <Path d="M4 18h8.5l3.5-2 4 2v1H4Zm2-8 3.5-3 3 2.5-4 3.5" {...line} />
      ) : null}
      {tab === "sleep" ? (
        <Path d="M20 14.5A8 8 0 1 1 9.5 4a6.6 6.6 0 0 0 10.5 10.5Z" {...line} />
      ) : null}
    </Svg>
  );
}

export type RecordTabKey = "glucose" | "meals" | "medication" | "activity" | "sleep";

export const RECORD_TABS: { key: RecordTabKey; label: string }[] = [
  { key: "glucose", label: "血糖" },
  { key: "meals", label: "饮食" },
  { key: "medication", label: "用药" },
  { key: "activity", label: "运动" },
  { key: "sleep", label: "睡眠" },
];

const GLUCOSE_CONTEXT_LABELS: Record<string, string> = {
  fasting: "空腹",
  pre_meal: "餐前",
  post_meal: "餐后",
  bedtime: "睡前",
  night: "夜间",
  other: "随时",
};

// 数值从哪来：指尖血、传感器、化验单……患者自己知道，界面上要能看见。
const SOURCE_LABELS: Record<string, string> = {
  fingerstick: "指尖血",
  cgm: "传感器",
  lab: "化验单",
  manual: "手动",
  report: "报告解读",
};

/** 事件时刻和记录时刻分开写：补记时这两个时间不一样，得一眼看得出来。 */
function recordedLine(occurredAt: string, createdAt: string): string {
  const occurred = new Date(occurredAt).getTime();
  const recorded = new Date(createdAt).getTime();
  if (Number.isNaN(occurred) || Number.isNaN(recorded)) return "";
  const late = Math.abs(recorded - occurred) >= 5 * 60_000;
  return `记录于 ${formatMeasuredAt(createdAt)}${late ? "（补记）" : ""}`;
}

const MEAL_LABELS: Record<string, string> = {
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
  snack: "加餐",
};

const MEDICATION_LABELS: Record<string, string> = {
  insulin: "胰岛素",
  oral: "口服药",
  other: "其它",
};

// 份量档位只用来给一个粗估，患者可以改；不做食物库、不做精确营养计算。
const PORTIONS: { label: string; grams: number }[] = [
  { label: "半碗", grams: 20 },
  { label: "一碗", grams: 45 },
  { label: "两碗", grams: 80 },
];

// 记的是真实发生的时间：患者可以自己选到具体某天的某个时刻，见 EventTimeField。

export function formatGlucose(reading: GlucoseReading): string {
  return `${(reading.valueMgDl / 18).toFixed(1)} mmol/L`;
}

/** mg/dL → mmol/L 的一位数文本，目标范围与绿带图例都走它。 */
export function mmolValue(mgDl: number): string {
  return (mgDl / 18).toFixed(1);
}

export function formatMeasuredAt(iso: string): string {
  const date = new Date(iso);
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hour = `${date.getHours()}`.padStart(2, "0");
  const minute = `${date.getMinutes()}`.padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}

/** 报告只写日期（05-30 至 06-11），不带时刻，所以单独一个格式。 */
export function formatReportPeriod(report: GlucoseReport): string {
  const shape = (iso: string): string => {
    const parts = iso.split("-");
    return parts.length === 3 ? `${parts[1]}-${parts[2]}` : iso;
  };
  return `${shape(report.periodStart)} 至 ${shape(report.periodEnd)}`;
}

/** 报告上「低血糖风险」的档位，说成患者能听懂的话。 */
export const LBGI_LABELS: Record<string, string> = {
  high: "低血糖风险高",
  moderate: "低血糖风险中",
  low: "低血糖风险低",
  lowest: "低血糖风险最低",
};

/** 报告速览一行：只写报告上真有的指标，没写的不补。 */
export function reportLine(report: GlucoseReport): string {
  const parts: string[] = [];
  if (report.device.trim().length > 0) parts.push(report.device.trim());
  if (report.meanMmol !== null) parts.push(`平均 ${report.meanMmol} mmol/L`);
  if (report.tirPercent !== null) parts.push(`目标内 ${report.tirPercent}%`);
  if (report.tbrPercent !== null) parts.push(`低于目标 ${report.tbrPercent}%`);
  if (report.tarPercent !== null) parts.push(`高于目标 ${report.tarPercent}%`);
  if (report.ehba1cPercent !== null) parts.push(`预估糖化 ${report.ehba1cPercent}%`);
  if (report.cvPercent !== null) parts.push(`变异系数 ${report.cvPercent}%`);
  if (report.sdMmol !== null) parts.push(`标准差 ${report.sdMmol}`);
  const lbgi = LBGI_LABELS[report.lbgiLevel];
  if (lbgi !== undefined) parts.push(lbgi);
  return parts.length === 0 ? "这份报告没有可显示的数字" : parts.join(" · ");
}

/** 报告按五档给占比时，另起一行照原样列出来；合并过的三档对不上原数。 */
export function reportBandsLine(report: GlucoseReport): string | null {
  const bands: string[] = [];
  if (report.normalPercent !== null) bands.push(`正常 ${report.normalPercent}%`);
  if (report.lowPercent !== null) bands.push(`低 ${report.lowPercent}%`);
  if (report.veryLowPercent !== null) bands.push(`很低 ${report.veryLowPercent}%`);
  if (report.highPercent !== null) bands.push(`高 ${report.highPercent}%`);
  if (report.veryHighPercent !== null) bands.push(`很高 ${report.veryHighPercent}%`);
  if (bands.length === 0) return null;
  return `分档 ${bands.join(" · ")}`;
}

export function eventIso(minutesAgo: number): string {
  const now = new Date(Date.now() - minutesAgo * 60_000);
  const offset = -now.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const hours = `${Math.floor(Math.abs(offset) / 60)}`.padStart(2, "0");
  const minutes = `${Math.abs(offset) % 60}`.padStart(2, "0");
  return `${now.getFullYear()}-${`${now.getMonth() + 1}`.padStart(2, "0")}-${`${now.getDate()}`.padStart(2, "0")}T${`${now.getHours()}`.padStart(2, "0")}:${`${now.getMinutes()}`.padStart(2, "0")}:00${sign}${hours}:${minutes}`;
}

export function nowIso(): string {
  return eventIso(0);
}

// 曲线上只看 2.5–16.7 mmol/L：再往外的极端值拉满一屏会把日常波动压成一条直线。
const CGM_FLOOR = 2.5;
const CGM_CEIL = 16.7;
const CHART_W = 300;
const CHART_H = 110;

function dayStartOf(value: string): number {
  const date = new Date(value);
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  return start.getTime();
}

/**
 * 传感器曲线：一天一条线，绿色带是患者自己的目标区间（默认 3.9–10.0）。
 *
 * 只画「最新那天」而不是把 7 天叠在一张图上：叠图看起来很热闹，但患者真正要判断
 * 的是「今天什么时候低过、什么时候冲上去」，多天叠在一起反而读不出来。
 * 缺口就是缺口——没有采样的时段保持空白，不做插值。
 */
function CgmCurve({
  samples,
  target,
}: {
  samples: CgmSample[];
  target: GlucoseTarget;
}) {
  if (samples.length === 0) return null;
  const newest = samples.reduce(
    (latest, sample) =>
      new Date(sample.recordedAt).getTime() > new Date(latest.recordedAt).getTime()
        ? sample
        : latest,
    samples[0],
  );
  const dayStart = dayStartOf(newest.recordedAt);
  const dayEnd = dayStart + 24 * 60 * 60 * 1000;
  const daySamples = samples
    .filter((sample) => {
      const time = new Date(sample.recordedAt).getTime();
      return time >= dayStart && time < dayEnd;
    })
    .map((sample) => ({
      time: new Date(sample.recordedAt).getTime(),
      value: sample.valueMgDl / 18,
    }))
    .sort((left, right) => left.time - right.time);

  if (daySamples.length < 2) return null;

  const x = (time: number) => ((time - dayStart) / (24 * 60 * 60 * 1000)) * CHART_W;
  const y = (value: number) => {
    const clamped = Math.min(Math.max(value, CGM_FLOOR), CGM_CEIL);
    return CHART_H - ((clamped - CGM_FLOOR) / (CGM_CEIL - CGM_FLOOR)) * CHART_H;
  };
  const bandTop = y(target.highMgDl / 18);
  const bandBottom = y(target.lowMgDl / 18);
  const points = daySamples.map((sample) => `${x(sample.time)},${y(sample.value)}`).join(" ");
  const dayLabel = `${new Date(dayStart).getMonth() + 1} 月 ${new Date(dayStart).getDate()} 日`;

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{dayLabel} · 传感器曲线</Text>
      <Svg height={CHART_H} viewBox={`0 0 ${CHART_W} ${CHART_H}`} width="100%">
        <Rect
          fill="#E7F7F2"
          height={bandBottom - bandTop}
          width={CHART_W}
          x={0}
          y={bandTop}
        />
        <Line
          stroke="#9BC9BB"
          strokeDasharray="4 4"
          strokeWidth={1}
          x1={0}
          x2={CHART_W}
          y1={bandTop}
          y2={bandTop}
        />
        <Line
          stroke="#9BC9BB"
          strokeDasharray="4 4"
          strokeWidth={1}
          x1={0}
          x2={CHART_W}
          y1={bandBottom}
          y2={bandBottom}
        />
        <Polyline
          fill="none"
          points={points}
          stroke="#11866F"
          strokeLinejoin="round"
          strokeWidth={1.8}
        />
      </Svg>
      <View style={styles.curveLegend}>
        <Text style={styles.cardHint}>
          绿色带 {mmolValue(target.lowMgDl)}–{mmolValue(target.highMgDl)} mmol/L
        </Text>
        <Text style={styles.cardHint}>{daySamples.length} 个采样</Text>
      </View>
      <Text style={styles.cardHint}>
        只画这一天的采样；没有采样的时段留空，不插值补点。
      </Text>
    </View>
  );
}

type TrendPoint = {
  /** 患者只说得出时段（早上 / 晚上），横轴位置是近似的。 */
  approximate: boolean;
  reported: boolean;
  time: number;
  value: number;
};

/** 趋势图看多久：和血糖摘要同一个窗口，患者不需要再学一个新的时间概念。 */
export const TREND_DAYS = 7;

/**
 * 手输的血糖和报告里记下的点，排成一条按时间的序列。
 *
 * 只留近 7 天、按测量时间升序；作废的记录不算。做纯函数是为了能单独验证
 * 「报告的点确实进得来」，而不用去数图上的像素。
 */
export function trendSeries(
  readings: readonly GlucoseReading[],
  now: number = Date.now(),
): TrendPoint[] {
  const since = now - TREND_DAYS * 24 * 60 * 60 * 1000;
  return readings
    .filter((reading) => reading.revokedAt === null)
    .map((reading) => ({
      approximate: reading.measuredPrecision === "daypart",
      // 空心 = 报告 / 导入进来的数字；实心 = 自己量的。判据和下面「手动 / 传感器」
      // 两个子栏完全一致（`manualReadings` 排除的正是这两类），否则同一份数据在
      // 列表里算「手动」、在图里算「报告」，患者会觉得哪里不对。
      reported: reading.source === "report" || reading.source === "cgm",
      time: new Date(reading.measuredAt).getTime(),
      value: reading.valueMgDl / 18,
    }))
    .filter(
      (point) =>
        Number.isFinite(point.time) &&
        point.time >= since &&
        point.time <= now + 60 * 1000,
    )
    .sort((left, right) => left.time - right.time);
}

/**
 * 近 7 天的手输血糖 + 报告读数，一条线。
 *
 * 两个来源要是分成两张图，患者会问「我报告里的数去哪了」；混在一起又得能分清
 * 哪个点是自己扎手指量的、哪个是报告里的。所以一条折线、两种点：
 * 实心 = 自己量的，空心 = 报告里记下的。没有测量的时段留空，不插值。
 */
function GlucoseTrend({
  now,
  readings,
  target,
}: {
  now: number | null;
  readings: readonly GlucoseReading[];
  target: GlucoseTarget;
}) {
  const points = now === null ? [] : trendSeries(readings, now);
  if (now === null || points.length < 2) return null;

  const since = now - TREND_DAYS * 24 * 60 * 60 * 1000;
  const span = Math.max(now - since, 1);
  const x = (time: number) => ((time - since) / span) * CHART_W;
  const y = (value: number) => {
    const clamped = Math.min(Math.max(value, CGM_FLOOR), CGM_CEIL);
    return CHART_H - ((clamped - CGM_FLOOR) / (CGM_CEIL - CGM_FLOOR)) * CHART_H;
  };
  const bandTop = y(target.highMgDl / 18);
  const bandBottom = y(target.lowMgDl / 18);
  const line = points
    .map((point) => `${x(point.time)},${y(point.value)}`)
    .join(" ");
  const measured = points.filter((point) => !point.reported).length;
  const reported = points.length - measured;

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>近 {TREND_DAYS} 天 · 血糖趋势</Text>
      <Svg height={CHART_H} viewBox={`0 0 ${CHART_W} ${CHART_H}`} width="100%">
        <Rect
          fill="#E7F7F2"
          height={bandBottom - bandTop}
          width={CHART_W}
          x={0}
          y={bandTop}
        />
        <Line
          stroke="#9BC9BB"
          strokeDasharray="4 4"
          strokeWidth={1}
          x1={0}
          x2={CHART_W}
          y1={bandTop}
          y2={bandTop}
        />
        <Line
          stroke="#9BC9BB"
          strokeDasharray="4 4"
          strokeWidth={1}
          x1={0}
          x2={CHART_W}
          y1={bandBottom}
          y2={bandBottom}
        />
        <Polyline
          fill="none"
          points={line}
          stroke="#11866F"
          strokeLinejoin="round"
          strokeWidth={1.8}
        />
        {points.map((point) => (
          <Circle
            cx={x(point.time)}
            cy={y(point.value)}
            fill={point.reported ? colors.paper : "#11866F"}
            key={`${point.time}-${point.value}`}
            r={point.reported ? 2.6 : 3}
            stroke="#11866F"
            strokeWidth={1.4}
          />
        ))}
      </Svg>
      <View style={styles.curveLegend}>
        <Text style={styles.cardHint}>
          绿带 {mmolValue(target.lowMgDl)}–{mmolValue(target.highMgDl)} mmol/L
        </Text>
        <Text style={styles.cardHint}>
          {measured > 0 ? `自己量的 ${measured} 次` : ""}
          {measured > 0 && reported > 0 ? " · " : ""}
          {reported > 0 ? `报告里 ${reported} 条` : ""}
        </Text>
      </View>
      <Text style={styles.cardHint}>
        实心点是你自己量的，空心点是报告里记下的；没有测量的时段留空，不插值。
      </Text>
      {points.some((point) => point.approximate) ? (
        <Text style={styles.cardHint}>
          标成「大概」的点是只说得出时段的，横轴位置按该时段起点画，不代表那一刻。
        </Text>
      ) : null}
    </View>
  );
}

type Props = {
  api: Pick<
    ApiClient,
    | "getGlucoseReadings"
    | "getGlucoseSummary"
    | "getGlucoseTarget"
    | "setGlucoseTarget"
    | "getMealLogs"
    | "createMealLog"
    | "deleteMealLog"
    | "getMedicationLogs"
    | "createMedicationLog"
    | "deleteMedicationLog"
    | "getCgmSamples"
    | "getGlucoseReports"
    | "deleteGlucoseReport"
    | "saveChatAttachment"
    | "getWearableDashboard"
  >;
  onImport: () => void;
  onRecordGlucose: () => void;
  /** 手环两条通道挂在 App 外壳上，这里展示它们的状态与失败原因。 */
  healthConnect: HealthConnectController;
  gadgetbridge: GadgetbridgeController;
  /** tab 切到「记录」时刷新一次：对话里刚记下的饭/药要能立刻出现在列表里。 */
  visible?: boolean;
};

/**
 * 记录页：血糖 / 饮食 / 用药 / 运动 / 睡眠。
 * 血糖内部再分「手动 / 传感器」两种来源；运动与睡眠的数据来自手环导入。
 *
 * 这是导航重构的提案实现：还没有接进 App.tsx，见 README-nav-v2.md。
 */
export function RecordScreen({
  api,
  onImport,
  onRecordGlucose,
  healthConnect,
  gadgetbridge,
  visible = true,
}: Props) {
  const [tab, setTab] = useState<RecordTabKey>("glucose");
  const [source, setSource] = useState<"manual" | "sensor">("manual");
  // 设备通道默认收起：三条通道只在真连不上时才需要展开看原因，
  // 天天占着半屏会把真正的记录推到下面。
  const [devicesOpen, setDevicesOpen] = useState(false);

  const [readings, setReadings] = useState<GlucoseReading[]>([]);
  const [summary, setSummary] = useState<GlucoseSummary | null>(null);
  const [meals, setMeals] = useState<MealLog[]>([]);
  const [medications, setMedications] = useState<MedicationLog[]>([]);
  const [dashboard, setDashboard] = useState<WearableDashboard | null>(null);
  const [cgmSamples, setCgmSamples] = useState<CgmSample[]>([]);
  const [glucoseReports, setGlucoseReports] = useState<GlucoseReport[]>([]);
  // 趋势图的时间轴锚点：取到数据时记一次，不在渲染期间读钟。
  const [trendNow, setTrendNow] = useState<number | null>(null);
  // 点开哪一份报告速览；null 表示详情页没开着。
  const [openReport, setOpenReport] = useState<GlucoseReport | null>(null);
  // 患者自己的目标范围：绿带、统计口径、对话里说的「高低」都按它算。
  const [target, setTarget] = useState<GlucoseTarget>(DEFAULT_GLUCOSE_TARGET);
  const [targetSheetOpen, setTargetSheetOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [mealType, setMealType] = useState<"breakfast" | "lunch" | "dinner" | "snack">(
    "lunch",
  );
  const [mealAt, setMealAt] = useState<Date>(() => new Date());
  const [mealDescription, setMealDescription] = useState("");
  const [mealCarbs, setMealCarbs] = useState<string>("");
  const [medAt, setMedAt] = useState<Date>(() => new Date());
  const [medName, setMedName] = useState("");
  const [medDose, setMedDose] = useState("");
  const [medKind, setMedKind] = useState<"insulin" | "oral" | "other">("insulin");

  useEffect(() => {
    if (!visible) {
      return;
    }
    let active = true;
    Promise.all([
      api.getGlucoseReadings(),
      api.getGlucoseSummary(),
      api.getMealLogs(),
      api.getMedicationLogs(),
      api.getWearableDashboard(),
      api.getCgmSamples().catch(() => ({ days: 7, count: 0, samples: [] })),
      api.getGlucoseReports().catch(() => [] as GlucoseReport[]),
      api.getGlucoseTarget().catch(() => DEFAULT_GLUCOSE_TARGET),
    ])
      .then(
        ([
          rows,
          nextSummary,
          nextMeals,
          nextMedications,
          nextDashboard,
          series,
          reports,
          nextTarget,
        ]) => {
          if (!active) {
            return;
          }
          setReadings(rows);
          setSummary(nextSummary);
          setMeals(nextMeals);
          setMedications(nextMedications);
          setDashboard(nextDashboard);
          setCgmSamples(series.samples);
          setGlucoseReports(reports);
          setTarget(nextTarget);
          // 趋势图的「近 7 天」以取到数据的那一刻为锚点：放在渲染里算 Date.now()
          // 会让每次重渲染都换一个时间轴，图会自己抖。
          setTrendNow(Date.now());
          setError(null);
        },
      )
      .catch((cause: unknown) => {
        if (active) {
          setError(cause instanceof Error ? cause.message : "记录读取失败");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [api, visible]);

  // 「传感器」那一栏只看导入进来的 CGM；指尖血/化验单留在「手动」。
  const manualReadings = readings.filter(
    (reading) => reading.source !== "report" && reading.source !== "cgm",
  );
  // 报告里读出来的数字也走 source=cgm/report，它们不在「手动」里，得在这栏露面，
  // 否则对话说「已经记进血糖记录」，患者翻记录页却是空的。
  const reportReadings = readings.filter(
    (reading) => reading.source === "report" || reading.source === "cgm",
  );
  const cgmSampleStats =
    cgmSamples.length === 0
      ? null
      : {
          average: (
            cgmSamples.reduce((sum, sample) => sum + sample.valueMgDl, 0) /
            cgmSamples.length /
            18
          ).toFixed(1),
          low: cgmSamples.filter((sample) => sample.valueMgDl < target.lowMgDl)
            .length,
        };

  async function saveTarget(next: GlucoseTarget) {
    setTargetSheetOpen(false);
    try {
      const saved = await api.setGlucoseTarget(next.lowMgDl, next.highMgDl);
      setTarget(saved);
      // 统计口径跟着变，重新拉一次摘要，别让「低于 4.4 的 N 次」停在旧数字上。
      const refreshed = await api.getGlucoseSummary();
      setSummary(refreshed);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "目标范围没存上");
    }
  }

  const totalSteps =
    dashboard?.sources.reduce(
      (sum, sourceItem) => sum + sourceItem.totals.steps,
      0,
    ) ?? 0;
  const sleepMinutes =
    dashboard?.sources.reduce(
      (sum, sourceItem) => sum + sourceItem.totals.sleepMinutes,
      0,
    ) ?? 0;
  const sleepSessions =
    dashboard?.sources.reduce(
      (sum, sourceItem) => sum + sourceItem.totals.sleepSessions,
      0,
    ) ?? 0;

  async function saveWater(milliliters: number) {
    setSaving(true);
    try {
      const created = await api.createMealLog({
        eatenAt: new Date().toISOString(),
        mealType: "snack",
        description: `喝水 ${milliliters} ml`,
        carbsGrams: null,
      });
      setMeals((current) => [created, ...current]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "这杯水没记上");
    } finally {
      setSaving(false);
    }
  }

  async function saveMeal() {
    setSaving(true);
    try {
      const created = await api.createMealLog({
        eatenAt: mealAt.toISOString(),
        mealType,
        description: mealDescription.trim(),
        carbsGrams: mealCarbs.trim() === "" ? null : Number(mealCarbs),
      });
      setMeals((current) => [created, ...current]);
      setMealDescription("");
      setMealCarbs("");
      setMealAt(new Date());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "这餐没记上");
    } finally {
      setSaving(false);
    }
  }

  async function saveMedication() {    setSaving(true);
    try {
      const created = await api.createMedicationLog({
        takenAt: medAt.toISOString(),
        name: medName.trim(),
        doseText: medDose.trim(),
        kind: medKind,
      });
      setMedications((current) => [created, ...current]);
      setMedName("");
      setMedDose("");
      setMedAt(new Date());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "这条用药没记上");
    } finally {
      setSaving(false);
    }
  }

  async function removeReport(report: GlucoseReport) {
    setOpenReport(null);
    try {
      await api.deleteGlucoseReport(report.id);
      setGlucoseReports((current) => current.filter((item) => item.id !== report.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "这份报告速览没删掉");
    }
  }

  async function openReportOriginal(report: GlucoseReport) {
    if (report.sourceAttachmentId === null) return;
    try {
      await openOriginalReport(api, report.sourceAttachmentId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "原报告没打开");
    }
  }

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.title}>记录</Text>
        <Text style={styles.headerHint}>最近 7 天</Text>
      </View>

      {/* 五个分类芯片：不横向滚动，放不下就换行——横向滚动会把「睡眠」切掉半个。 */}
      <View style={styles.tabRow}>
        {RECORD_TABS.map((item) => {
          const selected = item.key === tab;
          return (
            <Pressable
              key={item.key}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              accessibilityLabel={`${item.label}分类`}
              onPress={() => setTab(item.key)}
              style={[styles.tab, selected ? styles.tabActive : null]}
            >
              <RecordTabIcon active={selected} tab={item.key} />
              <Text style={[styles.tabText, selected ? styles.tabTextActive : null]}>
                {item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <ScrollView contentContainerStyle={styles.body}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={devicesOpen ? "收起设备与手环" : "展开设备与手环"}
          accessibilityState={{ expanded: devicesOpen }}
          onPress={() => setDevicesOpen((open) => !open)}
          style={styles.deviceBar}
        >
          <View>
            <Text style={styles.deviceBarTitle}>设备与手环</Text>
            <Text style={styles.deviceBarHint}>
              系统健康数据 · 导出文件 · 实时心率
            </Text>
          </View>
          <Text style={styles.deviceBarAction}>{devicesOpen ? "收起" : "展开"}</Text>
        </Pressable>
        {devicesOpen ? (
          <DeviceChannels gadgetbridge={gadgetbridge} healthConnect={healthConnect} />
        ) : null}

        {loading ? <ActivityIndicator color={colors.primary} /> : null}
        {error !== null ? <Text style={styles.error}>{error}</Text> : null}

        {tab === "glucose" ? (
          <>
            <View style={styles.segment}>
              {(["manual", "sensor"] as const).map((key) => (
                <Pressable
                  key={key}
                  accessibilityRole="button"
                  accessibilityLabel={key === "manual" ? "手动记录" : "传感器来源"}
                  accessibilityState={{ selected: source === key }}
                  onPress={() => setSource(key)}
                  style={[
                    styles.segmentItem,
                    source === key ? styles.segmentItemActive : null,
                  ]}
                >
                  <Text
                    style={[
                      styles.segmentText,
                      source === key ? styles.segmentTextActive : null,
                    ]}
                  >
                    {key === "manual" ? "手动" : "传感器"}
                  </Text>
                </Pressable>
              ))}
            </View>

            {/* 两个子栏共用一张趋势图：手输的点、报告里的点都在这条线上，
                切到哪一栏都看得见自己最近的高低起伏。 */}
            <GlucoseTrend now={trendNow} readings={readings} target={target} />

            {/* 目标范围是医生给的数字，患者自己能改；绿带与统计都按它算。 */}
            <Pressable
              accessibilityLabel="修改血糖目标范围"
              onPress={() => setTargetSheetOpen(true)}
              style={styles.card}
            >
              <View style={styles.targetRow}>
                <Text style={styles.cardBody}>
                  {`目标范围 ${mmolValue(target.lowMgDl)}–${mmolValue(target.highMgDl)} mmol/L`}
                </Text>
                <Text style={styles.targetAction}>按医生给的改</Text>
              </View>
            </Pressable>

            {source === "manual" ? (
              <>
                {summary !== null && summary.count > 0 ? (
                  <View style={styles.card}>
                    <Text style={styles.cardTitle}>最近 7 天</Text>
                    <Text style={styles.cardBody}>
                      {`平均 ${
                        summary.averageMgDl === null
                          ? "—"
                          : `${(summary.averageMgDl / 18).toFixed(1)} mmol/L`
                      } · 记录 ${summary.count} 次 · 低于 ${mmolValue(
                        summary.targetLowMgDl,
                      )} 共 ${summary.belowTargetCount} 次`}
                    </Text>
                  </View>
                ) : null}
                {manualReadings.length === 0 && !loading ? (
                  <View style={styles.card}>
                    <Text style={styles.cardTitle}>还没有记录</Text>
                    <Text style={styles.cardBody}>
                      手上有刚测的数字就记一条，之后对话里可以直接引用。
                    </Text>
                  </View>
                ) : null}
                {manualReadings.map((reading) => (
                  <View key={reading.id} style={styles.card}>
                    <Text style={styles.cardTitle}>{formatGlucose(reading)}</Text>
                    <Text style={styles.cardBody}>
                       测量{" "}
                       {measuredTimeLabel(
                         reading.measuredAt,
                         reading.measuredPrecision,
                       )}{" "}
                       ·{" "}
                      {SOURCE_LABELS[reading.source] ?? "手动"} ·{" "}
                      {GLUCOSE_CONTEXT_LABELS[reading.context] ?? reading.context}
                      {reading.note === null ? "" : ` ·「${reading.note}」`}
                    </Text>
                    <Text style={styles.cardHint}>
                      {recordedLine(reading.measuredAt, reading.createdAt)}
                    </Text>
                  </View>
                ))}
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="记一条血糖"
                  onPress={onRecordGlucose}
                  style={styles.primaryButton}
                >
                  <Text style={styles.primaryButtonText}>记一条血糖</Text>
                </Pressable>
              </>
            ) : (
              <>
                <View style={styles.card}>
                  <Text style={styles.cardTitle}>
                    {cgmSamples.length === 0
                      ? reportReadings.length === 0 && glucoseReports.length === 0
                        ? "还没有导入传感器数据"
                        : reportReadings.length === 0
                          ? `报告速览 ${glucoseReports.length} 份`
                          : `报告里记下 ${reportReadings.length} 条`
                      : `已导入 ${cgmSamples.length} 条`}
                  </Text>
                  <Text style={styles.cardBody}>
                    从厂家 App 导出 CSV，先预览能用的行与可疑行，确认后才入库。
                  </Text>
                </View>
                {glucoseReports.map((report) => (
                  <Pressable
                    accessibilityLabel={`报告速览 ${formatReportPeriod(report)}`}
                    accessibilityRole="button"
                    key={report.id}
                    onPress={() => setOpenReport(report)}
                    style={styles.card}
                  >
                    <Text style={styles.cardTitle}>
                      {`${formatReportPeriod(report)} · 报告速览`}
                    </Text>
                    <Text style={styles.cardBody}>
                      {reportLine(report)}
                    </Text>
                    {reportBandsLine(report) === null ? null : (
                      <Text style={styles.cardBody}>{reportBandsLine(report)}</Text>
                    )}
                    {report.note === null ? null : (
                      <Text style={styles.cardHint}>{report.note}</Text>
                    )}
                    <Text style={styles.cardAction}>看全部指标</Text>
                  </Pressable>
                ))}
                {reportReadings.slice(0, 5).map((reading) => (
                  <View key={reading.id} style={styles.card}>
                    <Text style={styles.cardTitle}>{formatGlucose(reading)}</Text>
                    <Text style={styles.cardBody}>
                      {measuredTimeLabel(
                        reading.measuredAt,
                        reading.measuredPrecision,
                      )}{" "}
                      · {SOURCE_LABELS[reading.source] ?? "传感器"}
                      {reading.note === null ? "" : ` ·「${reading.note}」`}
                    </Text>
                  </View>
                ))}
                {cgmSamples.length === 0 ? null : (
                  <CgmCurve samples={cgmSamples} target={target} />
                )}
                {cgmSampleStats === null ? null : (
                  <View style={styles.metricRow}>
                    <View style={[styles.metricSmall, styles.metricSmallPrimary]}>
                      <Text style={styles.metricSmallLabel}>平均值</Text>
                      <Text style={styles.metricSmallValue}>
                        {cgmSampleStats.average} mmol/L
                      </Text>
                    </View>
                    <View style={[styles.metricSmall, styles.metricSmallSleep]}>
                      <Text style={styles.metricSmallLabel}>
                        低于 {mmolValue(target.lowMgDl)}
                      </Text>
                      <Text style={styles.metricSmallValue}>
                        {cgmSampleStats.low} 次
                      </Text>
                    </View>
                  </View>
                )}
                {cgmSamples
                  .slice(-3)
                  .reverse()
                  .map((sample) => (
                    <View key={`${sample.recordedAt}-${sample.valueMgDl}`} style={styles.card}>
                      <Text style={styles.cardTitle}>
                        {(sample.valueMgDl / 18).toFixed(1)}
                        <Text style={styles.cardBody}> mmol/L</Text>
                      </Text>
                      <Text style={styles.cardBody}>
                        {formatMeasuredAt(sample.recordedAt)} · 传感器
                      </Text>
                    </View>
                  ))}
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="导入传感器数据"
                  onPress={onImport}
                  style={styles.primaryButton}
                >
                  <Text style={styles.primaryButtonText}>导入传感器数据</Text>
                </Pressable>
              </>
            )}
          </>
        ) : null}

        {tab === "meals" ? (
          <>
            {meals.length === 0 ? (
              <View style={styles.card}>
                <Text style={styles.cardTitle}>还没有记过饭</Text>
                <Text style={styles.cardBody}>
                  记「吃了什么 + 大概多少」就够了，碳水是估计值，可以改。
                </Text>
              </View>
            ) : null}
            {meals.map((meal) => (
              <View key={meal.id} style={styles.card}>
                <Text style={styles.cardTitle}>
                  {MEAL_LABELS[meal.mealType] ?? meal.mealType}
                  {meal.carbsGrams === null ? "" : ` · 碳水约 ${meal.carbsGrams} g`}
                </Text>
                <Text style={styles.cardBody}>
                  {formatMeasuredAt(meal.eatenAt)}
                  {meal.description === "" ? "" : ` · ${meal.description}`}
                </Text>
                <Text style={styles.cardHint}>
                  {recordedLine(meal.eatenAt, meal.createdAt)}
                </Text>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="删除这条饮食记录"
                  onPress={() =>
                    void api
                      .deleteMealLog(meal.id)
                      .then(() =>
                        setMeals((current) => current.filter((row) => row.id !== meal.id)),
                      )
                      .catch((cause: unknown) =>
                        setError(cause instanceof Error ? cause.message : "删除失败"),
                      )
                  }
                >
                  <Text style={styles.linkDanger}>删除</Text>
                </Pressable>
              </View>
            ))}

            <View style={styles.form}>
              <Text style={styles.formTitle}>记一餐</Text>
              <View style={styles.chipRow}>
                {(["breakfast", "lunch", "dinner", "snack"] as const).map((key) => (
                  <Pressable
                    key={key}
                    accessibilityRole="button"
                    accessibilityLabel={`选择${MEAL_LABELS[key] ?? key}`}
                    onPress={() => setMealType(key)}
                    style={[styles.chip, mealType === key ? styles.chipActive : null]}
                  >
                    <Text
                      style={[styles.chipText, mealType === key ? styles.chipTextActive : null]}
                    >
                      {MEAL_LABELS[key] ?? key}
                    </Text>
                  </Pressable>
                ))}
              </View>
              <TextInput
                accessibilityLabel="这一餐吃了什么"
                placeholder="吃了什么（可选）"
                placeholderTextColor={colors.faint}
                value={mealDescription}
                onChangeText={setMealDescription}
                style={styles.input}
                maxLength={200}
              />
              <View style={styles.chipRow}>
                {PORTIONS.map((portion) => (
                  <Pressable
                    key={portion.label}
                    accessibilityRole="button"
                    accessibilityLabel={`份量${portion.label}`}
                    onPress={() => setMealCarbs(`${portion.grams}`)}
                    style={[styles.chip, mealCarbs === `${portion.grams}` ? styles.chipActive : null]}
                  >
                    <Text
                      style={[
                        styles.chipText,
                        mealCarbs === `${portion.grams}` ? styles.chipTextActive : null,
                      ]}
                    >
                      {portion.label}
                    </Text>
                  </Pressable>
                ))}
                <TextInput
                  accessibilityLabel="碳水估计值"
                  placeholder="碳水 g"
                  placeholderTextColor={colors.faint}
                  value={mealCarbs}
                  onChangeText={setMealCarbs}
                  keyboardType="number-pad"
                  style={[styles.input, styles.inputNarrow]}
                  maxLength={3}
                />
              </View>
              <EventTimeField
                accessibilityLabel="这一餐是什么时候吃的"
                label="这一餐是什么时候吃的"
                onChange={setMealAt}
                value={mealAt}
              />
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="保存这餐"
                onPress={() => void saveMeal()}
                disabled={saving}
                style={[styles.primaryButton, saving ? styles.buttonDisabled : null]}
              >
                <Text style={styles.primaryButtonText}>保存这餐</Text>
              </Pressable>
            </View>

            {/* 喝水不单开一个分类：它不是营养，也不分餐次，但口渴、喝得多、尿多
                恰恰是高血糖的信号，所以随手记在这儿，对话里一样看得到。 */}
            <View style={styles.form}>
              <Text style={styles.formTitle}>只记喝水</Text>
              <Text style={styles.formHint}>
                喝水不记碳水和份量，算 0 碳水；口渴喝得多这件事本身值得留一条。
              </Text>
              <View style={styles.chipRow}>
                {[200, 300, 500].map((milliliters) => (
                  <Pressable
                    key={milliliters}
                    accessibilityRole="button"
                    accessibilityLabel={`记喝水 ${milliliters} 毫升`}
                    onPress={() => void saveWater(milliliters)}
                    disabled={saving}
                    style={[styles.chip, saving ? styles.buttonDisabled : null]}
                  >
                    <Text style={styles.chipText}>{milliliters} ml</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          </>
        ) : null}

        {tab === "medication" ? (
          <>
            {medications.length === 0 ? (
              <View style={styles.card}>
                <Text style={styles.cardTitle}>还没有记过用药</Text>
                <Text style={styles.cardBody}>
                  记下药名、剂量和时间。这里只记录，不算剂量、不给建议。
                </Text>
              </View>
            ) : null}
            {medications.map((log) => (
              <View key={log.id} style={styles.card}>
                <Text style={styles.cardTitle}>
                  {log.name}
                  {log.doseText === "" ? "" : ` · ${log.doseText}`}
                </Text>
                <Text style={styles.cardBody}>
                  {formatMeasuredAt(log.takenAt)} · {MEDICATION_LABELS[log.kind] ?? log.kind}
                  {log.note === null ? "" : ` · ${log.note}`}
                </Text>
                <Text style={styles.cardHint}>
                  {recordedLine(log.takenAt, log.createdAt)}
                </Text>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="删除这条用药记录"
                  onPress={() =>
                    void api
                      .deleteMedicationLog(log.id)
                      .then(() =>
                        setMedications((current) =>
                          current.filter((row) => row.id !== log.id),
                        ),
                      )
                      .catch((cause: unknown) =>
                        setError(cause instanceof Error ? cause.message : "删除失败"),
                      )
                  }
                >
                  <Text style={styles.linkDanger}>删除</Text>
                </Pressable>
              </View>
            ))}

            <View style={styles.form}>
              <Text style={styles.formTitle}>记一次用药</Text>
              <View style={styles.chipRow}>
                {(["insulin", "oral", "other"] as const).map((key) => (
                  <Pressable
                    key={key}
                    accessibilityRole="button"
                    accessibilityLabel={`选择${MEDICATION_LABELS[key] ?? key}`}
                    onPress={() => setMedKind(key)}
                    style={[styles.chip, medKind === key ? styles.chipActive : null]}
                  >
                    <Text
                      style={[styles.chipText, medKind === key ? styles.chipTextActive : null]}
                    >
                      {MEDICATION_LABELS[key] ?? key}
                    </Text>
                  </Pressable>
                ))}
              </View>
              <TextInput
                accessibilityLabel="药名"
                placeholder="药名（例如 诺和锐）"
                placeholderTextColor={colors.faint}
                value={medName}
                onChangeText={setMedName}
                style={styles.input}
                maxLength={64}
              />
              <TextInput
                accessibilityLabel="剂量"
                placeholder="剂量（例如 6 单位 / 一片）"
                placeholderTextColor={colors.faint}
                value={medDose}
                onChangeText={setMedDose}
                style={styles.input}
                maxLength={64}
              />
              <EventTimeField
                accessibilityLabel="这次用药是什么时候"
                label="这次用药是什么时候"
                onChange={setMedAt}
                value={medAt}
              />
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="保存这次用药"
                onPress={() => void saveMedication()}
                disabled={saving || medName.trim() === ""}
                style={[
                  styles.primaryButton,
                  saving || medName.trim() === "" ? styles.buttonDisabled : null,
                ]}
              >
                <Text style={styles.primaryButtonText}>保存这次用药</Text>
              </Pressable>
            </View>
          </>
        ) : null}

        {tab === "activity" ? (
          <>
            <View style={styles.metricCard}>
              <Text style={styles.metricLabel}>近 7 天步数</Text>
              <Text style={styles.metricValue}>
                {totalSteps.toLocaleString()}
                <Text style={styles.metricUnit}> 步</Text>
              </Text>
              <Text style={styles.cardHint}>
                数据来自手环导入（华为运动健康 / Health Connect），不需要再手填。
              </Text>
            </View>
            <View style={styles.metricRow}>
              <View style={[styles.metricSmall, styles.metricSmallPrimary]}>
                <Text style={styles.metricSmallLabel}>活动</Text>
                <Text style={styles.metricSmallValue}>
                  {totalSteps > 0 ? "有记录" : "暂无"}
                </Text>
              </View>
              <View style={[styles.metricSmall, styles.metricSmallSleep]}>
                <Text style={styles.metricSmallLabel}>睡眠</Text>
                <Text style={styles.metricSmallValue}>
                  {sleepMinutes > 0 ? `${Math.round(sleepMinutes / 60)} 小时` : "暂无"}
                </Text>
              </View>
            </View>
            <View style={styles.card}>
              <Text style={styles.cardTitle}>对话里提到的回避</Text>
              <Text style={styles.cardBody}>
                「不敢运动」这类话会单独标出来，不和你真的做了多少混在一起。
              </Text>
            </View>
          </>
        ) : null}

        {tab === "sleep" ? (
          <>
            <View style={styles.metricCard}>
              <Text style={styles.metricLabel}>近 7 天睡眠</Text>
              <Text style={styles.metricValue}>
                {sleepMinutes > 0 ? `约 ${Math.round(sleepMinutes / 60)} 小时` : "—"}
              </Text>
              <Text style={styles.cardBody}>
                {sleepSessions > 0 ? `共 ${sleepSessions} 段` : "还没有导入睡眠数据"}
              </Text>
              <Text style={styles.cardHint}>来自手环导入，不用手动填。</Text>
            </View>
            <View style={styles.metricRow}>
              <View style={[styles.metricSmall, styles.metricSmallSleep]}>
                <Text style={styles.metricSmallLabel}>平均每段</Text>
                <Text style={styles.metricSmallValue}>
                  {sleepSessions > 0
                    ? `约 ${Math.max(1, Math.round(sleepMinutes / sleepSessions / 60))} 小时`
                    : "暂无"}
                </Text>
              </View>
              <View style={[styles.metricSmall, styles.metricSmallPrimary]}>
                <Text style={styles.metricSmallLabel}>数据段</Text>
                <Text style={styles.metricSmallValue}>{sleepSessions} 段</Text>
              </View>
            </View>
          </>
        ) : null}
      </ScrollView>

      {openReport === null ? null : (
        <GlucoseReportDetail
          onClose={() => setOpenReport(null)}
          onDelete={() => void removeReport(openReport)}
          onOpenOriginal={
            openReport.sourceAttachmentId === null
              ? undefined
              : () => void openReportOriginal(openReport)
          }
          report={openReport}
        />
      )}

      <GlucoseTargetSheet
        onCancel={() => setTargetSheetOpen(false)}
        onSave={(next) => void saveTarget(next)}
        target={target}
        visible={targetSheetOpen}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  header: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    paddingBottom: spacing.xs,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 24,
    fontWeight: "700",
  },
  headerHint: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  tabRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingTop: 2,
  },
  tab: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.paperMuted,
  },
  tabActive: {
    backgroundColor: colors.primary,
  },
  tabText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  tabTextActive: {
    color: "#FFFFFF",
    fontFamily: typefaces.sansMedium,
    fontWeight: "600",
  },
  deviceBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.md,
    backgroundColor: colors.paper,
  },
  deviceBarTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
    fontWeight: "600",
  },
  deviceBarHint: {
    marginTop: 2,
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
  },
  deviceBarAction: {
    color: colors.primary,
    fontFamily: typefaces.sansMedium,
    fontSize: 12.5,
    fontWeight: "600",
  },
  body: {
    padding: spacing.md,
    gap: spacing.sm,
  },
  segment: {
    flexDirection: "row",
    backgroundColor: colors.paperMuted,
    borderRadius: radii.pill,
    padding: 3,
  },
  segmentItem: {
    flex: 1,
    alignItems: "center",
    paddingVertical: 6,
    borderRadius: radii.pill,
  },
  segmentItemActive: {
    backgroundColor: colors.paper,
  },
  segmentText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12.5,
  },
  segmentTextActive: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontWeight: "600",
  },
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    padding: spacing.md,
    gap: 4,
  },
  cardTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
    fontWeight: "600",
  },
  cardBody: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12.5,
    lineHeight: 19,
  },
  cardHint: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
    lineHeight: 17,
  },
  cardAction: {
    marginTop: 8,
    color: colors.primary,
    fontFamily: typefaces.sansMedium,
    fontSize: 12.5,
  },
  targetRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  targetAction: {
    color: colors.primary,
    fontFamily: typefaces.sansMedium,
    fontSize: 12.5,
  },
  curveLegend: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 6,
  },
  // 手环指标沿用健康看板的样子：一个数字 + 单位 + 一句说明，不是一段文字。
  metricCard: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    padding: spacing.md,
    gap: 2,
  },
  metricLabel: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  metricValue: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 34,
    fontWeight: "600",
  },
  metricUnit: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
    fontWeight: "400",
  },
  metricRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  metricSmall: {
    flex: 1,
    borderRadius: radii.lg,
    padding: spacing.sm,
    gap: 2,
  },
  metricSmallPrimary: {
    backgroundColor: colors.primarySoft,
  },
  metricSmallSleep: {
    backgroundColor: "#EDEFFA",
  },
  metricSmallLabel: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
  },
  metricSmallValue: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 18,
    fontWeight: "600",
  },
  fieldLabel: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 4,
  },
  error: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 12.5,
  },
  linkDanger: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 12,
    marginTop: 4,
  },
  form: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    padding: spacing.md,
    gap: spacing.xs,
  },
  formTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
    fontWeight: "600",
    marginBottom: 2,
  },
  formHint: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
    lineHeight: 17,
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs,
    alignItems: "center",
  },
  chip: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.paperMuted,
  },
  chipActive: {
    backgroundColor: colors.primarySoft,
  },
  chipText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  chipTextActive: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontWeight: "600",
  },
  input: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.md,
    backgroundColor: colors.paperMuted,
    paddingHorizontal: spacing.sm,
    paddingVertical: 9,
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 13.5,
  },
  inputNarrow: {
    width: 92,
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.primary,
    borderRadius: radii.pill,
    paddingVertical: 12,
    marginTop: spacing.xs,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  primaryButtonText: {
    color: "#FFFFFF",
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
    fontWeight: "600",
  },
});
