import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { DeviceChannels } from './device-channels';
import { fakeGadgetbridge, fakeHealthConnect } from './device-controllers.fake';

// 实时心率的会话由 App 外壳的 Provider 提供；这里用变量替身模拟「有没有 Provider」。
const mockLive: { current: Record<string, unknown> | null } = { current: null };

jest.mock('../live-heart-rate/LiveHeartRateProvider', () => ({
  useLiveHeartRateControllerOrNull: () => mockLive.current,
}));

jest.mock('../live-heart-rate/LiveHeartRatePanel', () => ({
  LiveHeartRatePanel: () => null,
}));

function fakeLive(overrides: Record<string, unknown> = {}) {
  return {
    supported: true,
    phase: 'idle',
    busy: false,
    latest: null,
    deviceName: null,
    message: null,
    devices: [],
    samples: [],
    startScan: jest.fn(),
    stopScan: jest.fn(),
    connect: jest.fn(),
    disconnect: jest.fn(),
    ...overrides,
  };
}

async function mount(
  healthConnect = fakeHealthConnect(),
  gadgetbridge = fakeGadgetbridge(),
): Promise<ReactTestRenderer> {
  let tree: ReactTestRenderer | null = null;
  await act(async () => {
    tree = create(
      <DeviceChannels gadgetbridge={gadgetbridge} healthConnect={healthConnect} />,
    );
  });
  return tree as unknown as ReactTestRenderer;
}

function text(tree: ReactTestRenderer): string {
  return JSON.stringify(tree.toJSON());
}

describe('DeviceChannels', () => {
  afterEach(() => {
    mockLive.current = null;
  });

  it('把三条手环通道分开列，并说清各自适合什么手机', async () => {
    const tree = await mount();
    const body = text(tree);
    expect(body).toContain('设备与手环');
    expect(body).toContain('Health Connect');
    expect(body).toContain('系统健康数据');
    expect(body).toContain('导出文件导入');
    expect(body).toContain('华为手环 / 华为运动健康');
    expect(body).toContain('实时心率');
    expect(body).toContain('手环蓝牙广播');
    expect(body).toContain('华为手机大多没有 Health Connect');
  });

  it('未连接时点一下是去连接，已连接时点一下是同步', async () => {
    const disconnected = fakeHealthConnect();
    const first = await mount(disconnected);
    await act(async () => {
      first.root
        .findByProps({ accessibilityLabel: '连接 Health Connect' })
        .props.onPress();
    });
    expect(disconnected.connect).toHaveBeenCalledTimes(1);
    expect(disconnected.sync).not.toHaveBeenCalled();

    const connected = fakeHealthConnect({ connected: true });
    const second = await mount(connected);
    await act(async () => {
      second.root
        .findByProps({ accessibilityLabel: '连接 Health Connect' })
        .props.onPress();
    });
    expect(connected.sync).toHaveBeenCalledTimes(1);
    expect(connected.connect).not.toHaveBeenCalled();
  });

  it('错误只落在出问题的那一行，不把三条通道一起变红', async () => {
    mockLive.current = fakeLive();
    const tree = await mount(
      fakeHealthConnect({ error: '这台手机没有安装 Health Connect。' }),
      fakeGadgetbridge({ connected: true, heartRateCount: 12 }),
    );
    const body = text(tree);
    expect(body).toContain('这台手机没有安装 Health Connect。');
    expect(body).toContain('已导入 12 条心率记录');
    expect(body).toContain('再导入');
    // 实时心率那一行照旧给出「怎么开始」，没有被别人的错误带跑。
    expect(body).toContain('心率广播');
    expect(body).toContain('开始监测');
  });

  it('实时心率：点一下开始搜索，正在测时点一下停止', async () => {
    const idle = fakeLive();
    mockLive.current = idle;
    const first = await mount();
    await act(async () => {
      first.root
        .findByProps({ accessibilityLabel: '实时心率监测' })
        .props.onPress();
    });
    expect(idle.startScan).toHaveBeenCalledTimes(1);

    const streaming = fakeLive({
      phase: 'streaming',
      latest: { beatsPerMinute: 76, measuredAt: 1_000, name: '手环' },
    });
    mockLive.current = streaming;
    const second = await mount();
    expect(text(second)).toContain('76 bpm');
    await act(async () => {
      second.root
        .findByProps({ accessibilityLabel: '实时心率监测' })
        .props.onPress();
    });
    expect(streaming.disconnect).toHaveBeenCalledTimes(1);
    expect(streaming.startScan).not.toHaveBeenCalled();
  });

  it('这个包没编译进实时心率模块时，那一行说清原因且点不动', async () => {
    mockLive.current = fakeLive({ supported: false });
    const tree = await mount();
    const row = tree.root.findByProps({ accessibilityLabel: '实时心率监测' });
    expect(JSON.stringify(tree.toJSON())).toContain('没有编译进实时心率模块');
    expect(row.props.disabled).toBe(true);
    expect(row.props.accessibilityState ?? {}).not.toEqual({ disabled: false });
  });
});
