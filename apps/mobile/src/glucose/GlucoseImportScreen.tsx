import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";

import type { ApiClient } from "../api/client";
import { errorMessage } from "../api/client";
import type {
  CgmImportPreview,
  CgmSourceSystem,
  GlucoseReportPreview,
} from "../api/types";

const SOURCES: { value: CgmSourceSystem; label: string }[] = [
  { value: "sibionics", label: "硅基" },
  { value: "nightscout", label: "Nightscout" },
  { value: "libre", label: "雅培" },
  { value: "dexcom", label: "德康" },
  { value: "generic", label: "其他" },
];

type Mode = "csv" | "pdf";

type Props = {
  api: Pick<
    ApiClient,
    | "previewGlucoseImport"
    | "importGlucoseCsv"
    | "previewGlucoseReport"
    | "createGlucoseReport"
  >;
  onBack: () => void;
  onImported: (insertedCount: number) => void;
};

/**
 * 传感器／报告导入：先预览再入库。
 *
 * 两条路分开：
 * - CSV：厂家 App 导出的逐点数据，常有单位混用、重复行、缺时区，先给患者看能导入多少行。
 * - PDF：医院给的「动态葡萄糖评估报告」只有汇总指标，服务端按版式读出数字，
 *   读不到的项目会明说「没读到」，不会补 0。
 */
