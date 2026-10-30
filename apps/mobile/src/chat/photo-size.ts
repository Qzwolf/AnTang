import { manipulateAsync, SaveFormat } from 'expo-image-manipulator';
import { Image } from 'react-native';

import type { DraftAttachment } from './attachment-flows';

/**
 * 照片发给模型前先缩到长边 1568 像素。
 *
 * 为什么不原样发：手机原图动辄 4000 像素宽、4–8 MB，模型侧会被切成很多图块，
 * token 和流量都白花；1568 是多数多模态模型「一张图看全」的性价比拐点。
 * 只缩不放：本来就小的照片原样上传，放大只会更糊、更贵。
 */
export const MAX_PHOTO_EDGE = 1568;
export const PHOTO_QUALITY = 0.82;

function imageSize(uri: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    Image.getSize(
      uri,
      (width, height) => resolve({ width, height }),
      (error) => reject(error),
    );
  });
}

function jpegName(name: string): string {
  return name.replace(/\.[^./]+$/, '') + '.jpg';
}

/**
 * 返回可以直接上传的附件；拿不到图片尺寸或压缩失败时退回流式处理，
 * 宁可按原图上传，也不要让患者因为「缩图失败」发不出照片。
 */
export async function shrinkPhoto<T extends DraftAttachment>(
  attachment: T,
): Promise<T> {
  if (attachment.kind !== 'photo') return attachment;
  if (!attachment.mimeType.startsWith('image/')) return attachment;

  let size: { width: number; height: number };
  try {
    size = await imageSize(attachment.uri);
  } catch {
    return attachment;
  }
  const longest = Math.max(size.width, size.height);
  if (longest <= MAX_PHOTO_EDGE) return attachment;

  const resize =
    size.width >= size.height
      ? { width: MAX_PHOTO_EDGE }
      : { height: MAX_PHOTO_EDGE };
  try {
    const result = await manipulateAsync(attachment.uri, [{ resize }], {
      compress: PHOTO_QUALITY,
      format: SaveFormat.JPEG,
    });
    return {
      ...attachment,
      mimeType: 'image/jpeg',
      name: jpegName(attachment.name),
      size: undefined,
      uri: result.uri,
    };
  } catch {
    return attachment;
  }
}
