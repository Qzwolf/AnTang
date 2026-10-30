import {
  ArrowLeft01Icon,
  Camera01Icon,
  CameraRotated01Icon,
  Cancel01Icon,
  Delete02Icon,
  FileChartLineIcon,
  Image01Icon,
  Upload01Icon,
} from '@hugeicons/core-free-icons';
import { CameraView, type CameraType, useCameraPermissions } from 'expo-camera';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';
import type { ChatAttachment } from '../api/types';

export type DraftAttachment = {
  kind: 'photo' | 'report';
  mimeType: string;
  name: string;
  size?: number;
  uri: string;
};

type CameraProps = {
  onClose: () => void;
  onError: (message: string) => void;
  onUsePhoto: (attachment: DraftAttachment) => void;
};

export function CameraScreen({ onClose, onError, onUsePhoto }: CameraProps) {
  const [permission, requestPermission] = useCameraPermissions();
  const [facing, setFacing] = useState<CameraType>('back');
  const [capturedUri, setCapturedUri] = useState<string | null>(null);
  const [takingPhoto, setTakingPhoto] = useState(false);
  const [pickingPhoto, setPickingPhoto] = useState(false);
  const cameraRef = useRef<CameraView>(null);

  async function pickPhoto() {
    if (pickingPhoto) return;
    setPickingPhoto(true);
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: 'images',
        quality: 0.9,
      });
      if (result.canceled) return;
      const photo = result.assets[0];
      onUsePhoto({
        kind: 'photo',
        mimeType: photo.mimeType ?? 'image/jpeg',
        name: photo.fileName ?? `照片-${Date.now()}.jpg`,
        size: photo.fileSize,
        uri: photo.uri,
      });
    } catch {
      onError('没有读取到这张照片，请重新选择。');
    } finally {
      setPickingPhoto(false);
    }
  }

  async function takePhoto() {
    if (cameraRef.current === null || takingPhoto) return;
    setTakingPhoto(true);
    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.86 });
      setCapturedUri(photo.uri);
    } catch {
      onError('没有拍摄成功，请重新试一次。');
    } finally {
      setTakingPhoto(false);
    }
  }

  if (permission === null) {
    return (
      <View style={cameraStyles.centered}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <SafeAreaView style={cameraStyles.permissionScreen}>
        <View style={cameraStyles.permissionIcon}>
          <AppIcon color={colors.primary} icon={Camera01Icon} size={30} />
        </View>
        <Text style={cameraStyles.permissionTitle}>允许使用相机</Text>
        <Text style={cameraStyles.permissionText}>
          拍摄报告或健康相关照片前，需要获得相机权限。照片会先给你确认，不会自动发送。
        </Text>
        <Pressable
          accessibilityLabel="允许相机权限"
          accessibilityRole="button"
          onPress={() => void requestPermission()}
          style={({ pressed }) => [
            cameraStyles.permissionButton,
            pressed && cameraStyles.pressed,
          ]}
        >
          <Text style={cameraStyles.permissionButtonText}>继续</Text>
        </Pressable>
        <Pressable
          accessibilityLabel="从相册选择"
          accessibilityRole="button"
          onPress={() => void pickPhoto()}
          style={cameraStyles.secondaryButton}
        >
          <Text style={cameraStyles.secondaryButtonText}>从相册选择</Text>
        </Pressable>
        <Pressable
          accessibilityLabel="返回聊天"
          accessibilityRole="button"
          onPress={onClose}
          style={cameraStyles.secondaryButton}
        >
          <Text style={cameraStyles.secondaryButtonText}>暂不选择</Text>
        </Pressable>
      </SafeAreaView>
    );
  }

  if (capturedUri !== null) {
    return (
      <View style={cameraStyles.cameraScreen}>
        <Image resizeMode="contain" source={{ uri: capturedUri }} style={StyleSheet.absoluteFill} />
        <SafeAreaView edges={['top']} style={cameraStyles.cameraTopBar}>
          <Pressable
            accessibilityLabel="关闭照片预览"
            accessibilityRole="button"
            onPress={onClose}
            style={cameraStyles.cameraButton}
          >
            <AppIcon color={colors.white} icon={Cancel01Icon} />
          </Pressable>
          <Text style={cameraStyles.cameraTitle}>确认照片</Text>
          <View style={cameraStyles.cameraButtonPlaceholder} />
        </SafeAreaView>
        <SafeAreaView edges={['bottom']} style={cameraStyles.previewActions}>
          <Pressable
            accessibilityLabel="重新拍摄"
            accessibilityRole="button"
            onPress={() => setCapturedUri(null)}
            style={cameraStyles.previewSecondary}
          >
            <Text style={cameraStyles.previewSecondaryText}>重拍</Text>
          </Pressable>
          <Pressable
            accessibilityLabel="使用这张照片"
            accessibilityRole="button"
            onPress={() =>
              onUsePhoto({
                kind: 'photo',
                mimeType: 'image/jpeg',
                name: `照片-${Date.now()}.jpg`,
                uri: capturedUri,
              })
            }
            style={cameraStyles.previewPrimary}
          >
            <Text style={cameraStyles.previewPrimaryText}>使用照片</Text>
          </Pressable>
        </SafeAreaView>
      </View>
    );
  }

  return (
    <View style={cameraStyles.cameraScreen}>
      <CameraView ref={cameraRef} facing={facing} style={StyleSheet.absoluteFill} />
      <SafeAreaView edges={['top']} style={cameraStyles.cameraTopBar}>
        <Pressable
          accessibilityLabel="关闭相机"
          accessibilityRole="button"
          onPress={onClose}
          style={cameraStyles.cameraButton}
        >
          <AppIcon color={colors.white} icon={Cancel01Icon} />
        </Pressable>
        <Text style={cameraStyles.cameraTitle}>拍照</Text>
        <Pressable
          accessibilityLabel="切换前后摄像头"
          accessibilityRole="button"
          onPress={() => setFacing((value) => (value === 'back' ? 'front' : 'back'))}
          style={cameraStyles.cameraButton}
        >
          <AppIcon color={colors.white} icon={CameraRotated01Icon} />
        </Pressable>
      </SafeAreaView>
      <SafeAreaView edges={['bottom']} style={cameraStyles.shutterBar}>
        <Pressable
          accessibilityLabel="从相册选择"
          accessibilityRole="button"
          disabled={pickingPhoto || takingPhoto}
          onPress={() => void pickPhoto()}
          style={({ pressed }) => [
            cameraStyles.galleryButton,
            pressed && cameraStyles.shutterPressed,
          ]}
        >
          {pickingPhoto ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <AppIcon color={colors.white} icon={Image01Icon} size={27} />
          )}
        </Pressable>
        <Pressable
          accessibilityLabel="拍摄照片"
          accessibilityRole="button"
          disabled={takingPhoto}
          onPress={() => void takePhoto()}
          style={({ pressed }) => [
            cameraStyles.shutterOuter,
            pressed && cameraStyles.shutterPressed,
          ]}
        >
          {takingPhoto ? (
            <ActivityIndicator color={colors.ink} />
          ) : (
            <View style={cameraStyles.shutterInner} />
          )}
        </Pressable>
        <View style={cameraStyles.shutterPlaceholder} />
      </SafeAreaView>
    </View>
  );
}

