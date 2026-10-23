import { useEffect } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import * as SecureStore from 'expo-secure-store';

import { checkForUpdate, type ReleaseInfo } from './release';
import { useUpdateCheck, type UpdateCheckState } from './use-update-check';

jest.mock('./release', () => ({
  checkForUpdate: jest.fn(),
  installedVersion: jest.fn(() => ({ name: '1.0.0', code: 26102301 })),
}));

const checkForUpdateMock = checkForUpdate as jest.MockedFunction<typeof checkForUpdate>;

const release: ReleaseInfo = {
  versionName: '1.0.1',
  versionCode: 26102302,
  apkUrl: 'https://example.test/a.apk',
  sha256: 'a'.repeat(64),
  sizeBytes: 49_000_000,
  notes: '修好了两个问题',
  builtFromCommit: 'abc123',
  features: [],
};

function resetStore() {
  const module = SecureStore as unknown as { __resetSecureStore?: () => void };
  module.__resetSecureStore?.();
}

async function settle(milliseconds = 40) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, milliseconds));
  });
}

let latest: UpdateCheckState;

function Probe() {
  const state = useUpdateCheck();
  // 把最新状态交给测试：不在渲染期间改外部变量，放进 effect 里。
  useEffect(() => {
    latest = state;
  });
  return null;
}

async function mount(): Promise<ReactTestRenderer> {
  let renderer: ReactTestRenderer | null = null;
  await act(async () => {
    renderer = create(<Probe />);
  });
  await settle();
  return renderer as unknown as ReactTestRenderer;
}

describe('useUpdateCheck', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    resetStore();
    checkForUpdateMock.mockResolvedValue({
      kind: 'available',
      installed: { name: '1.0.0', code: 26102301 },
      release,
    });
  });

  it('checks once when the app opens and remembers that it already did', async () => {
    const first = await mount();
    expect(checkForUpdateMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      first.unmount();
    });

    await mount();
    expect(checkForUpdateMock).toHaveBeenCalledTimes(1);
  });

  it('lets a manual check ignore the daily limit', async () => {
    await mount();
    await act(async () => {
      await latest.check({ manual: true });
    });

    expect(checkForUpdateMock).toHaveBeenCalledTimes(2);
  });

  it('keeps a dismissed version quiet until a newer one shows up', async () => {
    await mount();
    expect(latest.release?.versionCode).toBe(26102302);

    await act(async () => {
      await latest.dismiss();
    });
    expect(latest.release).toBeNull();

    await act(async () => {
      await latest.check({ manual: true });
    });
    expect(latest.release).toBeNull();

    checkForUpdateMock.mockResolvedValue({
      kind: 'available',
      installed: { name: '1.0.0', code: 26102301 },
      release: { ...release, versionCode: 26102303, versionName: '1.0.2' },
    });
    await act(async () => {
      await latest.check({ manual: true });
    });
    expect(latest.release?.versionCode).toBe(26102303);
  });

  it('reports up-to-date without opening a prompt', async () => {
    checkForUpdateMock.mockResolvedValue({
      kind: 'up-to-date',
      installed: { name: '1.0.1', code: 26102302 },
    });
    await mount();

    expect(latest.release).toBeNull();
    expect(latest.lastResult).toBe('up-to-date');
  });

  it('surfaces a failed check instead of pretending it is current', async () => {
    checkForUpdateMock.mockRejectedValue(new Error('版本清单缺少 version_code'));
    await mount();

    expect(latest.error).toBe('版本清单缺少 version_code');
    expect(latest.lastResult).toBeNull();
  });

  it('does not auto check after the switch is turned off', async () => {
    await mount();
    expect(checkForUpdateMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      await latest.setAutoCheck(false);
    });

    checkForUpdateMock.mockClear();
    await mount();

    // 开关关掉之后，下次启动不该再自动查。
    expect(checkForUpdateMock).toHaveBeenCalledTimes(0);
  });
});
