import * as Sharing from 'expo-sharing';

import type { ApiClient } from '../api/client';

/**
 * 打开报告速览的原文件（患者当初发进对话的那份 PDF）。
 *
 * 为什么不是「用系统浏览器打开一个链接」：附件接口要 Bearer token，浏览器里没有；
 * 所以先把文件取到应用缓存，再交给系统的打开方式（分享面板里通常就有 PDF 阅读器、
 * 也能存到文件或转发）。缓存里的副本不会长期留在手机上。
 */
export async function openOriginalReport(
  api: Pick<ApiClient, 'saveChatAttachment'>,
  attachmentId: string,
): Promise<void> {
  const { uri, mimeType } = await api.saveChatAttachment(attachmentId);
  if (!(await Sharing.isAvailableAsync())) {
    throw new Error('这台手机没有可用的打开方式，暂时打不开原报告。');
  }
  await Sharing.shareAsync(uri, {
    dialogTitle: '原报告',
    mimeType,
    UTI: 'com.adobe.pdf',
  });
}