type ReportProps = {
  onClose: () => void;
  onError: (message: string) => void;
  onPicked: (attachment: DraftAttachment) => void;
};

export function ReportPickerScreen({ onClose, onError, onPicked }: ReportProps) {
  const [picking, setPicking] = useState(false);

  async function pickReport() {
    if (picking) return;
    setPicking(true);
    try {
      const result = await DocumentPicker.getDocumentAsync({
        copyToCacheDirectory: true,
        multiple: false,
        type: ['application/pdf', 'image/jpeg', 'image/png'],
      });
      if (result.canceled) return;
      const asset = result.assets[0];
      onPicked({
        kind: 'report',
        mimeType: asset.mimeType ?? 'application/octet-stream',
        name: asset.name,
        size: asset.size,
        uri: asset.uri,
      });
    } catch {
      onError('没有读取到这个文件，请重新选择。');
    } finally {
      setPicking(false);
    }
  }

  return (
    <SafeAreaView style={reportStyles.screen}>
      <View style={reportStyles.header}>
        <Pressable
          accessibilityLabel="返回聊天"
          accessibilityRole="button"
          onPress={onClose}
          style={reportStyles.headerButton}
        >
          <AppIcon icon={ArrowLeft01Icon} />
        </Pressable>
        <Text style={reportStyles.title}>报告解读</Text>
        <View style={reportStyles.headerButton} />
      </View>
      <View style={reportStyles.content}>
        <View style={reportStyles.heroIcon}>
          <AppIcon color={colors.primary} icon={FileChartLineIcon} size={36} />
        </View>
        <Text style={reportStyles.heroTitle}>选择需要解读的报告</Text>
        <Text style={reportStyles.heroText}>
          支持 PDF、JPG 和 PNG。文件会先显示在聊天输入区，由你确认后再发送。
        </Text>
        <View style={reportStyles.uploadCard}>
          <AppIcon color={colors.primary} icon={Upload01Icon} size={30} />
          <View style={reportStyles.uploadCopy}>
            <Text style={reportStyles.uploadTitle}>从手机中选择</Text>
            <Text style={reportStyles.uploadHint}>检验报告、CGM 报告或报告照片</Text>
          </View>
          <Pressable
            accessibilityLabel="选择报告文件"
            accessibilityRole="button"
            disabled={picking}
            onPress={() => void pickReport()}
            style={({ pressed }) => [
              reportStyles.pickButton,
              pressed && reportStyles.pressed,
            ]}
          >
            <Text style={reportStyles.pickButtonText}>
              {picking ? '正在打开…' : '选择文件'}
            </Text>
          </Pressable>
        </View>
        <Text style={reportStyles.safetyText}>
          报告会作为健康信息保存，并交给 AI 读取和解读。请确认文件属于你本人或已获得授权。
        </Text>
      </View>
    </SafeAreaView>
  );
}

