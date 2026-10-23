import Constants from 'expo-constants';

import { API_URL } from '../config';

/** 版本清单由发布脚本写到下载目录，和 APK 放在一起。 */
export const RELEASE_MANIFEST_URL = `${API_URL}/download/latest.json`;

export type ReleaseInfo = {
  versionName: string;
  versionCode: number;
  apkUrl: string;
  sha256: string;
  sizeBytes: number;
  notes: string;
  /** 这个包是用哪个源码提交打的；旧清单没有这一栏时为 null。 */
  builtFromCommit: string | null;
  /** 这版打开了哪些功能开关，供排查「这包怎么没有某个功能」。 */
  features: string[];
};

export type InstalledVersion = {
  name: string;
  code: number;
};

export type ReleaseCheck =
  | { kind: 'up-to-date'; installed: InstalledVersion }
  | { kind: 'available'; installed: InstalledVersion; release: ReleaseInfo };

/** 已安装版本。开发环境（没有打包过的 expo 配置）没有 versionCode，返回 0。 */
export function installedVersion(): InstalledVersion {
  const config = Constants.expoConfig;
  const code = config?.android?.versionCode ?? 0;
  return { name: config?.version ?? '开发版', code };
}

function readString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`版本清单缺少 ${key}`);
  }
  return value;
}

function readNumber(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`版本清单缺少 ${key}`);
  }
  return value;
}

/** 把清单解析成 ReleaseInfo。字段不对就直接报错，不用默认值糊过去。 */
export function parseRelease(payload: unknown): ReleaseInfo {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('版本清单不是对象');
  }
  const source = payload as Record<string, unknown>;
  const release: ReleaseInfo = {
    versionName: readString(source, 'version_name'),
    versionCode: readNumber(source, 'version_code'),
    apkUrl: readString(source, 'apk_url'),
    sha256: readString(source, 'sha256'),
    sizeBytes: readNumber(source, 'size_bytes'),
    notes: typeof source.notes === 'string' ? source.notes : '',
    builtFromCommit:
      typeof source.built_from_commit === 'string' && source.built_from_commit.length > 0
        ? source.built_from_commit
        : null,
    features: Array.isArray(source.features)
      ? source.features.filter((item): item is string => typeof item === 'string')
      : [],
  };
  if (!/^https?:\/\//.test(release.apkUrl)) {
    throw new Error('版本清单里的下载地址不是 http(s) 地址');
  }
  return release;
}

/** 拉清单并和已安装版本比较。 */
export async function checkForUpdate(signal?: AbortSignal): Promise<ReleaseCheck> {
  const response = await fetch(RELEASE_MANIFEST_URL, {
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new Error(`读取版本清单失败（HTTP ${response.status}）`);
  }
  const release = parseRelease(await response.json());
  const installed = installedVersion();
  if (installed.code <= 0 || release.versionCode <= installed.code) {
    return { kind: 'up-to-date', installed };
  }
  return { kind: 'available', installed, release };
}

/** 「12.3 MB」这种人读的大小。 */
export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
