import * as ExpoNotifications from 'expo-notifications';
import * as SecureStore from 'expo-secure-store';
import { AppState, Text } from 'react-native';
import { act, create } from 'react-test-renderer';

import type { ApiClient } from '../api/client';
import type { ChatMessage } from '../api/types';
import { useNotifications } from './use-notifications';

jest.mock('expo-notifications', () => ({
  AndroidImportance: { DEFAULT: 3, HIGH: 4 },
  PermissionStatus: {
    UNDETERMINED: 'undetermined',
    DENIED: 'denied',
    GRANTED: 'granted',
  },
  setNotificationHandler: jest.fn(),
  setNotificationChannelAsync: jest.fn(),
  getPermissionsAsync: jest.fn(),
  requestPermissionsAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn(),
  addPushTokenListener: jest.fn(),
  addNotificationReceivedListener: jest.fn(),
  addNotificationResponseReceivedListener: jest.fn(),
  getLastNotificationResponse: jest.fn(),
  clearLastNotificationResponse: jest.fn(),
}));
jest.mock('expo-constants', () => ({
  __esModule: true,
  default: {
    expoConfig: {
      version: '1.0.0',
      extra: { eas: { projectId: 'd04128ba-7ddd-4df4-a952-d52a8a9bea68' } },
    },
  },
}));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
}));
jest.mock('expo-crypto', () => ({
  randomUUID: () => '019b8888-8888-7888-8888-888888888888',
}));

const mockGetPermissionsAsync = jest.mocked(
  ExpoNotifications.getPermissionsAsync,
);
const mockRequestPermissionsAsync = jest.mocked(
  ExpoNotifications.requestPermissionsAsync,
);
const mockGetExpoPushTokenAsync = jest.mocked(
  ExpoNotifications.getExpoPushTokenAsync,
);
const mockAddPushTokenListener = jest.mocked(
  ExpoNotifications.addPushTokenListener,
);
const mockAddNotificationReceivedListener = jest.mocked(
  ExpoNotifications.addNotificationReceivedListener,
);
const mockAddNotificationResponseReceivedListener = jest.mocked(
  ExpoNotifications.addNotificationResponseReceivedListener,
);
const mockGetLastNotificationResponse = jest.mocked(
  ExpoNotifications.getLastNotificationResponse,
);
const mockClearLastNotificationResponse = jest.mocked(
  ExpoNotifications.clearLastNotificationResponse,
);
const mockGetItemAsync = jest.mocked(SecureStore.getItemAsync);

const CONVERSATION_ID = '019b4444-4444-7444-8444-444444444444';

const proactiveMessage: ChatMessage = {
  id: '019b5555-5555-7555-8555-555555555555',
  clientMessageId: null,
  role: 'assistant',
  status: 'completed',
  content: '今天感觉怎么样？',
  sources: [],
  attachments: [],
  createdAt: '2026-07-18T10:00:00Z',
  completedAt: '2026-07-18T10:00:01Z',
};

const notification = {
  request: {
    content: {
      data: {
        kind: 'routine_check_in',
        message_id: proactiveMessage.id,
        delivery_id: '019b6666-6666-7666-8666-666666666666',
      },
    },
  },
};

