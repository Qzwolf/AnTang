import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { Alert } from "react-native";

import type { GlucoseReading, GlucoseSummary, MealLog, MedicationLog } from "../api/types";
import { RecordScreen, formatGlucose, formatMeasuredAt, trendSeries } from "./record-screen";
import { fakeGadgetbridge, fakeHealthConnect } from "./device-controllers.fake";

/**
 * 这些断言本来就是「本机时区里显示成几点」，所以按本地时间构造。
 * 写死 `+08:00` 的固定偏移在 CI（UTC runner）上会整体挪 8 小时，测试就红了。
 */
function atLocal(month: number, day: number, hour: number, minute = 0): string {
  return new Date(2026, month - 1, day, hour, minute).toISOString();
}

const READING: GlucoseReading = {
  id: "00000000-0000-0000-0000-000000000001",
  valueMgDl: 99,
  measuredAt: atLocal(10, 24, 12, 35),
  context: "fasting",
  source: "manual",
  sourceDevice: "",
  measuredPrecision: "exact",
  note: "早上好",
  revokedAt: null,
  createdAt: atLocal(10, 24, 12, 36),
};

const SUMMARY: GlucoseSummary = {
  days: 7,
  count: 1,
  averageMgDl: 99,
  minimumMgDl: 99,
  maximumMgDl: 99,
  targetLowMgDl: 70,
  targetHighMgDl: 180,
  belowTargetCount: 0,
  inTargetCount: 1,
  aboveTargetCount: 0,
};

const MEAL: MealLog = {
  id: "00000000-0000-0000-0000-000000000002",
  eatenAt: atLocal(10, 24, 12, 30),
  mealType: "lunch",
  description: "一碗米饭、青菜、鸡胸",
  carbsGrams: 65,
  createdAt: atLocal(10, 24, 12, 31),
};

const MEDICATION: MedicationLog = {
  id: "00000000-0000-0000-0000-000000000003",
  takenAt: atLocal(10, 24, 7, 50),
  name: "诺和锐",
  doseText: "6 单位",
  kind: "insulin",
  note: "早餐前",
  createdAt: atLocal(10, 24, 7, 51),
};

const DASHBOARD = {
  periodStart: "2026-10-17T00:00:00+08:00",
  periodEnd: "2026-10-24T00:00:00+08:00",
  sources: [
    {
      sourcePackage: "com.huawei.health",
      device: { manufacturer: "HUAWEI", model: "Watch" },
      latestObservedAt: "2026-10-24T12:00:00+08:00",
      daily: [],
      heartRateTrend: [],
      sleepSessions: [],
      totals: {
        heartRateSamples: 0,
        heartRateMinimum: null,
        heartRateMaximum: null,
        heartRateAverage: null,
        steps: 8200,
        distanceMeters: 5600,
        oxygenSaturationSamples: 0,
        oxygenSaturationMinimum: null,
        oxygenSaturationAverage: null,
        restingHeartRateSamples: 0,
        restingHeartRateMinimum: null,
        restingHeartRateMaximum: null,
        restingHeartRateAverage: null,
        sleepSessions: 3,
        sleepMinutes: 370,
      },
    },
  ],
};

function fakeApi(overrides: Record<string, unknown> = {}) {
  return {
    getGlucoseReadings: jest.fn(async () => [READING]),
    getGlucoseSummary: jest.fn(async () => SUMMARY),
    getGlucoseTarget: jest.fn(async () => ({ lowMgDl: 70, highMgDl: 180 })),
    setGlucoseTarget: jest.fn(async (lowMgDl: number, highMgDl: number) => ({
      lowMgDl,
      highMgDl,
    })),
    getMealLogs: jest.fn(async () => [MEAL]),
    createMealLog: jest.fn(async () => MEAL),
    deleteMealLog: jest.fn(async () => undefined),
    getMedicationLogs: jest.fn(async () => [MEDICATION]),
    createMedicationLog: jest.fn(async () => MEDICATION),
    deleteMedicationLog: jest.fn(async () => undefined),
    getWearableDashboard: jest.fn(async () => DASHBOARD),
    getCgmSamples: jest.fn(async () => ({ days: 7, count: 0, samples: [] })),
    getGlucoseReports: jest.fn(async () => []),
    deleteGlucoseReport: jest.fn(async () => undefined),
    saveChatAttachment: jest.fn(async () => ({
      uri: "file:///cache/chat-attachment-attachment-2.pdf",
      mimeType: "application/pdf",
    })),
    ...overrides,
  };
}

