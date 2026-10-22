import * as FileSystem from 'expo-file-system/legacy';
import * as IntentLauncher from 'expo-intent-launcher';
import { Platform } from 'react-native';

import {
  apkCachePath,
  downloadAndInstall,
  downloadedSizeMatches,
  downloadApk,
  downloadPercent,
  installApk,
} from './install';
import type { ReleaseInfo } from './release';

// jest.mock 会被提升到模块顶部，写在这里不影响顺序。
jest.mock('expo-file-system/legacy', () => ({
  cacheDirectory: 'file:///cache/',
  createDownloadResumable: jest.fn(),
  deleteAsync: jest.fn(async () => undefined),
  getContentUriAsync: jest.fn(async () => 'content://install/1'),
  getInfoAsync: jest.fn(),
}));

jest.mock('expo-intent-launcher', () => ({
  startActivityAsync: jest.fn(async () => undefined),
}));

// jest-expo 的默认平台不是 android，而安装流程只认 android——改这个 getter 就够了，
// 换掉整个 react-native 模块会让 expo-modules-core 找不到宿主，测试结束后还去写日志。
Object.defineProperty(Platform, 'OS', { get: () => 'android' });

const release: ReleaseInfo = {
  versionName: '1.0.3',
  versionCode: 26102003,
  apkUrl: 'https://api.antang.test/download/antang.apk',
  sha256: 'a'.repeat(64),
  sizeBytes: 10,
  notes: '',
  builtFromCommit: null,
  features: [],
};

const downloadMock = FileSystem.createDownloadResumable as jest.Mock;
const infoMock = FileSystem.getInfoAsync as jest.Mock;
const deleteMock = FileSystem.deleteAsync as jest.Mock;
const contentUriMock = FileSystem.getContentUriAsync as jest.Mock;
const startActivityMock = IntentLauncher.startActivityAsync as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('install helpers', () => {
  test('turns byte counts into a clamped percentage', () => {
    expect(downloadPercent(0, 0)).toBe(0);
    expect(downloadPercent(50, 100)).toBe(50);
    expect(downloadPercent(200, 100)).toBe(100);
  });

  test('only accepts a known, exact size', () => {
    expect(downloadedSizeMatches(undefined, 10)).toBe(false);
    expect(downloadedSizeMatches(9, 10)).toBe(false);
    expect(downloadedSizeMatches(10, 10)).toBe(true);
  });

  test('refuses to guess a cache path without a cache directory', () => {
    expect(() => apkCachePath(release, null)).toThrow('没有可用的缓存目录');
    expect(apkCachePath(release, 'file:///cache/')).toBe(
      'file:///cache/antang-26102003.apk',
    );
  });
});

describe('downloadApk', () => {
  test('downloads, reports progress and keeps the file when the size matches', async () => {
    downloadMock.mockReturnValue({
      downloadAsync: jest.fn(async () => {
        const report = downloadMock.mock.calls[0][3] as (p: {
          totalBytesExpectedToWrite: number;
          totalBytesWritten: number;
        }) => void;
        report({ totalBytesExpectedToWrite: 10, totalBytesWritten: 5 });
        return { uri: 'file:///cache/antang-26102003.apk' };
      }),
    });
    infoMock.mockResolvedValue({ exists: true, size: 10 });
    const progress = jest.fn();

    await expect(downloadApk(release, progress)).resolves.toBe(
      'file:///cache/antang-26102003.apk',
    );

    expect(deleteMock).toHaveBeenCalledWith('file:///cache/antang-26102003.apk', {
      idempotent: true,
    });
    expect(progress).toHaveBeenCalledWith({ loaded: 5, percent: 50, total: 10 });
  });

  test('throws and cleans up when the file is shorter than the manifest says', async () => {
    downloadMock.mockReturnValue({
      downloadAsync: jest.fn(async () => ({
        uri: 'file:///cache/antang-26102003.apk',
      })),
    });
    infoMock.mockResolvedValue({ exists: true, size: 4 });

    await expect(downloadApk(release, jest.fn())).rejects.toThrow(
      '下载的文件不完整',
    );
    expect(deleteMock).toHaveBeenCalledTimes(2);
  });
});

describe('installApk', () => {
  test('hands a content uri to the system installer', async () => {
    await installApk('file:///cache/antang-26102003.apk');

    expect(contentUriMock).toHaveBeenCalledWith(
      'file:///cache/antang-26102003.apk',
    );
    expect(startActivityMock).toHaveBeenCalledWith('android.intent.action.VIEW', {
      data: 'content://install/1',
      flags: 1,
      type: 'application/vnd.android.package-archive',
    });
  });

  test('explains how to allow installs when the installer does not open', async () => {
    startActivityMock.mockRejectedValueOnce(new Error('no activity'));

    await expect(installApk('file:///cache/antang-26102003.apk')).rejects.toThrow(
      '安装未知应用',
    );
  });
});

describe('downloadAndInstall', () => {
  test('downloads first and only then opens the installer', async () => {
    downloadMock.mockReturnValue({
      downloadAsync: jest.fn(async () => ({
        uri: 'file:///cache/antang-26102003.apk',
      })),
    });
    infoMock.mockResolvedValue({ exists: true, size: 10 });

    await downloadAndInstall(release, jest.fn());

    expect(startActivityMock).toHaveBeenCalledTimes(1);
  });
});
