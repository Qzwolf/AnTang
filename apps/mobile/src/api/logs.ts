export type ActivityKind =
  | "walk"
  | "run"
  | "cycle"
  | "strength"
  | "swim"
  | "yoga"
  | "other";

export type ActivityIntensity = "light" | "moderate" | "vigorous";

export const ACTIVITY_KIND_LABELS: Record<ActivityKind, string> = {
  walk: "走路",
  run: "跑步",
  cycle: "骑车",
  strength: "力量",
  swim: "游泳",
  yoga: "瑜伽",
  other: "别的",
};

export const ACTIVITY_INTENSITY_LABELS: Record<ActivityIntensity, string> = {
  light: "轻",
  moderate: "中等",
  vigorous: "较强",
};

const ACTIVITY_KINDS: ActivityKind[] = [
  "walk",
  "run",
  "cycle",
  "strength",
  "swim",
  "yoga",
  "other",
];
const ACTIVITY_INTENSITIES: ActivityIntensity[] = [
  "light",
  "moderate",
  "vigorous",
];

export type ActivityLogEntry = {
  id: string;
  performedAt: string;
  kind: ActivityKind;
  minutes: number;
  intensity: ActivityIntensity;
  note: string | null;
};

export type SleepLogEntry = {
  id: string;
  fellAsleepAt: string;
  wokeAt: string;
  /** 这段睡了多少分钟；由两个时间算出来，不让患者再填一遍。 */
  minutes: number;
  note: string | null;
};

function logField(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  if (typeof value !== "string") throw new Error(`补记数据缺少 ${key}`);
  return value;
}

function logObject(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("补记数据格式不正确");
  }
  return value as Record<string, unknown>;
}

function activityKind(value: unknown): ActivityKind {
  return ACTIVITY_KINDS.includes(value as ActivityKind)
    ? (value as ActivityKind)
    : "other";
}

function activityIntensity(value: unknown): ActivityIntensity {
  return ACTIVITY_INTENSITIES.includes(value as ActivityIntensity)
    ? (value as ActivityIntensity)
    : "moderate";
}

export function parseActivityLogEntry(value: unknown): ActivityLogEntry {
  const data = logObject(value);
  const minutes = data.minutes;
  return {
    id: logField(data, "id"),
    performedAt: logField(data, "performed_at"),
    kind: activityKind(data.kind),
    minutes: typeof minutes === "number" ? minutes : 0,
    intensity: activityIntensity(data.intensity),
    note: typeof data.note === "string" ? data.note : null,
  };
}

export function parseActivityLogs(value: unknown): ActivityLogEntry[] {
  if (!Array.isArray(value)) throw new Error("运动记录格式不正确");
  return value.map(parseActivityLogEntry);
}

export function parseSleepLogEntry(value: unknown): SleepLogEntry {
  const data = logObject(value);
  const fellAsleepAt = logField(data, "fell_asleep_at");
  const wokeAt = logField(data, "woke_at");
  return {
    id: logField(data, "id"),
    fellAsleepAt,
    wokeAt,
    minutes: Math.max(
      0,
      Math.round(
        (new Date(wokeAt).getTime() - new Date(fellAsleepAt).getTime()) / 60_000,
      ),
    ),
    note: typeof data.note === "string" ? data.note : null,
  };
}

export function parseSleepLogs(value: unknown): SleepLogEntry[] {
  if (!Array.isArray(value)) throw new Error("睡眠记录格式不正确");
  return value.map(parseSleepLogEntry);
}
