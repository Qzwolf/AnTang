import { act, create, type ReactTestRenderer } from "react-test-renderer";

import type { GlucoseReportPreview } from "../api/types";
import { GlucoseImportScreen, reportMetricLines } from "./GlucoseImportScreen";

jest.mock("expo-document-picker", () => ({
  getDocumentAsync: jest.fn(),
}));

const DocumentPicker = jest.requireMock("expo-document-picker") as {
  getDocumentAsync: jest.Mock;
};

const PREVIEW: GlucoseReportPreview = {
  fileName: "report.pdf",
  periodStart: "2025-07-28",
  periodEnd: "2025-07-31",
  reportGeneratedAt: "2025-07-31",
  device: "硅基动感Pro",
  deviceSerial: "LT2410FE3P",
  hospital: "示例医院",
  meanMmol: 5.81,
  sdMmol: 0.9,
  cvPercent: 15.53,
  ehba1cPercent: 5.34,
  normalPercent: 99.6,
  lowPercent: 0.4,
  veryLowPercent: null,
  highPercent: null,
  veryHighPercent: null,
  missing: ["高分档", "很高分档"],
};

function texts(tree: ReactTestRenderer): string {
  return JSON.stringify(tree.toJSON());
}

function find(tree: ReactTestRenderer, label: string) {
  return tree.root.findAll(
    (node) =>
      node.props.accessibilityLabel === label &&
      typeof node.props.onPress === "function",
  )[0];
}

function fakeApi() {
  return {
    previewGlucoseImport: jest.fn(),
    importGlucoseCsv: jest.fn(),
    previewGlucoseReport: jest.fn(async () => PREVIEW),
    createGlucoseReport: jest.fn(async () => ({})),
  };
}

async function mount(api: ReturnType<typeof fakeApi>, onImported = jest.fn()) {
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(
      <GlucoseImportScreen
        api={api as never}
        onBack={jest.fn()}
        onImported={onImported}
      />,
    );
  });
  return { tree: tree as ReactTestRenderer, onImported };
}

beforeEach(() => {
  DocumentPicker.getDocumentAsync.mockReset();
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("GlucoseImportScreen", () => {
  it("shows what the server read from the PDF before saving", async () => {
    DocumentPicker.getDocumentAsync.mockResolvedValue({
      assets: [{ mimeType: "application/pdf", name: "report.pdf", uri: "file:///r.pdf" }],
      canceled: false,
    });
    const api = fakeApi();
    const { tree } = await mount(api);

    await act(async () => {
      find(tree, "导入报告 PDF").props.onPress();
    });
    await act(async () => {
      find(tree, "选择报告 PDF").props.onPress();
    });

    expect(api.previewGlucoseReport).toHaveBeenCalledWith({
      mimeType: "application/pdf",
      name: "report.pdf",
      uri: "file:///r.pdf",
    });
    const body = texts(tree);
    expect(body).toContain("监测区间 07-28 至 07-31");
    expect(body).toContain("硅基动感Pro");
    expect(body).toContain("序列号 LT2410FE3P");
    expect(body).toContain("预估糖化 HbA1c");
    expect(body).toContain("5.34 %");
    // 没读到的项目要照实说，不能显示成 0。
    expect(body).toContain("这几项没读到：");
    expect(body).toContain("高分档、很高分档");
    expect(body).not.toContain("很高 0%");
    // 预览期间一行都没写库。
    expect(api.createGlucoseReport).not.toHaveBeenCalled();
  });

  it("saves the file itself only after the patient confirms", async () => {
    DocumentPicker.getDocumentAsync.mockResolvedValue({
      assets: [{ mimeType: "application/pdf", name: "report.pdf", uri: "file:///r.pdf" }],
      canceled: false,
    });
    const api = fakeApi();
    const { tree, onImported } = await mount(api);

    await act(async () => {
      find(tree, "导入报告 PDF").props.onPress();
    });
    await act(async () => {
      find(tree, "选择报告 PDF").props.onPress();
    });
    await act(async () => {
      find(tree, "确认存进血糖记录").props.onPress();
    });

    // 确认时传的是选中的那份文件：服务端重新读一遍，原件也留下来。
    expect(api.createGlucoseReport).toHaveBeenCalledWith({
      mimeType: "application/pdf",
      name: "report.pdf",
      uri: "file:///r.pdf",
    });
    expect(onImported).toHaveBeenCalledWith(1);
  });

  it("tells the patient why a report could not be read", async () => {
    DocumentPicker.getDocumentAsync.mockResolvedValue({
      assets: [{ mimeType: "application/pdf", name: "scan.pdf", uri: "file:///s.pdf" }],
      canceled: false,
    });
    const api = fakeApi();
    api.previewGlucoseReport.mockRejectedValue(
      new Error("这个 PDF 里没有文字层（大概率是扫描件或截图），读不出数字。"),
    );
    const { tree } = await mount(api);

    await act(async () => {
      find(tree, "导入报告 PDF").props.onPress();
    });
    await act(async () => {
      find(tree, "选择报告 PDF").props.onPress();
    });

    expect(texts(tree)).toContain("扫描件");
  });

  it("lists only the numbers the report actually printed", () => {
    const lines = reportMetricLines({
      ...PREVIEW,
      cvPercent: null,
      highPercent: 1.6,
    });

    const labels = lines.map((line) => line.label);
    expect(labels).not.toContain("变异系数");
    const band = lines.find((line) => line.label === "分档");
    expect(band?.value).toBe("正常 99.6% · 低 0.4% · 高 1.6%");
  });
});
