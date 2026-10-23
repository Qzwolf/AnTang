import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { downloadAndInstall } from './install';
import { UpdatePrompt } from './update-prompt';

jest.mock('./install', () => ({
  downloadAndInstall: jest.fn(async () => undefined),
}));

jest.mock('./release', () => ({
  formatSize: (bytes: number) => `${Math.round(bytes / 1_000_000)} MB`,
}));

const downloadMock = downloadAndInstall as jest.MockedFunction<typeof downloadAndInstall>;

const release = {
  versionName: '1.0.1',
  versionCode: 26102302,
  apkUrl: 'https://example.test/a.apk',
  sha256: 'a'.repeat(64),
  sizeBytes: 49_000_000,
  notes: '血糖记录能被对话读到',
  builtFromCommit: null,
  features: [],
};

function renderedText(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

async function mount(onDismiss = () => undefined): Promise<ReactTestRenderer> {
  let renderer: ReactTestRenderer | null = null;
  await act(async () => {
    renderer = create(<UpdatePrompt onDismiss={onDismiss} release={release} />);
  });
  return renderer as unknown as ReactTestRenderer;
}

describe('UpdatePrompt', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    downloadMock.mockResolvedValue(undefined);
  });

  it('shows the version and notes, and installs when asked', async () => {
    const renderer = await mount();
    const text = renderedText(renderer);

    expect(text).toContain('有新版本 1.0.1');
    expect(text).toContain('血糖记录能被对话读到');
    expect(text).toContain('49 MB');

    await act(async () => {
      renderer.root
        .findByProps({ accessibilityLabel: '下载并安装' })
        .props.onPress();
    });
    expect(downloadMock).toHaveBeenCalledWith(release, expect.any(Function));
  });

  it('shows download progress instead of a dead button', async () => {
    let report: ((progress: { percent: number }) => void) | null = null;
    downloadMock.mockImplementation(
      (_release, onProgress) =>
        new Promise<void>((resolve) => {
          report = onProgress as (progress: { percent: number }) => void;
          void resolve;
        }),
    );

    const renderer = await mount();
    await act(async () => {
      renderer.root
        .findByProps({ accessibilityLabel: '下载并安装' })
        .props.onPress();
    });

    await act(async () => {
      report?.({ percent: 46 });
    });

    expect(renderedText(renderer)).toContain('已下载 46%');
  });

  it('keeps the dialog open with the reason when the download fails', async () => {
    downloadMock.mockRejectedValue(new Error('下载的文件不完整，再试一次。'));

    const renderer = await mount();
    await act(async () => {
      renderer.root
        .findByProps({ accessibilityLabel: '下载并安装' })
        .props.onPress();
    });

    const text = renderedText(renderer);
    expect(text).toContain('下载的文件不完整，再试一次。');
    expect(
      renderer.root.findAllByProps({ accessibilityLabel: '下载并安装' }).length,
    ).toBeGreaterThan(0);
  });

  it('can be put off for today', async () => {
    const onDismiss = jest.fn();
    const renderer = await mount(onDismiss);

    act(() => {
      renderer.root
        .findByProps({ accessibilityLabel: '今天先不更新' })
        .props.onPress();
    });
    expect(onDismiss).toHaveBeenCalled();
  });
});
