import { ReadableStream } from 'stream/web';
import { TextDecoder, TextEncoder } from 'util';

Object.defineProperty(globalThis, 'ReadableStream', { value: ReadableStream });
Object.defineProperty(globalThis, 'TextDecoder', { value: TextDecoder });
Object.defineProperty(globalThis, 'TextEncoder', { value: TextEncoder });

// 单元测试不读 .env：给一个显式地址，避免 config.ts 在测试里抛错。
process.env.EXPO_PUBLIC_API_URL ??= 'https://api.antang.test';

// 录音与安装器都是原生模块，测试里只需要形状正确；具体的状态机在 useVoiceInput
// 与 update/install 里，由纯函数和其它测试覆盖。
jest.mock('expo-audio', () => ({
  RecordingPresets: { HIGH_QUALITY: {}, LOW_QUALITY: {} },
  requestRecordingPermissionsAsync: jest.fn(async () => ({ granted: true })),
  setAudioModeAsync: jest.fn(async () => undefined),
  useAudioRecorder: () => ({
    prepareToRecordAsync: jest.fn(async () => undefined),
    record: jest.fn(),
    stop: jest.fn(async () => undefined),
    uri: null,
  }),
  useAudioRecorderState: () => ({
    canRecord: true,
    durationMillis: 0,
    isRecording: false,
    mediaServicesDidReset: false,
    // 默认当作听得到说话（-20 dB），免得每个用例都要显式开麦。
    metering: -20,
    url: null,
  }),
}));

jest.mock('expo-intent-launcher', () => ({
  startActivityAsync: jest.fn(async () => undefined),
}));

// 图片附件下载会把字节写到缓存文件；测试里只记录调用。
jest.mock('expo-file-system/legacy', () => ({
  cacheDirectory: 'file:///cache/',
  EncodingType: { UTF8: 'utf8', Base64: 'base64' },
  readAsStringAsync: jest.fn(async () => ''),
  writeAsStringAsync: jest.fn(async () => undefined),
  deleteAsync: jest.fn(async () => undefined),
}));

jest.mock('expo-sharing', () => ({
  isAvailableAsync: jest.fn(async () => true),
  shareAsync: jest.fn(async () => undefined),
}));

// 安全存储没有原生实现，用一份内存实现替身；测试可以用 __resetSecureStore()
// 在两个用例之间把键值清干净。
jest.mock('expo-secure-store', () => {
  const values = new Map<string, string>();
  return {
    __resetSecureStore: () => values.clear(),
    getItemAsync: jest.fn(async (key: string) => values.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      values.set(key, value);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      values.delete(key);
    }),
  };
});

jest.mock('expo-image-manipulator', () => ({
  SaveFormat: { JPEG: 'jpeg' },
  manipulateAsync: jest.fn(async (uri: string) => ({ uri, width: 1568, height: 1176 })),
}));
