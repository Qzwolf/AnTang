import { act, create } from 'react-test-renderer';
import { FlatList, TextInput } from 'react-native';

import type { ApiClient } from '../api/client';
import { ChatScreen, currentLiveHeartRate } from './ChatScreen';
import { fakeGadgetbridge, fakeHealthConnect } from '../navigation/device-controllers.fake';
import type { LiveHeartRateController } from '../live-heart-rate/use-live-heart-rate';

const mockAppMenu = jest.fn((_props: unknown) => null);
const mockRefreshHealthProfileCards = jest.fn().mockResolvedValue(undefined);

jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: jest.requireActual('react-native').View,
}));

jest.mock('expo-camera', () => ({
  CameraView: () => null,
  useCameraPermissions: () => [{ granted: true }, jest.fn()],
}));

jest.mock('expo-image-picker', () => ({
  launchImageLibraryAsync: jest.fn(async () => ({
    canceled: false,
    assets: [
      {
        uri: 'file:///photos/IMG_0001.jpg',
        mimeType: 'image/jpeg',
        fileName: 'IMG_0001.jpg',
        fileSize: 4_000_000,
        width: 4032,
        height: 3024,
      },
    ],
  })),
}));

jest.mock('../health-profile/use-health-profile-cards', () => ({
  useHealthProfileCards: () => ({
    actingCardId: null,
    cards: [],
    error: null,
    loading: false,
    answer: jest.fn(),
    refresh: mockRefreshHealthProfileCards,
  }),
}));

jest.mock('../health-connect/use-health-connect', () => ({
  useHealthConnect: () => ({
    backgroundDisabling: false,
    backgroundEnabling: false,
    backgroundError: null,
    backgroundStatus: 'disabled',
    checking: false,
    connected: true,
    error: null,
    lastSyncedAt: null,
    progress: null,
    syncing: false,
    connect: jest.fn(),
    disableBackground: jest.fn(),
    enableBackground: jest.fn(),
    stop: jest.fn(),
    sync: jest.fn(),
  }),
}));

jest.mock('../notifications/use-notifications', () => ({
  useNotifications: () => ({
    permission: 'granted',
    requestingPermission: false,
    requestPermission: jest.fn(),
  }),
}));

jest.mock('../health-overview', () => ({
  HealthOverviewScreen: () => null,
}));

jest.mock('../health-profile/health-profile-cards', () => ({
  HealthProfileCards: () => null,
}));

jest.mock('../navigation/app-menu', () => ({
  AppMenu: (props: unknown) => mockAppMenu(props),
}));

jest.mock('../proactive-care/care-settings-modal', () => ({
  CareSettingsModal: () => null,
}));