export function AttachmentPreview({
  attachment,
  onRemove,
}: {
  attachment: DraftAttachment;
  onRemove: () => void;
}) {
  return (
    <View style={attachmentStyles.card}>
      {attachment.kind === 'photo' ? (
        <Image resizeMode="cover" source={{ uri: attachment.uri }} style={attachmentStyles.image} />
      ) : (
        <View style={attachmentStyles.fileIcon}>
          <AppIcon color={colors.primary} icon={FileChartLineIcon} />
        </View>
      )}
      <View style={attachmentStyles.copy}>
        <Text numberOfLines={1} style={attachmentStyles.name}>
          {attachment.name}
        </Text>
        <Text style={attachmentStyles.status}>已选择 · 等待发送</Text>
      </View>
      <Pressable
        accessibilityLabel="移除附件"
        accessibilityRole="button"
        onPress={onRemove}
        style={attachmentStyles.remove}
      >
        <AppIcon color={colors.muted} icon={Delete02Icon} size={20} />
      </Pressable>
    </View>
  );
}

export function MessageAttachmentCard({
  attachment,
  getImageSource,
}: {
  attachment: ChatAttachment;
  getImageSource: (attachmentId: string) => Promise<{
    uri: string;
  }>;
}) {
  const isImage = attachment.mimeType.startsWith('image/');
  const [remoteImageSource, setRemoteImageSource] = useState<{
    uri: string;
  } | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  useEffect(() => {
    if (!isImage || attachment.localUri) return;
    let active = true;
    void getImageSource(attachment.id).then(
      (source) => {
        if (active) setRemoteImageSource(source);
      },
      () => {
        if (active) setImageFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [attachment.id, attachment.localUri, getImageSource, isImage]);

  if (isImage) {
    const imageSource = attachment.localUri
      ? { uri: attachment.localUri }
      : remoteImageSource;
    return (
      <View style={attachmentStyles.imageBubble}>
        {imageSource !== null && !imageFailed ? (
          <Image
            accessibilityLabel="上传的图片"
            onError={() => setImageFailed(true)}
            resizeMode="cover"
            source={imageSource}
            style={attachmentStyles.messageImage}
          />
        ) : imageFailed ? (
          <Text style={attachmentStyles.imageError}>图片加载失败</Text>
        ) : (
          <ActivityIndicator color={colors.primary} />
        )}
      </View>
    );
  }

  return (
    <View style={attachmentStyles.card}>
      <View style={attachmentStyles.fileIcon}>
        <AppIcon color={colors.primary} icon={FileChartLineIcon} />
      </View>
      <View style={attachmentStyles.copy}>
        <Text numberOfLines={1} style={attachmentStyles.name}>
          {attachment.filename}
        </Text>
        <Text style={attachmentStyles.status}>
          {attachment.mimeType === 'application/pdf' ? 'PDF 报告' : '图片'}
        </Text>
      </View>
    </View>
  );
}

const cameraStyles = StyleSheet.create({
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
  },
  permissionScreen: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.xl,
    backgroundColor: colors.background,
  },
  permissionIcon: {
    width: 64,
    height: 64,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.xl,
    backgroundColor: colors.primarySoft,
  },
  permissionTitle: {
    paddingTop: spacing.lg,
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 24,
  },
  permissionText: {
    maxWidth: 360,
    paddingTop: spacing.sm,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 15,
    lineHeight: 23,
    textAlign: 'center',
  },
  permissionButton: {
    width: '100%',
    maxWidth: 320,
    alignItems: 'center',
    marginTop: spacing.xl,
    paddingVertical: 14,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  permissionButtonText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 16,
  },
  secondaryButton: { padding: spacing.md },
  secondaryButtonText: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  pressed: { opacity: 0.72 },
  cameraScreen: { flex: 1, backgroundColor: '#050807' },
  cameraTopBar: {
    position: 'absolute',
    top: 0,
    right: 0,
    left: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.sm,
    backgroundColor: 'rgba(5, 8, 7, 0.34)',
  },
  cameraButton: {
    width: 46,
    height: 46,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    backgroundColor: 'rgba(17, 34, 31, 0.52)',
  },
  cameraButtonPlaceholder: { width: 46, height: 46 },
  cameraTitle: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
  },
  shutterBar: {
    position: 'absolute',
    right: 0,
    bottom: 0,
    left: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.xl,
    paddingTop: spacing.xl,
    paddingBottom: spacing.lg,
    backgroundColor: 'rgba(5, 8, 7, 0.3)',
  },
  galleryButton: {
    width: 52,
    height: 52,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: 'rgba(17, 34, 31, 0.68)',
  },
  shutterPlaceholder: { width: 52, height: 52 },
  shutterOuter: {
    width: 78,
    height: 78,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 3,
    borderColor: colors.white,
    borderRadius: radii.pill,
  },
  shutterInner: {
    width: 62,
    height: 62,
    borderRadius: radii.pill,
    backgroundColor: colors.white,
  },
  shutterPressed: { transform: [{ scale: 0.94 }] },
  previewActions: {
    position: 'absolute',
    right: 0,
    bottom: 0,
    left: 0,
    flexDirection: 'row',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    paddingBottom: spacing.lg,
    backgroundColor: 'rgba(5, 8, 7, 0.48)',
  },
  previewSecondary: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.55)',
    borderRadius: radii.pill,
  },
  previewSecondaryText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  previewPrimary: {
    flex: 1.5,
    alignItems: 'center',
    paddingVertical: 14,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  previewPrimaryText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
});