async function mount(
  api = fakeApi(),
  handlers: { onImport?: () => void; onRecordGlucose?: () => void } = {},
): Promise<ReactTestRenderer> {
  let renderer: ReactTestRenderer | null = null;
  await act(async () => {
    renderer = create(
      <RecordScreen
        api={api as never}
        gadgetbridge={fakeGadgetbridge()}
        healthConnect={fakeHealthConnect()}
        onImport={handlers.onImport ?? (() => undefined)}
        onRecordGlucose={handlers.onRecordGlucose ?? (() => undefined)}
      />,
    );
  });
  return renderer as unknown as ReactTestRenderer;
}

describe("RecordScreen", () => {
  it("shows a manual reading with its context and note", async () => {
    const tree = await mount();
    const text = JSON.stringify(tree.toJSON());
    expect(text).toContain("5.5 mmol/L");
    expect(text).toContain("空腹");
    expect(text).toContain("早上好");
    expect(text).toContain("低于 3.9 共");
  });

  it("switches to the sensor tab and offers an import action", async () => {
    let imported = 0;
    const tree = await mount(fakeApi(), { onImport: () => (imported += 1) });

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "传感器来源" }).props.onPress();
    });
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "导入传感器数据" }).props.onPress();
    });
    expect(imported).toBe(1);
  });

  it("shows a reading that came in from a report under the sensor tab", async () => {
    // 报告里读出来的数字不应该凭空消失：对话说「已经记进血糖记录」，
    // 患者翻到这一页必须看得见，否则又变成「说了没记」。
    const api = fakeApi({
      getGlucoseReadings: jest.fn(async () => [
        READING,
        {
          ...READING,
          id: "report-1",
          valueMgDl: 74,
          source: "cgm",
          sourceDevice: "",
          note: "报告均值",
        },
      ]),
    });
    const tree = await mount(api);

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "传感器来源" }).props.onPress();
    });

    const text = JSON.stringify(tree.toJSON());
    expect(text).toContain("报告里记下 1 条");
    expect(text).toContain("4.1 mmol/L");
    expect(text).toContain("传感器");
    expect(text).toContain("报告均值");
  });

  it("shows a saved report summary under the sensor tab", async () => {
    // CGM 报告多数只有汇总指标、没有逐点读数，存成「速览」；这里要能看见。
    const api = fakeApi({
      getGlucoseReports: jest.fn(async () => [
        {
          id: "report-summary-1",
          periodStart: "2024-05-30",
          periodEnd: "2024-06-11",
          device: "硅基动感Pro",
          deviceSerial: "LT2410FE3P",
          reportGeneratedAt: "2024-06-11",
          meanMmol: 6.3,
          sdMmol: 1.46,
          cvPercent: 23.27,
          ehba1cPercent: 5.73,
          tirPercent: 84.5,
          tarPercent: 15.3,
          tbrPercent: 0.2,
          normalPercent: 84.5,
          lowPercent: 0.2,
          veryLowPercent: 0,
          highPercent: 14.6,
          veryHighPercent: 0.7,
          lbgiLevel: "high",
          note: "天津医科大学总医院",
          createdAt: "2026-10-26T14:41:00+00:00",
        },
      ]),
    });
    const tree = await mount(api);

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "传感器来源" }).props.onPress();
    });

    const text = JSON.stringify(tree.toJSON());
    expect(text).toContain("05-30 至 06-11 · 报告速览");
    expect(text).toContain("硅基动感Pro");
    expect(text).toContain("目标内 84.5%");
    expect(text).toContain("预估糖化 5.73%");
    // 报告上写着的档位要照原话说，别自己换算成「风险等级 1」。
    expect(text).toContain("低血糖风险高");
    expect(text).toContain("分档 正常 84.5% · 低 0.2%");
    expect(text).toContain("天津医科大学总医院");
  });

  it("opens a saved report's full detail and can delete it", async () => {
    const api = fakeApi({
      getGlucoseReports: jest.fn(async () => [
        {
          id: "report-summary-2",
          periodStart: "2025-07-28",
          periodEnd: "2025-07-31",
          device: "硅基动感Pro",
          deviceSerial: "LT2410FE3P",
          reportGeneratedAt: "2025-07-31",
          meanMmol: 5.81,
          sdMmol: 0.9,
          cvPercent: 15.53,
          ehba1cPercent: 5.34,
          tirPercent: 99.6,
          tarPercent: 1.6,
          tbrPercent: 0.4,
          normalPercent: 99.6,
          lowPercent: 0.4,
          veryLowPercent: 0,
          highPercent: 1.6,
          veryHighPercent: 0,
          lbgiLevel: "high",
          sourceAttachmentId: "attachment-2",
          note: "天津医科大学总医院",
          createdAt: "2026-10-27T00:12:00+00:00",
        },
      ]),
    });
    const tree = await mount(api);

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "传感器来源" }).props.onPress();
    });
    // 卡片上方那句提示；点开之后才看得到序列号与分组标题。
    expect(JSON.stringify(tree.toJSON())).toContain("看全部指标");
    expect(JSON.stringify(tree.toJSON())).not.toContain("序列号");

    await act(async () => {
      tree.root
        .findByProps({ accessibilityLabel: "报告速览 07-28 至 07-31" })
        .props.onPress();
    });
    const detail = JSON.stringify(tree.toJSON());
    expect(detail).toContain("设备 硅基动感Pro · 序列号 LT2410FE3P");
    expect(detail).toContain("整体水平");
    expect(detail).toContain("时间花在哪");
    expect(detail).toContain("报告写的档位");

    // 原文件取回来是为了交给系统的打开方式（分享面板里就有 PDF 阅读器）。
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "打开原报告" }).props.onPress();
    });
    expect(api.saveChatAttachment).toHaveBeenCalledWith("attachment-2");

    const alert = jest
      .spyOn(Alert, "alert")
      .mockImplementation(() => undefined);
    await act(async () => {
      tree.root
        .findByProps({ accessibilityLabel: "删除这份报告速览" })
        .props.onPress();
    });
    const buttons = alert.mock.calls.at(-1)?.[2] as
      | { text: string; onPress?: () => void }[]
      | undefined;
    await act(async () => {
      buttons?.find((button) => button.text === "删掉")?.onPress?.();
    });

    expect(api.deleteGlucoseReport).toHaveBeenCalledWith("report-summary-2");
    expect(JSON.stringify(tree.toJSON())).not.toContain("07-28 至 07-31 · 报告速览");
    alert.mockRestore();
  });

  it("lists meals and saves a new one without leaving the tab", async () => {    const api = fakeApi();
    const tree = await mount(api);

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "饮食分类" }).props.onPress();
    });
    expect(JSON.stringify(tree.toJSON())).toContain("一碗米饭、青菜、鸡胸");
    // 吃的时间与记录时间分开写：12:30 吃的、12:31 记的。
    expect(JSON.stringify(tree.toJSON())).toContain("记录于 10-24 12:31");

    await act(async () => {
      tree.root
        .findByProps({ accessibilityLabel: "这一餐吃了什么" })
        .props.onChangeText("两个包子");
    });
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "份量一碗" }).props.onPress();
    });
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "保存这餐" }).props.onPress();
    });

    expect(api.createMealLog).toHaveBeenCalledWith(
      expect.objectContaining({ mealType: "lunch", description: "两个包子", carbsGrams: 45 }),
    );
  });

  it("lists medication and saves one with the text the patient typed", async () => {
    const api = fakeApi();
    const tree = await mount(api);

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "用药分类" }).props.onPress();
    });
    expect(JSON.stringify(tree.toJSON())).toContain("诺和锐");
    expect(JSON.stringify(tree.toJSON())).toContain("记录于 10-24 07:51");

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "药名" }).props.onChangeText("二甲双胍");
    });
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "剂量" }).props.onChangeText("一片");
    });
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "选择口服药" }).props.onPress();
    });
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "保存这次用药" }).props.onPress();
    });

    expect(api.createMedicationLog).toHaveBeenCalledWith(
      expect.objectContaining({ name: "二甲双胍", doseText: "一片", kind: "oral" }),
    );
  });

  it("fills exercise and sleep from the wearable import, not from typing", async () => {
    const tree = await mount();

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "运动分类" }).props.onPress();
    });
    expect(JSON.stringify(tree.toJSON())).toContain("8,200");

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "睡眠分类" }).props.onPress();
    });
    expect(JSON.stringify(tree.toJSON())).toContain("约 6 小时");
  });

  it("puts manual readings and report points on one trend line", async () => {
    // 时间按「现在」往前推：写死日期会在几天后掉出 7 天窗口，变成定时炸弹。
    const hoursAgo = (hours: number) =>
      new Date(Date.now() - hours * 3600 * 1000).toISOString();
    const api = fakeApi({
      getGlucoseReadings: jest.fn(async () => [
        { ...READING, id: "m-1", measuredAt: hoursAgo(5), valueMgDl: 126 },
        { ...READING, id: "r-1", measuredAt: hoursAgo(2), source: "report", valueMgDl: 74 },
      ]),
    });
    const tree = await mount(api);

    const body = JSON.stringify(tree.toJSON());
    // 标题是「近 {天数} 天 · 血糖趋势」，模板字符串会被拆成多个子节点，断言看后半段。
    expect(body).toContain("· 血糖趋势");
    expect(body).toContain("自己量的 1 次");
    expect(body).toContain("报告里 1 条");
  });

  it("draws no trend from a single point", async () => {
    const api = fakeApi({
      getGlucoseReadings: jest.fn(async () => [
        { ...READING, measuredAt: new Date().toISOString() },
      ]),
    });
    const tree = await mount(api);

    expect(JSON.stringify(tree.toJSON())).not.toContain("血糖趋势");
  });

  it("says in the list and on the chart when a time is only a daypart", async () => {
    // 只有某个时段的读数：后端存的是该时段的代表时刻（早上 07:00），
    // 界面必须写「早上（大概）」，不能让 07:00 冒充精确时间。
    const rough = new Date();
    rough.setDate(rough.getDate() - 1);
    rough.setHours(7, 0, 0, 0);
    const api = fakeApi({
      getGlucoseReadings: jest.fn(async () => [
        {
          ...READING,
          id: "rough",
          measuredAt: rough.toISOString(),
          measuredPrecision: "daypart" as const,
        },
        { ...READING, id: "exact", measuredAt: new Date().toISOString() },
      ]),
    });
    const tree = await mount(api);
    const body = JSON.stringify(tree.toJSON());

    expect(body).toContain("（大概）");
    expect(body).toContain("横轴位置按该时段起点画");
  });

  it("reads the low line from the patient's own target range", async () => {
    // 医生把目标放宽到 4.4–8.9：界面不能再写 3.9，也不能按 70 数「低于」。
    const api = fakeApi({
      getGlucoseTarget: jest.fn(async () => ({ lowMgDl: 80, highMgDl: 160 })),
      getGlucoseSummary: jest.fn(async () => ({
        ...SUMMARY,
        targetLowMgDl: 80,
        targetHighMgDl: 160,
        belowTargetCount: 2,
      })),
    });
    const tree = await mount(api);
    const body = JSON.stringify(tree.toJSON());

    expect(body).toContain("目标范围 4.4–8.9 mmol/L");
    expect(body).toContain("低于 4.4 共 2 次");
  });

  it("saves the range the patient typed into the target sheet", async () => {
    const api = fakeApi();
    const tree = await mount(api);

    await act(async () => {
      tree.root
        .findByProps({ accessibilityLabel: "修改血糖目标范围" })
        .props.onPress();
    });
    // 上限从 10.0 往上调两格（一格 0.1）。
    for (let step = 0; step < 2; step += 1) {
      await act(async () => {
        tree.root
          .findByProps({ accessibilityLabel: "上限加 0.1" })
          .props.onPress();
      });
    }
    await act(async () => {
      tree.root
        .findByProps({ accessibilityLabel: "保存目标范围" })
        .props.onPress();
    });

    expect(api.setGlucoseTarget).toHaveBeenCalledWith(70, 184);
    expect(JSON.stringify(tree.toJSON())).toContain("目标范围 3.9–10.2 mmol/L");
  });
});