describe('ChatScreen', () => {
  test('renders a photo attachment returned by chat history', async () => {
    const imageSource = {
      uri: 'file:///cache/chat-attachment-photo-1.jpg',
    };
    const api = {
      getConversations: jest.fn().mockResolvedValue([
        {
          id: '019b3333-3333-7333-8333-333333333333',
          title: '早上的对话',
          createdAt: '2026-08-16T04:00:00.000Z',
          lastMessageAt: new Date().toISOString(),
          messageCount: 1,
        },
      ]),
      startConversation: jest.fn().mockResolvedValue({
        id: '019b4444-4444-7444-8444-444444444444',
        title: '新对话',
        createdAt: '2026-08-16T05:00:00.000Z',
        lastMessageAt: null,
        messageCount: 0,
      }),
      getMessages: jest.fn().mockResolvedValue({
        messages: [
          {
            id: '019b1111-1111-7111-8111-111111111111',
            clientMessageId: '11111111-1111-4111-8111-111111111111',
            role: 'user',
            status: 'completed',
            content: '请帮我看看这张照片。',
            sources: [],
            attachments: [
              {
                id: '019b2222-2222-7222-8222-222222222222',
                kind: 'photo',
                filename: 'report.jpg',
                mimeType: 'image/jpeg',
                sizeBytes: 128,
              },
            ],
            createdAt: '2026-08-16T05:00:00.000Z',
            completedAt: null,
          },
        ],
        nextBefore: null,
      }),
      getChatAttachmentImageSource: jest.fn().mockResolvedValue(imageSource),
    } as unknown as ApiClient;

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <ChatScreen
          gadgetbridge={fakeGadgetbridge()}
          healthConnect={fakeHealthConnect()}
          api={api}
          user={{ id: 'user-1', username: 'demo', role: 'patient' }}
        />,
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const menuProps = mockAppMenu.mock.calls.at(-1)![0] as Record<string, unknown>;
    expect(menuProps.onSelectConversation).toEqual(expect.any(Function));
    expect(menuProps.conversations).toBeDefined();
    expect(api.getChatAttachmentImageSource).toHaveBeenCalledWith(
      '019b2222-2222-7222-8222-222222222222',
    );
    expect(
      tree!.root.findByProps({ accessibilityLabel: '上传的图片' }).props
        .source,
    ).toEqual(imageSource);
    expect(tree!.root.findAllByProps({ children: 'report.jpg' })).toHaveLength(0);
    act(() => tree!.unmount());
  });

  test('disables pull-to-refresh while a reply stream is active', async () => {
    let finishStream!: () => void;
    let emitStreamEvent!: (event: {
      type: 'message_failed';
      code: string;
      errorType: string;
    }) => void;
    const stream = new Promise<void>((resolve) => {
      finishStream = resolve;
    });
    const api = {
      getConversations: jest.fn().mockResolvedValue([
        {
          id: '019b3333-3333-7333-8333-333333333333',
          title: '早上的对话',
          createdAt: '2026-08-16T04:00:00.000Z',
          lastMessageAt: new Date().toISOString(),
          messageCount: 1,
        },
      ]),
      startConversation: jest.fn().mockResolvedValue({
        id: '019b4444-4444-7444-8444-444444444444',
        title: '新对话',
        createdAt: '2026-08-16T05:00:00.000Z',
        lastMessageAt: null,
        messageCount: 0,
      }),
      getMessages: jest.fn().mockResolvedValue({
        messages: [],
        nextBefore: null,
      }),
      streamMessage: jest.fn(
        (
          _clientMessageId: string,
          _content: string,
          _attachmentId: string | null,
          onEvent: typeof emitStreamEvent,
        ) => {
          emitStreamEvent = onEvent;
          return stream;
        },
      ),
    } as unknown as ApiClient;

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <ChatScreen
          gadgetbridge={fakeGadgetbridge()}
          healthConnect={fakeHealthConnect()}
          api={api}
          user={{ id: 'user-1', username: 'demo', role: 'patient' }}
        />,
      );
      await Promise.resolve();
    });

    expect(tree!.root.findByType(FlatList).props.onRefresh).toEqual(
      expect.any(Function),
    );

    act(() => {
      tree!.root.findByType(TextInput).props.onChangeText('我有点担心低血糖');
    });
    // 发送前会先把会话确定下来（没有会话就新建一段），所以这里要等一个微任务。
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '发送消息' }).props.onPress();
      await Promise.resolve();
    });

    expect(api.streamMessage).toHaveBeenCalledTimes(1);
    expect(tree!.root.findByType(FlatList).props.onRefresh).toBeUndefined();
    expect(
      tree!.root.findByProps({ accessibilityLabel: '报告解读' }).props
        .disabled,
    ).toBe(true);

    await act(async () => {
      emitStreamEvent({
        type: 'message_failed',
        code: 'agent_run_failed',
        errorType: 'TestFailure',
      });
      finishStream();
      await stream;
    });
    act(() => tree!.unmount());
  });
  test('starts a fresh session when the last conversation is hours old', async () => {
    const eightHoursAgo = new Date(Date.now() - 8 * 3_600_000).toISOString();
    const getMessages = jest
      .fn()
      .mockResolvedValue({ messages: [], nextBefore: null });
    const streamMessage = jest.fn().mockResolvedValue(undefined);
    const api = {
      getConversations: jest.fn().mockResolvedValue([
        {
          id: '019b3333-3333-7333-8333-333333333333',
          title: '早上的对话',
          createdAt: eightHoursAgo,
          lastMessageAt: eightHoursAgo,
          messageCount: 4,
        },
      ]),
      startConversation: jest.fn().mockResolvedValue({
        id: '019b4444-4444-7444-8444-444444444444',
        title: '新对话',
        createdAt: new Date().toISOString(),
        lastMessageAt: null,
        messageCount: 0,
      }),
      getMessages,
      streamMessage,
    } as unknown as ApiClient;

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <ChatScreen
          gadgetbridge={fakeGadgetbridge()}
          healthConnect={fakeHealthConnect()}
          api={api}
          user={{ id: 'user-1', username: 'demo', role: 'patient' }}
        />,
      );
      await Promise.resolve();
    });

    // 上一次聊天已经过去 8 小时：不该把它当成正在聊的那段，历史也不该拉进来。
    expect(getMessages).not.toHaveBeenCalled();

    act(() => {
      tree!.root.findByType(TextInput).props.onChangeText('又来问一下');
    });
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '发送消息' }).props.onPress();
      await Promise.resolve();
    });

    expect(api.startConversation).toHaveBeenCalledTimes(1);
    expect(streamMessage).toHaveBeenCalledTimes(1);
    expect(streamMessage.mock.calls[0][6]).toBe(
      '019b4444-4444-7444-8444-444444444444',
    );
    act(() => tree!.unmount());
  });

  test('leaves empty conversations out of the drawer and groups by recency', async () => {
    const now = new Date().toISOString();
    const eightDaysAgo = new Date(Date.now() - 8 * 86_400_000).toISOString();
    const api = {
      getConversations: jest.fn().mockResolvedValue([
        {
          id: '019b3333-3333-7333-8333-333333333333',
          title: '今天的对话',
          createdAt: now,
          lastMessageAt: now,
          messageCount: 3,
        },
        {
          id: '019b5555-5555-7555-8555-555555555555',
          title: '新对话',
          createdAt: now,
          lastMessageAt: null,
          messageCount: 0,
        },
        {
          id: '019b6666-6666-7666-8666-666666666666',
          title: '上周的对话',
          createdAt: eightDaysAgo,
          lastMessageAt: eightDaysAgo,
          messageCount: 9,
        },
      ]),
      getMessages: jest
        .fn()
        .mockResolvedValue({ messages: [], nextBefore: null }),
      startConversation: jest.fn(),
      streamMessage: jest.fn(),
    } as unknown as ApiClient;

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <ChatScreen
          gadgetbridge={fakeGadgetbridge()}
          healthConnect={fakeHealthConnect()}
          api={api}
          user={{ id: 'user-1', username: 'demo', role: 'patient' }}
        />,
      );
      await Promise.resolve();
    });

    const menuProps = mockAppMenu.mock.calls.at(-1)![0] as {
      conversations: { id: string; group: string }[];
    };
    expect(menuProps.conversations.map((item) => item.id)).toEqual([
      '019b3333-3333-7333-8333-333333333333',
      '019b6666-6666-7666-8666-666666666666',
    ]);
    expect(menuProps.conversations.map((item) => item.group)).toEqual([
      '今天',
      '近 30 天',
    ]);
    act(() => tree!.unmount());
  });
});

