import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { Alert } from 'react-native';

import type { GlucoseReport } from '../api/types';
import { GlucoseReportDetail } from './report-detail';

const REPORT: GlucoseReport = {
  id: 'report-1',
  periodStart: '2025-07-28',
  periodEnd: '2025-07-31',
  device: '硅基动感Pro',
  deviceSerial: 'LT2410FE3P',
  reportGeneratedAt: '2025-07-31',
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
  lbgiLevel: 'high',
  sourceAttachmentId: 'attachment-1',
  note: '天津医科大学总医院',
  createdAt: '2026-10-27T00:12:00+00:00',
};

async function mount(
  report: GlucoseReport = REPORT,
  handlers: {
    onClose?: () => void;
    onDelete?: () => void;
    onOpenOriginal?: () => void;
  } = {},
): Promise<ReactTestRenderer> {
  let renderer: ReactTestRenderer | null = null;
  await act(async () => {
    renderer = create(
      <GlucoseReportDetail
        onClose={handlers.onClose ?? (() => undefined)}
        onDelete={handlers.onDelete ?? (() => undefined)}
        onOpenOriginal={handlers.onOpenOriginal}
        report={report}
      />,
    );
  });
  return renderer as unknown as ReactTestRenderer;
}

describe('报告速览详情', () => {
  it('lists every number the report actually printed', async () => {
    const tree = await mount();
    const body = JSON.stringify(tree.toJSON());

    expect(body).toContain('07-28 至 07-31');
    expect(body).toContain('监测区间 2025-07-28 至 2025-07-31');
    expect(body).toContain('设备 硅基动感Pro · 序列号 LT2410FE3P');
    expect(body).toContain('预估糖化（eHbA1c）');
    expect(body).toContain('5.34%');
    expect(body).toContain('5.81 mmol/L');
    expect(body).toContain('变异系数（CV）');
    expect(body).toContain('正常（3.9–10.0）');
    expect(body).toContain('报告写的档位');
    expect(body).toContain('这是报告按 LBGI 预测出来的档位，不是我们算的。');
  });

  it('hides the metric groups the report did not print', async () => {
    const tree = await mount({
      ...REPORT,
      normalPercent: null,
      lowPercent: null,
      veryLowPercent: null,
      highPercent: null,
      veryHighPercent: null,
      lbgiLevel: '',
      ehba1cPercent: null,
      sdMmol: null,
      cvPercent: null,
      deviceSerial: '',
    });
    const body = JSON.stringify(tree.toJSON());

    expect(body).not.toContain('低血糖风险');
    expect(body).not.toContain('正常（3.9–10.0）');
    expect(body).not.toContain('预估糖化（eHbA1c）');
    // 没印序列号就只写设备名，不留一个空括号。
    expect(body).toContain('设备 硅基动感Pro');
    expect(body).not.toContain('序列号');
  });

  it('asks before deleting and then hands it back', async () => {
    const onDelete = jest.fn();
    const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
    const tree = await mount(REPORT, { onDelete });

    await act(async () => {
      tree.root
        .findByProps({ accessibilityLabel: '删除这份报告速览' })
        .props.onPress();
    });

    expect(alert).toHaveBeenCalledWith(
      '删掉这份报告速览？',
      '删掉之后这条就不再出现在血糖记录里。',
      expect.any(Array),
    );
    expect(onDelete).not.toHaveBeenCalled();

    const buttons = alert.mock.calls[0][2] as { text: string; onPress?: () => void }[];
    await act(async () => {
      buttons.find((button) => button.text === '删掉')?.onPress?.();
    });

    expect(onDelete).toHaveBeenCalledTimes(1);
    alert.mockRestore();
  });

  it('closes from the header', async () => {
    const onClose = jest.fn();
    const tree = await mount(REPORT, { onClose });

    await act(async () => {
      tree.root
        .findByProps({ accessibilityLabel: '关闭报告速览' })
        .props.onPress();
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('opens the original report only when the summary remembers its file', async () => {
    const onOpenOriginal = jest.fn();
    const tree = await mount(REPORT, { onOpenOriginal });

    expect(JSON.stringify(tree.toJSON())).toContain('打开原报告');
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: '打开原报告' }).props.onPress();
    });
    expect(onOpenOriginal).toHaveBeenCalledTimes(1);

    // 没有原文件（例如患者是口述数字、或者附件被清理）就不给这个入口。
    const withoutFile = await mount(
      { ...REPORT, sourceAttachmentId: null },
      { onOpenOriginal: undefined },
    );
    expect(JSON.stringify(withoutFile.toJSON())).not.toContain('打开原报告');
  });
});
