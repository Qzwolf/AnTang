import { checkForUpdate, parseRelease } from './release';

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: {
    expoConfig: {
      version: '1.0.0',
      android: { versionCode: 26101901 },
      extra: { buildCommit: 'bc7c320e7600' },
    },
  },
}));

const manifest = {
  version_name: '1.0.1',
  version_code: 26101902,
  apk_url: 'https://82.157.48.200:8000/download/antang-101.apk',
  sha256: 'a'.repeat(64),
  size_bytes: 48926749,
  notes: '新增量表入口',
};

describe('parseRelease', () => {
  it('reads a complete manifest', () => {
    expect(parseRelease(manifest)).toEqual({
      versionName: '1.0.1',
      versionCode: 26101902,
      apkUrl: 'https://82.157.48.200:8000/download/antang-101.apk',
      sha256: 'a'.repeat(64),
      sizeBytes: 48926749,
      notes: '新增量表入口',
      builtFromCommit: null,
      features: [],
    });
  });

  it('keeps the source commit and feature list when the manifest has them', () => {
    const parsed = parseRelease({
      ...manifest,
      built_from_commit: 'c3ea943a1234',
      features: ['voice:off', 'glucose-log:on', 42],
    });
    expect(parsed.builtFromCommit).toBe('c3ea943a1234');
    // 非字符串的项丢掉，不让坏清单把界面搞崩。
    expect(parsed.features).toEqual(['voice:off', 'glucose-log:on']);
  });

  it('rejects a missing field instead of guessing', () => {
    expect(() => parseRelease({ ...manifest, version_code: undefined })).toThrow(
      '版本清单缺少 version_code',
    );
  });

  it('rejects a download address that is not http', () => {
    expect(() => parseRelease({ ...manifest, apk_url: 'file:///sdcard/antang.apk' })).toThrow(
      '下载地址不是 http(s) 地址',
    );
  });

  it('rejects a non-object payload', () => {
    expect(() => parseRelease('nope')).toThrow('版本清单不是对象');
  });
});

describe('checkForUpdate', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('reports an update when the manifest version is newer', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => manifest,
    }) as unknown as typeof fetch;

    const result = await checkForUpdate();

    expect(result.kind).toBe('available');
    if (result.kind === 'available') {
      expect(result.release.versionCode).toBe(26101902);
      expect(result.installed.code).toBe(26101901);
    }
  });

  it('stays quiet when the installed version is the same', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...manifest, version_code: 26101901 }),
    }) as unknown as typeof fetch;

    await expect(checkForUpdate()).resolves.toMatchObject({ kind: 'up-to-date' });
  });

  it('surfaces an http failure instead of pretending there is no update', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({}),
    }) as unknown as typeof fetch;

    await expect(checkForUpdate()).rejects.toThrow('读取版本清单失败（HTTP 404）');
  });
});