describe("glucose trend series", () => {
  it("keeps the last week, oldest first, and marks report points", () => {
    const now = Date.parse("2026-10-29T12:00:00+08:00");
    const series = trendSeries(
      [
        { ...READING, id: "new", measuredAt: "2026-10-29T09:00:00+08:00" },
        { ...READING, id: "old", measuredAt: "2026-10-01T09:00:00+08:00" },
        {
          ...READING,
          id: "report",
          measuredAt: "2026-10-28T09:00:00+08:00",
          measuredPrecision: "daypart" as const,
          source: "report",
        },
        {
          ...READING,
          id: "imported",
          measuredAt: "2026-10-29T06:00:00+08:00",
          source: "cgm",
        },
        {
          ...READING,
          id: "revoked",
          measuredAt: "2026-10-27T09:00:00+08:00",
          revokedAt: "2026-10-27T10:00:00+08:00",
        },
      ],
      now,
    );

    // 报告和导入的读数都算「报告里记下的」，和「手动 / 传感器」两个子栏的划分一致。
    expect(series.map((point) => point.reported)).toEqual([true, true, false]);
    expect(series[0].value).toBeCloseTo(5.5, 1);
    expect(series.map((point) => point.approximate)).toEqual([
      true,
      false,
      false,
    ]);
  });
});

describe("record formatting", () => {
  it("shows every reading in mmol/L, the unit patients here actually use", () => {
    expect(formatGlucose(READING)).toBe("5.5 mmol/L");
    expect(formatGlucose({ ...READING, valueMgDl: 86 })).toBe("4.8 mmol/L");
  });

  it("formats the measurement time as month-day hour:minute", () => {
    const text = formatMeasuredAt(atLocal(10, 24, 12, 35));
    expect(text).toBe("10-24 12:35");
  });
});
