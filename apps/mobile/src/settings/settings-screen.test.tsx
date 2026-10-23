import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import { SettingsScreen } from './settings-screen';
import type { UpdateCheckState } from '../update/use-update-check';

jest.mock('../update/release', () => ({
  installedVersion: jest.fn(() => ({ name: '1.0.0', code: 26102301 })),
}));

function updateState(overrides: Partial<UpdateCheckState> = {}): UpdateCheckState {
  return {
    release: null,
    checking: false,
    error: null,
    lastResult: null,
    autoCheck: true,
    check: jest.fn(async () => undefined),
    dismiss: jest.fn(async () => undefined),
    setAutoCheck: jest.fn(async () => undefined),
    ...overrides,
  };
}

async function renderScreen(update: UpdateCheckState, handlers: Partial<Record<
  'onClose' | 'onLogout' | 'onOpenAccount' | 'onOpenCare',
  () => void
>> = {}): Promise<ReactTestRenderer> {
  let renderer: ReactTestRenderer | null = null;
  await act(async () => {
    renderer = create(
      <SettingsScreen
        onClose={handlers.onClose ?? (() => undefined)}
        onLogout={handlers.onLogout ?? (() => undefined)}
        onOpenAccount={handlers.onOpenAccount ?? (() => undefined)}
        onOpenCare={handlers.onOpenCare ?? (() => undefined)}
        update={update}
        username="akaa"
      />,
    );
  });
  return renderer as unknown as ReactTestRenderer;
}

function renderedText(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

describe('SettingsScreen', () => {
  it('shows the installed version and can check by hand', async () => {
    const update = updateState();
    const renderer = await renderScreen(update);

    expect(renderedText(renderer)).toContain('1.0.0 · 26102301');

    act(() => {
      renderer.root.findByProps({ accessibilityLabel: '检查更新' }).props.onPress();
    });
    expect(update.check).toHaveBeenCalledWith({ manual: true });
  });

  it('says when a new version is waiting', async () => {
    const renderer = await renderScreen(
      updateState({
        release: {
          versionName: '1.0.1',
          versionCode: 26102302,
          apkUrl: 'https://example.test/a.apk',
          sha256: 'a'.repeat(64),
          sizeBytes: 1,
          notes: '',
          builtFromCommit: null,
          features: [],
        },
      }),
    );

    const text = renderedText(renderer);
    expect(text).toContain('有新版本');
    expect(text).toContain('新版本 1.0.1 已就绪');
  });

  it('reports the latest version instead of an empty check', async () => {
    const renderer = await renderScreen(updateState({ lastResult: 'up-to-date' }));

    expect(renderedText(renderer)).toContain('已是最新版本');
  });

  it('wires the rows to the actions the screen is given', async () => {
    const onClose = jest.fn();
    const onLogout = jest.fn();
    const onOpenAccount = jest.fn();
    const onOpenCare = jest.fn();
    const update = updateState();
    const renderer = await renderScreen(update, {
      onClose,
      onLogout,
      onOpenAccount,
      onOpenCare,
    });

    act(() => {
      renderer.root
        .findByProps({ accessibilityLabel: '打开主动关怀设置' })
        .props.onPress();
      renderer.root.findByProps({ accessibilityLabel: '退出登录' }).props.onPress();
      renderer.root.findByProps({ accessibilityLabel: '注销账号' }).props.onPress();
      renderer.root
        .findByProps({ accessibilityLabel: '自动检查更新' })
        .props.onValueChange(false);
      renderer.root.findByProps({ accessibilityLabel: '返回' }).props.onPress();
    });

    expect(onOpenCare).toHaveBeenCalled();
    expect(onLogout).toHaveBeenCalled();
    expect(onOpenAccount).toHaveBeenCalled();
    expect(update.setAutoCheck).toHaveBeenCalledWith(false);
    expect(onClose).toHaveBeenCalled();
  });
});