const reportStyles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: {
    minHeight: 62,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
    backgroundColor: colors.paper,
  },
  headerButton: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 18,
  },
  content: {
    flex: 1,
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
    paddingTop: 64,
  },
  heroIcon: {
    width: 72,
    height: 72,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.xl,
    backgroundColor: colors.primarySoft,
  },
  heroTitle: {
    paddingTop: spacing.lg,
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 24,
  },
  heroText: {
    maxWidth: 420,
    paddingTop: spacing.sm,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 15,
    lineHeight: 23,
    textAlign: 'center',
  },
  uploadCard: {
    width: '100%',
    maxWidth: 480,
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.xxl,
    padding: spacing.xl,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radii.xl,
    borderCurve: 'continuous',
    backgroundColor: colors.paper,
  },
  uploadCopy: { alignItems: 'center', gap: 3 },
  uploadTitle: {
    color: colors.text,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
  },
  uploadHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  pickButton: {
    alignItems: 'center',
    marginTop: spacing.sm,
    paddingHorizontal: spacing.xl,
    paddingVertical: 12,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  pickButtonText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  pressed: { opacity: 0.72 },
  safetyText: {
    maxWidth: 440,
    paddingTop: spacing.lg,
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
    textAlign: 'center',
  },
});

const attachmentStyles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.xs,
    paddingRight: spacing.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.md,
    backgroundColor: colors.paper,
  },
  image: { width: 48, height: 48, borderRadius: radii.sm },
  imageBubble: {
    width: 238,
    height: 178,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: 18,
    backgroundColor: colors.paperMuted,
    boxShadow: '0 6px 18px rgba(24, 58, 49, 0.12)',
  },
  messageImage: { width: '100%', height: '100%' },
  imageError: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  fileIcon: {
    width: 48,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.sm,
    backgroundColor: colors.primarySoft,
  },
  copy: { flex: 1, gap: 2 },
  name: {
    color: colors.text,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  status: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
  remove: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
  },
});