describe('useNotifications', () => {
  let receivedListener:
    | ((value: ExpoNotifications.Notification) => void)
    | undefined;
  let appStateListener: ((state: string) => void) | undefined;
  const subscription = { remove: jest.fn() };

  beforeEach(() => {
    receivedListener = undefined;
    appStateListener = undefined;
    subscription.remove.mockReset();
    mockGetPermissionsAsync.mockReset().mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.UNDETERMINED,
    } as never);
    mockRequestPermissionsAsync.mockReset();
    mockGetExpoPushTokenAsync.mockReset();
    mockGetItemAsync
      .mockReset()
      .mockResolvedValue('019b8888-8888-7888-8888-888888888888');
    mockAddPushTokenListener.mockReset().mockReturnValue(subscription);
    mockAddNotificationReceivedListener.mockReset().mockImplementation((listener) => {
      receivedListener = listener;
      return subscription;
    });
    mockAddNotificationResponseReceivedListener
      .mockReset()
      .mockReturnValue(subscription);
    mockGetLastNotificationResponse.mockReset().mockReturnValue(null);
    mockClearLastNotificationResponse.mockReset();
    jest.spyOn(AppState, 'addEventListener').mockImplementation((_, listener) => {
      appStateListener = listener as typeof appStateListener;
      return subscription;
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test('waits for an active Core stream to finish before merging a foreground notification', async () => {
    const api = {
      syncPushInstallation: jest.fn().mockResolvedValue(undefined),
      getMessageWindow: jest.fn().mockResolvedValue({
        messages: [proactiveMessage],
        nextBefore: null,
      }),
      getMessages: jest.fn(),
      markPushDeliveryOpened: jest.fn(),
    } as unknown as ApiClient;
    const onMessages = jest.fn();
    const onError = jest.fn();
    function Probe({ streamActive }: { streamActive: boolean }) {
      useNotifications({
        api,
        userId: '019b7777-7777-7777-8777-777777777777',
        conversationId: CONVERSATION_ID,
        historyReady: true,
        streamActive,
        onMessages,
        onError,
      });
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe streamActive />);
    });
    act(() =>
      receivedListener?.(notification as unknown as ExpoNotifications.Notification),
    );
    expect(api.getMessageWindow).not.toHaveBeenCalled();

    await act(async () => {
      tree!.update(<Probe streamActive={false} />);
    });

    expect(api.getMessageWindow).toHaveBeenCalledWith(proactiveMessage.id);
    expect(onMessages).toHaveBeenCalledWith([proactiveMessage], null);
    expect(api.markPushDeliveryOpened).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    act(() => tree!.unmount());
  });

  test('exposes an explicit permission action without prompting during mount', async () => {
    mockRequestPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.GRANTED,
    } as never);
    mockGetExpoPushTokenAsync.mockResolvedValue({
      type: 'expo',
      data: 'ExponentPushToken[settings]',
    });
    const api = {
      syncPushInstallation: jest.fn().mockResolvedValue(undefined),
      getMessageWindow: jest.fn(),
      getMessages: jest.fn(),
      markPushDeliveryOpened: jest.fn(),
    } as unknown as ApiClient;
    let requestPermission: (() => Promise<unknown>) | undefined;

    function Probe() {
      requestPermission = useNotifications({
        api,
        userId: '019b7777-7777-7777-8777-777777777777',
        conversationId: CONVERSATION_ID,
        historyReady: true,
        streamActive: false,
        onMessages: jest.fn(),
        onError: jest.fn(),
      }).requestPermission;
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe />);
    });
    expect(mockRequestPermissionsAsync).not.toHaveBeenCalled();

    await act(async () => {
      await requestPermission?.();
    });
    expect(mockRequestPermissionsAsync).toHaveBeenCalledTimes(1);
    expect(api.syncPushInstallation).toHaveBeenLastCalledWith(
      '019b8888-8888-7888-8888-888888888888',
      'ExponentPushToken[settings]',
      'granted',
      '1.0.0',
    );
    act(() => tree!.unmount());
  });

  test('does not apply a notification window if streaming starts while it is loading', async () => {
    let resolveWindow!: (value: {
      messages: ChatMessage[];
      nextBefore: null;
    }) => void;
    const firstWindow = new Promise<{
      messages: ChatMessage[];
      nextBefore: null;
    }>((resolve) => {
      resolveWindow = resolve;
    });
    const api = {
      syncPushInstallation: jest.fn().mockResolvedValue(undefined),
      getMessageWindow: jest
        .fn()
        .mockReturnValueOnce(firstWindow)
        .mockResolvedValue({ messages: [proactiveMessage], nextBefore: null }),
      getMessages: jest.fn(),
      markPushDeliveryOpened: jest.fn(),
    } as unknown as ApiClient;
    const onMessages = jest.fn();
    function Probe({ streamActive }: { streamActive: boolean }) {
      useNotifications({
        api,
        userId: '019b7777-7777-7777-8777-777777777777',
        conversationId: CONVERSATION_ID,
        historyReady: true,
        streamActive,
        onMessages,
        onError: jest.fn(),
      });
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe streamActive={false} />);
    });
    await act(async () => {
      receivedListener?.(
        notification as unknown as ExpoNotifications.Notification,
      );
      await Promise.resolve();
    });
    await act(async () => {
      tree!.update(<Probe streamActive />);
    });
    await act(async () => {
      resolveWindow({ messages: [proactiveMessage], nextBefore: null });
      await firstWindow;
    });
    expect(onMessages).not.toHaveBeenCalled();

    await act(async () => {
      tree!.update(<Probe streamActive={false} />);
    });
    expect(api.getMessageWindow).toHaveBeenCalledTimes(2);
    expect(onMessages).toHaveBeenCalledWith([proactiveMessage], null);
    act(() => tree!.unmount());
  });

  test('waits for initial history before handling a cold-start notification tap', async () => {
    mockGetLastNotificationResponse.mockReturnValue({ notification } as never);
    const api = {
      syncPushInstallation: jest.fn().mockResolvedValue(undefined),
      getMessageWindow: jest.fn().mockResolvedValue({
        messages: [proactiveMessage],
        nextBefore: null,
      }),
      getMessages: jest.fn(),
      markPushDeliveryOpened: jest.fn().mockResolvedValue(undefined),
    } as unknown as ApiClient;
    const onMessages = jest.fn();
    const onError = jest.fn();
    function Probe({ historyReady }: { historyReady: boolean }) {
      useNotifications({
        api,
        userId: '019b7777-7777-7777-8777-777777777777',
        conversationId: CONVERSATION_ID,
        historyReady,
        streamActive: false,
        onMessages,
        onError,
      });
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe historyReady={false} />);
    });
    expect(api.getMessageWindow).not.toHaveBeenCalled();

    await act(async () => {
      tree!.update(<Probe historyReady />);
    });

    expect(onMessages).toHaveBeenCalledWith(
      [proactiveMessage],
      proactiveMessage.id,
    );
    expect(api.markPushDeliveryOpened).toHaveBeenCalledWith(
      '019b6666-6666-7666-8666-666666666666',
    );
    expect(mockClearLastNotificationResponse).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    act(() => tree!.unmount());
  });

  test('refreshes latest messages after returning to the foreground and waits for streaming', async () => {
    const api = {
      syncPushInstallation: jest.fn().mockResolvedValue(undefined),
      getMessageWindow: jest.fn(),
      getMessages: jest.fn().mockResolvedValue({
        messages: [proactiveMessage],
        nextBefore: null,
      }),
      markPushDeliveryOpened: jest.fn(),
    } as unknown as ApiClient;
    const onMessages = jest.fn();
    function Probe({ streamActive }: { streamActive: boolean }) {
      useNotifications({
        api,
        userId: '019b7777-7777-7777-8777-777777777777',
        conversationId: CONVERSATION_ID,
        historyReady: true,
        streamActive,
        onMessages,
        onError: jest.fn(),
      });
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe streamActive />);
    });
    act(() => appStateListener?.('active'));
    expect(api.getMessages).not.toHaveBeenCalled();

    await act(async () => {
      tree!.update(<Probe streamActive={false} />);
    });

    expect(api.getMessages).toHaveBeenCalledWith(null, CONVERSATION_ID);
    expect(onMessages).toHaveBeenCalledWith([proactiveMessage], null);
    act(() => tree!.unmount());
  });

  test('recovers when notification registration never settles', async () => {
    let resolveToken!: (value: { type: 'expo'; data: string }) => void;
    const tokenRequest = new Promise<{ type: 'expo'; data: string }>((resolve) => {
      resolveToken = resolve;
    });
    const syncPushInstallation = jest.fn().mockResolvedValue(undefined);
    const api = {
      syncPushInstallation,
      getMessageWindow: jest.fn(),
      getMessages: jest.fn().mockResolvedValue({ messages: [], nextBefore: null }),
      markPushDeliveryOpened: jest.fn(),
    } as unknown as ApiClient;
    const onError = jest.fn();
    const onMessages = jest.fn();
    let requestPermission: (() => Promise<unknown>) | undefined;
    let tree: ReturnType<typeof create> | undefined;
    let permissionRequest: Promise<unknown> | undefined;

    function Probe() {
      const notifications = useNotifications({
        api,
        userId: '019b7777-7777-7777-8777-777777777777',
        conversationId: CONVERSATION_ID,
        historyReady: true,
        streamActive: false,
        onMessages,
        onError,
      });
      requestPermission = notifications.requestPermission;
      return (
        <Text>
          {`${notifications.permission ?? 'unknown'}|${
            notifications.registrationFailed ? 'failed' : 'ready'
          }|${
            notifications.requestingPermission
              ? '正在检查…'
              : '重新检查系统通知'
          }`}
        </Text>
      );
    }

    mockGetPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.DENIED,
    } as never);
    await act(async () => {
      tree = create(<Probe />);
    });
    syncPushInstallation.mockClear();

    mockGetPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.GRANTED,
    } as never);
    mockGetExpoPushTokenAsync.mockReturnValue(tokenRequest);
    jest.useFakeTimers();

    try {
      await act(async () => {
        appStateListener?.('active');
        await Promise.resolve();
      });
      act(() => {
        permissionRequest = requestPermission?.();
        void permissionRequest?.catch(() => undefined);
      });
      expect(
        tree!.root.findByProps({ children: 'denied|ready|正在检查…' }),
      ).toBeTruthy();

      await act(async () => {
        jest.advanceTimersByTime(15_000);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(
        tree!.root.findByProps({
          children: 'granted|failed|重新检查系统通知',
        }),
      ).toBeTruthy();
      expect(onError).toHaveBeenCalledWith(
        '通知注册超时，请检查网络和 Google Play 服务后重试。',
      );
      expect(mockGetExpoPushTokenAsync).toHaveBeenCalledTimes(1);
      expect(syncPushInstallation).not.toHaveBeenCalled();
    } finally {
      await act(async () => {
        resolveToken({ type: 'expo', data: 'ExponentPushToken[late]' });
        await permissionRequest?.catch(() => undefined);
      });
      expect(syncPushInstallation).not.toHaveBeenCalled();
      act(() => tree?.unmount());
      jest.useRealTimers();
    }
  });
});