test('a picked photo is shrunk before it is uploaded', async () => {
  const { manipulateAsync } = jest.requireMock('expo-image-manipulator');
  const { Image } = jest.requireActual('react-native');
  jest
    .spyOn(Image, 'getSize')
    .mockImplementation((_uri, onLoad, _onError) => {
      (onLoad as (w: number, h: number) => void)(4032, 3024);
    });

  const now = new Date().toISOString();
  const api = {
    getConversations: jest.fn().mockResolvedValue([
      {
        id: '019b7777-7777-7777-8777-777777777777',
        title: '今天的对话',
        createdAt: now,
        lastMessageAt: now,
        messageCount: 1,
      },
    ]),
    getMessages: jest.fn().mockResolvedValue({ messages: [], nextBefore: null }),
    startConversation: jest.fn(),
    uploadChatAttachment: jest.fn().mockResolvedValue({
      id: 'attachment-1',
      kind: 'photo',
      filename: 'IMG_0001.jpg',
      mimeType: 'image/jpeg',
      sizeBytes: 120_000,
    }),
    streamMessage: jest.fn().mockResolvedValue(undefined),
  } as unknown as ApiClient;

  let tree: ReturnType<typeof create>;
  await act(async () => {
    tree = create(
      <ChatScreen
        gadgetbridge={fakeGadgetbridge()}
        healthConnect={fakeHealthConnect()}
        api={api}
        user={{ id: 'user-1', username: 'demo', role: 'patient' }}
      />,
    );
    await Promise.resolve();
  });

  await act(async () => {
    tree!.root.findByProps({ accessibilityLabel: '拍照' }).props.onPress();
    await Promise.resolve();
  });
  await act(async () => {
    tree!.root
      .findAllByProps({ accessibilityLabel: '从相册选择' })[0]
      .props.onPress();
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(
    tree!.root.findAllByProps({ accessibilityLabel: '移除附件' }).length,
  ).toBeGreaterThan(0);

  await act(async () => {
    tree!.root.findByProps({ accessibilityLabel: '发送消息' }).props.onPress();
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(manipulateAsync).toHaveBeenCalledWith(
    'file:///photos/IMG_0001.jpg',
    [{ resize: { width: 1568 } }],
    { compress: 0.82, format: 'jpeg' },
  );
  const uploaded = (api as unknown as { uploadChatAttachment: jest.Mock })
    .uploadChatAttachment;
  expect(uploaded).toHaveBeenCalledWith(
    expect.objectContaining({ kind: 'photo', mimeType: 'image/jpeg' }),
  );
  act(() => tree!.unmount());
});

test('only attaches a fresh sample from an active heart rate stream', () => {
  const controller = {
    phase: 'streaming', latest: { beatsPerMinute: 78, measuredAt: 100_000, name: '手环' },
  } as LiveHeartRateController;
  expect(currentLiveHeartRate(controller, 100_500)?.beatsPerMinute).toBe(78);
  expect(currentLiveHeartRate(controller, 131_000)).toBeNull();
  expect(currentLiveHeartRate(controller, 99_000)).toBeNull();
  expect(currentLiveHeartRate({ ...controller, phase: 'idle' }, 100_500)).toBeNull();
});
