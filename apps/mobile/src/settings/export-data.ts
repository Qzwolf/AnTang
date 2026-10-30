import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import type { ApiClient } from '../api/client';

export type ExportResult = {
  uri: string;
  fileName: string;
  /** 给用户看的一句话摘要：导出了哪个文件、大概多大。 */
  summary: string;
};

/**
 * 把患者的全部数据导成 JSON 交给系统分享面板。
 *
 * 为什么用分享面板而不是「保存到公共目录」：Android 10+ 的公共目录要走 MediaStore，
 * 各家 ROM 行为不一致；分享面板能让患者自己选存到文件、发给自己、或存进网盘。
 * 文件落在应用缓存目录，患者分享完即走，手机里不留长期副本。
 */
export async function exportAndShare(api: ApiClient): Promise<ExportResult> {
  const { body, contentDisposition } = await api.exportMyData();
  const directory = FileSystem.cacheDirectory;
  if (directory === null) {
    throw new Error('没有可用的缓存目录，导出不了。');
  }
  const fileName = fileNameFrom(contentDisposition);
  const uri = `${directory}${fileName}`;
  await FileSystem.writeAsStringAsync(uri, body, {
    encoding: FileSystem.EncodingType.UTF8,
  });

  const summary = `已生成 ${fileName}，大小 ${sizeLabel(body)}。`;
  if (!(await Sharing.isAvailableAsync())) {
    throw new Error(
      '文件已经生成，但这台手机没有可用的分享面板。可以先把手机连上电脑再导一次。',
    );
  }
  await Sharing.shareAsync(uri, {
    dialogTitle: '导出我的数据',
    mimeType: 'application/json',
    UTI: 'public.json',
  });
  return { uri, fileName, summary };
}

/** 服务器用 content-disposition 给文件名；解析不出来就退回带日期的默认名。 */
function fileNameFrom(contentDisposition: string | null): string {
  const fallback = `antang-export-${new Date().toISOString().slice(0, 10)}.json`;
  if (contentDisposition === null) return fallback;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(contentDisposition);
  if (match === null) return fallback;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return fallback;
  }
}

function sizeLabel(text: string): string {
  // 只给患者一个量级，不追求精确到字节。
  const kilobytes = Math.max(1, Math.round(text.length / 1024));
  return kilobytes >= 1024
    ? `${(kilobytes / 1024).toFixed(1)} MB`
    : `${kilobytes} KB`;
}