export function GlucoseImportScreen({ api, onBack, onImported }: Props) {
  const [mode, setMode] = useState<Mode>("csv");
  const [source, setSource] = useState<CgmSourceSystem>("sibionics");
  const [preview, setPreview] = useState<CgmImportPreview | null>(null);
  const [reportPreview, setReportPreview] = useState<GlucoseReportPreview | null>(
    null,
  );
  const [pending, setPending] = useState<{
    mimeType: string;
    name: string;
    uri: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setError(null);
    setPreview(null);
    setReportPreview(null);
  }

  async function pickCsv() {
    reset();
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ["text/csv", "text/comma-separated-values", "text/plain", "*/*"],
        copyToCacheDirectory: true,
      });
      if (result.canceled || result.assets.length === 0) {
        return;
      }
      const asset = result.assets[0];
      const file = {
        mimeType: asset.mimeType ?? "text/csv",
        name: asset.name,
        uri: asset.uri,
      };
      setPending(file);
      setBusy(true);
      setPreview(await api.previewGlucoseImport(file, source));
    } catch (cause) {
      setError(errorMessage(cause));
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  async function pickPdf() {
    reset();
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ["application/pdf", "*/*"],
        copyToCacheDirectory: true,
      });
      if (result.canceled || result.assets.length === 0) {
        return;
      }
      const asset = result.assets[0];
      const file = {
        mimeType: asset.mimeType ?? "application/pdf",
        name: asset.name,
        uri: asset.uri,
      };
      setPending(file);
      setBusy(true);
      setReportPreview(await api.previewGlucoseReport(file));
    } catch (cause) {
      setError(errorMessage(cause));
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmCsv() {
    if (pending === null) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.importGlucoseCsv(pending, source);
      onImported(result.insertedCount);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function confirmPdf() {
    if (pending === null) return;
    setBusy(true);
    setError(null);
    try {
      // 传的是文件本身：服务端重新读一遍再存，原件也留下来（以后还能打开）。
      await api.createGlucoseReport(pending);
      onImported(1);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const isCsv = mode === "csv";

  return (
    <ScrollView contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="返回记录"
          onPress={onBack}
          style={styles.back}
        >
          <Text style={styles.backText}>返回</Text>
        </Pressable>
        <Text style={styles.title}>导入传感器数据</Text>
      </View>

      <View style={styles.modeRow}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="导入 CSV 导出文件"
          accessibilityState={{ selected: isCsv }}
          onPress={() => {
            setMode("csv");
            reset();
          }}
          style={[styles.modeChip, isCsv ? styles.modeChipActive : null]}
        >
          <Text style={[styles.modeText, isCsv ? styles.modeTextActive : null]}>
            CSV 导出文件
          </Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="导入报告 PDF"
          accessibilityState={{ selected: !isCsv }}
          onPress={() => {
            setMode("pdf");
            reset();
          }}
          style={[styles.modeChip, !isCsv ? styles.modeChipActive : null]}
        >
          <Text style={[styles.modeText, !isCsv ? styles.modeTextActive : null]}>
            报告 PDF
          </Text>
        </Pressable>
      </View>

      <Text style={styles.lead}>
        {isCsv
          ? "从厂家 App 导出 CSV，先核对能用的行与可疑行，确认后才存进档案。只支持 CSV，单次不超过 5 MB。"
          : "选医院给的「动态葡萄糖评估报告」PDF。先把读到的数字摆出来给你核对，确认了才存；读不到的项目会照实说，不会补 0。单次不超过 10 MB。"}
      </Text>

      {isCsv ? (
        <>
          <Text style={styles.label}>数据来自</Text>
          <View style={styles.chipRow}>
            {SOURCES.map((option) => {
              const active = option.value === source;
              return (
                <Pressable
                  key={option.value}
                  accessibilityRole="button"
                  accessibilityLabel={`数据来自${option.label}`}
                  accessibilityState={{ selected: active }}
                  onPress={() => setSource(option.value)}
                  style={[styles.chip, active ? styles.chipActive : null]}
                >
                  <Text
                    style={[styles.chipText, active ? styles.chipTextActive : null]}
                  >
                    {option.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={isCsv ? "选择导出的 CSV 文件" : "选择报告 PDF"}
        onPress={() => void (isCsv ? pickCsv() : pickPdf())}
        disabled={busy}
        style={[styles.primary, busy ? styles.disabled : null]}
      >
        <Text style={styles.primaryText}>
          {isCsv
            ? preview === null
              ? "选择文件"
              : "换一个文件"
            : reportPreview === null
              ? "选择 PDF"
              : "换一个 PDF"}
        </Text>
      </Pressable>

      {busy ? <ActivityIndicator style={styles.spinner} /> : null}
      {error === null ? null : <Text style={styles.error}>{error}</Text>}

      {preview === null ? null : (
        <>
          <View style={styles.card}>
            <Text style={styles.cardTitle}>核对一下</Text>
            <Text style={styles.cardBody}>{preview.fileName}</Text>
            <View style={styles.statRow}>
              <View style={styles.stat}>
                <Text style={styles.statLabel}>能导入</Text>
                <Text style={styles.statValue}>{preview.usableRows} 行</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statLabel}>跳过</Text>
                <Text style={styles.statValue}>{preview.problems.length} 行</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statLabel}>单位</Text>
                <Text style={styles.statValue}>
                  {preview.unit === "mmol/L" ? "mmol/L" : "mg/dL"}
                </Text>
              </View>
            </View>
          </View>

          {preview.problems.length === 0 ? null : (
            <View style={styles.warnCard}>
              <Text style={styles.warnTitle}>这几行没导入</Text>
              {preview.problems.map((problem) => (
                <Text key={problem} style={styles.warnLine}>
                  · {problem}
                </Text>
              ))}
            </View>
          )}

          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`确认导入 ${preview.usableRows} 行`}
            onPress={() => void confirmCsv()}
            disabled={busy || preview.usableRows === 0}
            style={[
              styles.primary,
              busy || preview.usableRows === 0 ? styles.disabled : null,
            ]}
          >
            <Text style={styles.primaryText}>
              确认导入 {preview.usableRows} 行
            </Text>
          </Pressable>
          <Text style={styles.hint}>
            同一份文件再导一次不会重复记录。手输的血糖和传感器数值分开存，同一时刻以手输为准。
          </Text>
        </>
      )}

      {reportPreview === null ? null : (
        <>
          <View style={styles.card}>
            <Text style={styles.cardTitle}>读到了这些</Text>
            <Text style={styles.cardBody}>
              {reportPeriodLine(reportPreview)}
            </Text>
            {reportMetaLine(reportPreview) === null ? null : (
              <Text style={styles.cardBody}>{reportMetaLine(reportPreview)}</Text>
            )}
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>报告上的数</Text>
            {reportMetricLines(reportPreview).map((line) => (
              <View key={line.label} style={styles.metricRow}>
                <Text style={styles.metricLabel}>{line.label}</Text>
                <Text style={styles.metricValue}>{line.value}</Text>
              </View>
            ))}
            {reportPreview.missing.length === 0 ? null : (
              <Text style={styles.missingLine}>
                这几项没读到：{reportPreview.missing.join("、")}
              </Text>
            )}
          </View>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="确认存进血糖记录"
            onPress={() => void confirmPdf()}
            disabled={busy || pending === null}
            style={[styles.primary, busy || pending === null ? styles.disabled : null]}
          >
            <Text style={styles.primaryText}>确认存进血糖记录</Text>
          </Pressable>
          <Text style={styles.hint}>
            存的是报告上的指标，不是逐点读数。这份 PDF 会一起留下来：之后在「血糖 ·
            传感器」里点开这份速览，还能打开原始报告看图谱。
          </Text>
        </>
      )}
    </ScrollView>
  );
}

export function reportPeriodLine(preview: GlucoseReportPreview): string {
  return `监测区间 ${shortDate(preview.periodStart)} 至 ${shortDate(preview.periodEnd)}`;
}

export function reportMetaLine(preview: GlucoseReportPreview): string | null {
  const parts = [
    preview.device,
    preview.deviceSerial === "" ? "" : `序列号 ${preview.deviceSerial}`,
    preview.reportGeneratedAt === null
      ? ""
      : `报告生成于 ${shortDate(preview.reportGeneratedAt)}`,
  ].filter((part) => part.length > 0);
  return parts.length === 0 ? null : parts.join(" · ");
}

function metricValue(value: number, unit: string): string {
  return `${value} ${unit}`;
}

/** 只列报告上真有的项；没读到的不写，也不用 0 占位。 */
export function reportMetricLines(
  preview: GlucoseReportPreview,
): { label: string; value: string }[] {
  const lines: { label: string; value: string }[] = [];
  if (preview.ehba1cPercent !== null) {
    lines.push({
      label: "预估糖化 HbA1c",
      value: metricValue(preview.ehba1cPercent, "%"),
    });
  }
  if (preview.meanMmol !== null) {
    lines.push({ label: "平均", value: metricValue(preview.meanMmol, "mmol/L") });
  }
  if (preview.sdMmol !== null) {
    lines.push({ label: "标准差", value: metricValue(preview.sdMmol, "mmol/L") });
  }
  if (preview.cvPercent !== null) {
    lines.push({ label: "变异系数", value: metricValue(preview.cvPercent, "%") });
  }
  const bands: [string, number | null][] = [
    ["正常", preview.normalPercent],
    ["低", preview.lowPercent],
    ["很低", preview.veryLowPercent],
    ["高", preview.highPercent],
    ["很高", preview.veryHighPercent],
  ];
  const printed = bands.filter((entry): entry is [string, number] => entry[1] !== null);
  if (printed.length > 0) {
    lines.push({
      label: "分档",
      value: printed.map(([label, value]) => `${label} ${value}%`).join(" · "),
    });
  }
  return lines;
}

function shortDate(value: string): string {
  // 后端给 2025-07-28 这种日期，只保留月-日，年份在同一份报告里重复四次没意义。
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match === null ? value : `${match[2]}-${match[3]}`;
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 12, paddingBottom: 40 },
  header: { flexDirection: "row", alignItems: "center", gap: 12 },
  back: { paddingVertical: 6, paddingRight: 6 },
  backText: { fontSize: 14, color: "#20B894" },
  title: { fontSize: 20, fontWeight: "700", color: "#17302E" },
  modeRow: {
    flexDirection: "row",
    backgroundColor: "#EDF5F3",
    borderRadius: 999,
    padding: 3,
    gap: 3,
  },
  modeChip: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: 999,
    alignItems: "center",
  },
  modeChipActive: { backgroundColor: "#FFFFFF" },
  modeText: { fontSize: 13, color: "#667875" },
  modeTextActive: { color: "#17302E", fontWeight: "600" },
  lead: { fontSize: 13, lineHeight: 20, color: "#667875" },
  label: { fontSize: 12, color: "#667875", marginTop: 4 },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: "#EDF5F3",
  },
  chipActive: { backgroundColor: "#20B894" },
  chipText: { fontSize: 13, color: "#17302E" },
  chipTextActive: { color: "#FFFFFF", fontWeight: "600" },
  primary: {
    backgroundColor: "#20B894",
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: "center",
  },
  disabled: { opacity: 0.45 },
  primaryText: { color: "#FFFFFF", fontSize: 15, fontWeight: "600" },
  spinner: { marginTop: 4 },
  error: { color: "#DE665B", fontSize: 13 },
  card: {
    backgroundColor: "#FFFFFF",
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: "#E4ECEA",
    padding: 16,
    gap: 6,
  },
  cardTitle: { fontSize: 15, fontWeight: "600", color: "#17302E" },
  cardBody: { fontSize: 12.5, color: "#667875" },
  statRow: { flexDirection: "row", gap: 12, marginTop: 6 },
  stat: { flex: 1, gap: 2 },
  statLabel: { fontSize: 11.5, color: "#91A19E" },
  statValue: { fontSize: 16, fontWeight: "600", color: "#17302E" },
  metricRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 6,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#EDF5F3",
  },
  metricLabel: { fontSize: 13, color: "#475A57" },
  metricValue: { fontSize: 13, fontWeight: "600", color: "#17302E" },
  missingLine: { fontSize: 11.5, lineHeight: 17, color: "#8A5A12", marginTop: 6 },
  warnCard: {
    backgroundColor: "#FBF4E8",
    borderRadius: 16,
    padding: 14,
    gap: 4,
  },
  warnTitle: { fontSize: 13, fontWeight: "600", color: "#8A5A12" },
  warnLine: { fontSize: 12, lineHeight: 18, color: "#8A5A12" },
  hint: { fontSize: 11.5, lineHeight: 17, color: "#91A19E" },
});
