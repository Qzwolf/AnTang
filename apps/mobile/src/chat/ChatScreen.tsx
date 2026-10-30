import {
  ArrowUp01Icon,
  Attachment01Icon,
  Camera01Icon,
  FileChartLineIcon,
  FolderHeartIcon,
  Menu01Icon,
  Mic01Icon,
  StopIcon,
} from '@hugeicons/core-free-icons';
import { randomUUID } from 'expo-crypto';
import { StatusBar as ExpoStatusBar } from 'expo-status-bar';
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import {
  ActivityIndicator,
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiClient, ApiError, errorMessage, type LiveHeartRateReading } from '../api/client';
import type {
  ChatAttachment,
  ChatMessage,
  ChatStreamEvent,
  Conversation,
  MessageHistory,
  User,
} from '../api/types';
import { ActivityStatus } from './ActivityStatus';
import {
  AttachmentPreview,
  MessageAttachmentCard,
  CameraScreen,
  type DraftAttachment,
  ReportPickerScreen,
} from './attachment-flows';
import { chatReducer, initialChatState } from './chat-state';
import { shrinkPhoto } from './photo-size';
import { formatVoiceDuration, mergeTranscriptIntoDraft } from './voice-recording';
import { useVoiceInput } from './useVoiceInput';
import { VOICE_INPUT_ENABLED } from '../config';
import { MessageSources } from './MessageSources';
import { HealthProfileCards } from '../health-profile/health-profile-cards';
import { useHealthProfileCards } from '../health-profile/use-health-profile-cards';
import type {
  GadgetbridgeController,
  HealthConnectController,
} from '../navigation/device-channels';
import { HealthOverviewScreen } from '../health-overview';
import { HealthDashboardScreen } from '../health/HealthDashboardScreen';
import { PatientAccessScreen } from '../patient-access/patient-access-screen';
import { GlucoseScreen } from '../glucose/GlucoseScreen';
import { GlucoseImportScreen } from '../glucose/GlucoseImportScreen';
import { useLiveHeartRateControllerOrNull } from '../live-heart-rate/LiveHeartRateProvider';
import type { LiveHeartRateController } from '../live-heart-rate/use-live-heart-rate';
import { AppMenu, type ConversationAnchor } from '../navigation/app-menu';
import { useNotifications } from '../notifications/use-notifications';
import { CareSettingsModal } from '../proactive-care/care-settings-modal';
import { SettingsScreen } from '../settings/settings-screen';
import { UpdatePrompt } from '../update/update-prompt';
import { useUpdateCheck } from '../update/use-update-check';
import { BrandMark } from '../ui/brand-mark';
import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';

type Props = {
  api: ApiClient;
  user: User;
  /** 手环两条通道挂在 App 外壳上，这里只用来展示关怀设置与看板。 */
  healthConnect: HealthConnectController;
  gadgetbridge: GadgetbridgeController;
  /**
   * 从底部 tab 的「我的 / 记录」请求打开某一页。
   * 用一个自增的 id 传递，避免「同一页连点两次」时状态相同而不触发。
   */
  pageRequest?: { page: ChatPage; id: number } | null;
  /** 侧边栏头像 → 底部「我的」tab。 */
  onOpenProfile?: () => void;
};

export type ChatPage =
  | 'health'
  | 'dashboard'
  | 'glucose'
  | 'importSensor'
  | 'patientAccess'
  | 'care'
  | 'settings';

const dayFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'short',
});

const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function localDay(value: string): string {
  const date = new Date(value);
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

// 医疗口径提醒只在这条回复真的谈到诊断、用药、剂量、检验读数时出现。
// 每条都挂等于免责声明噪音，读者会当它不存在。
const MEDICAL_TOPICS =
  /(诊断|处方|用药|剂量|药物|胰岛素|口服药|化验|检验|报告|指标|读数|血压|血脂|糖化|并发症|治疗|手术)/;
const EMERGENCY_SIGNS =
  /(昏迷|意识不清|抽搐|酮症|酸中毒|呼吸困难|胸痛|剧烈|急诊|叫救护车)/;

/**
 * 回复底下那行灰字。
 *
 * 只做「知识参考 + 听医生的」这一件事：不重复回复内容、不吓人、不写成长段免责声明。
 * 提到危险信号时多加一句立刻就医，措辞按患者能听懂的说法（不写「请即刻就医」这种公文腔）。
 */
export function medicalFootnote(text: string): string | null {
  if (!MEDICAL_TOPICS.test(text)) return null;
  const base =
    '以上是健康知识参考，不能替代医生的诊断和处方。具体用药请按医生给你的方案。';
  if (EMERGENCY_SIGNS.test(text)) {
    return `${base}如果出现意识不清、抽搐、呼吸困难或很难受，请立刻联系急救，别等回复。`;
  }
  return base;
}

/**
 * 隔了这么久再说话就算另一次对话（和后端 `SESSION_GAP` 一致）：离开几小时
 * 回来，不该接着上一次那一段，也不该把历史全堆在一条里。
 */
export const SESSION_GAP_MS = 6 * 3_600_000;

function staleSession(value: string | null): boolean {
  if (value === null) return false;
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return false;
  return Date.now() - at > SESSION_GAP_MS;
}

function conversationGroup(value: string): string {
  const date = new Date(value);
  const now = new Date();
  if (localDay(value) === localDay(now.toISOString())) return '今天';
  const ageDays = (now.getTime() - date.getTime()) / 86_400_000;
  if (ageDays < 7) return '近 7 天';
  if (ageDays < 30) return '近 30 天';
  return '更早';
}

/** 每条都带具体日期与时刻——只给「3 天前」这类相对说法，过一周就分不清了。 */
function conversationStamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${month}-${day} ${timeFormatter.format(date)}`;
}

/**
 * 只有此刻真的在收心率广播时才把读数带给后端；其余情况明确不带，
 * 后端也就不会把任何数字当成实时值。
 */
export function currentLiveHeartRate(
  controller: LiveHeartRateController,
  now = Date.now(),
): LiveHeartRateReading | null {
  if (controller.phase !== 'streaming' || controller.latest === null) return null;
  const age = now - controller.latest.measuredAt;
  if (!Number.isFinite(age) || age < 0 || age > 30_000) return null;
  return {
    beatsPerMinute: controller.latest.beatsPerMinute,
    // 原生模块给的是手机收到这一帧的时刻（epoch 毫秒）。
    measuredAt: new Date(controller.latest.measuredAt).toISOString(),
    deviceName: controller.latest.name,
  };
}

export function ChatScreen({
  api,
  user,
  pageRequest = null,
  onOpenProfile,
  healthConnect,
  gadgetbridge,
}: Props) {
  // 实时心率会话由 App 外壳的 LiveHeartRateProvider 提供，四个 tab 共用一条 BLE 连接。
  return (
    <ChatScreenContent
      api={api}
      gadgetbridge={gadgetbridge}
      healthConnect={healthConnect}
      onOpenProfile={onOpenProfile}
      pageRequest={pageRequest}
      user={user}
    />
  );
}

function ChatScreenContent({
  api,
  user,
  onOpenProfile,
  healthConnect,
  gadgetbridge,
  pageRequest = null,
}: Props) {
  // 会话由 App 外壳提供；单独渲染聊天页（例如测试里）时没有 Provider，就当成没有心率。
  const liveHeartRate = useLiveHeartRateControllerOrNull();
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [draft, setDraft] = useState('');
  const [historyReady, setHistoryReady] = useState(false);
  const [targetMessageId, setTargetMessageId] = useState<string | null>(null);
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showCareSettings, setShowCareSettings] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showHealthOverview, setShowHealthOverview] = useState(false);
  // ===== 新增：健康看板 =====
  const [showDashboard, setShowDashboard] = useState(false);
  const [showGlucose, setShowGlucose] = useState(false);
  const [showImportSensor, setShowImportSensor] = useState(false);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [showCamera, setShowCamera] = useState(false);
  const [showReportPicker, setShowReportPicker] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [showPatientAccess, setShowPatientAccess] = useState(false);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [attachment, setAttachment] = useState<DraftAttachment | null>(null);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  const inputRef = useRef<TextInput>(null);
  const listRef = useRef<FlatList<ChatMessage>>(null);
  const streamController = useRef<AbortController | null>(null);
  const nearEnd = useRef(true);
  const knownMessageIds = useMemo(
    () => new Set(state.messages.map((message) => message.id)),
    [state.messages],
  );
  const healthProfileCards = useHealthProfileCards(api);
  const updateCheck = useUpdateCheck();
  const refreshHealthProfileCards = healthProfileCards.refresh;
  const getAttachmentImageSource = useCallback(
    (attachmentId: string) => api.getChatAttachmentImageSource(attachmentId),
    [api],
  );
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationAnchors = useMemo<ConversationAnchor[]>(
    () =>
      conversations
        // 点了「新的对话」却一句没说的空段不占位（当前正打开的那段除外）。
        .filter(
          (conversation) =>
            conversation.messageCount > 0 || conversation.id === conversationId,
        )
        .map((conversation) => ({
          group: conversationGroup(
            conversation.lastMessageAt ?? conversation.createdAt,
          ),
          id: conversation.id,
          title: conversation.title,
          time: conversationStamp(
            conversation.lastMessageAt ?? conversation.createdAt,
          ),
        })),
    [conversationId, conversations],
  );

  useEffect(() => {
    if (pageRequest === null) return;
    // 放到下一个 tick：effect 里同步 setState 会触发级联渲染（expo lint 会拦）。
    const timer = setTimeout(() => {
      setShowMenu(false);
      if (pageRequest.page === 'health') setShowHealthOverview(true);
      if (pageRequest.page === 'dashboard') setShowDashboard(true);
      if (pageRequest.page === 'glucose') setShowGlucose(true);
      if (pageRequest.page === 'importSensor') setShowImportSensor(true);
      if (pageRequest.page === 'patientAccess') setShowPatientAccess(true);
      if (pageRequest.page === 'care') setShowCareSettings(true);
      if (pageRequest.page === 'settings') setShowSettings(true);
    }, 0);
    return () => clearTimeout(timer);
  }, [pageRequest]);

  useEffect(() => {
    const showSubscription = Keyboard.addListener('keyboardDidShow', () =>
      setKeyboardVisible(true),
    );
    const hideSubscription = Keyboard.addListener('keyboardDidHide', () =>
      setKeyboardVisible(false),
    );
    return () => {
      showSubscription.remove();
      hideSubscription.remove();
    };
  }, []);

  const mergeNotificationMessages = useCallback(
    (messages: ChatMessage[], targetId: string | null) => {
      const hasNewMessage = messages.some(
        (message) => !knownMessageIds.has(message.id),
      );
      dispatch({ type: 'history-merged', messages });
      if (targetId !== null) {
        setTargetMessageId(targetId);
        setShowNewMessage(false);
      } else if (hasNewMessage && nearEnd.current) {
        requestAnimationFrame(() =>
          listRef.current?.scrollToEnd({ animated: true }),
        );
      } else if (hasNewMessage) {
        setShowNewMessage(true);
      }
    },
    [knownMessageIds],
  );

  const reportNotificationError = useCallback((message: string) => {
    dispatch({ type: 'history-failed', message });
  }, []);

  const fetchLatest = useCallback(async () => {
    // 还没开新会话时不要顺手把「最近一段」的历史拉进来——那段已经过去很久了，
    // 拉进来会让人以为自己在接着聊，其实是一段旧对话。
    if (conversationId === null) {
      await refreshHealthProfileCards();
      return { messages: [], nextBefore: null } satisfies MessageHistory;
    }
    const [page] = await Promise.all([
      api.getMessages(null, conversationId),
      refreshHealthProfileCards(),
    ]);
    return page;
  }, [api, conversationId, refreshHealthProfileCards]);

  const applyLatest = useCallback((page: MessageHistory) => {
    dispatch({
      type: 'history-replaced',
      messages: page.messages,
      nextBefore: page.nextBefore,
    });
    setHistoryReady(true);
  }, []);

  const loadLatest = useCallback(async () => {
    dispatch({ type: 'history-loading' });
    try {
      applyLatest(await fetchLatest());
    } catch (error) {
      dispatch({ type: 'history-failed', message: errorMessage(error) });
    }
  }, [applyLatest, fetchLatest]);

  const refreshLatest = useCallback(() => {
    if (streamController.current !== null) return;
    void loadLatest();
  }, [loadLatest]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const existing = await api.getConversations();
        const newest = existing[0];
        if (!active) return;
        setConversations(existing);
        if (
          newest === undefined ||
          staleSession(newest.lastMessageAt ?? newest.createdAt)
        ) {
          // 上次聊天已经过去很久：从一段空白开始，旧对话留在侧边栏里可以回去看。
          setConversationId(null);
          dispatch({
            type: 'history-replaced',
            messages: [],
            nextBefore: null,
          });
          setHistoryReady(true);
          return;
        }
        setConversationId(newest.id);
        const page = await api.getMessages(null, newest.id);
        if (active) applyLatest(page);
      } catch (error) {
        if (active) {
          dispatch({ type: 'history-failed', message: errorMessage(error) });
        }
      }
    })();
    return () => {
      active = false;
      streamController.current?.abort();
    };
  }, [api, applyLatest]);

  /** 侧边栏的列表要跟着最新的消息走，否则时间停在打开 App 那一刻。 */
  const refreshConversations = useCallback(async () => {
    try {
      const latest = await api.getConversations();
      setConversations(latest);
    } catch {
      // 列表刷新失败不该打断正在进行的聊天，下次打开侧边栏会再试。
    }
  }, [api]);

  useEffect(() => {
    if (!showMenu) return;
    // 放进下一拍：直接在这里拉列表会同步 setState，触发 react-hooks 的告警。
    const timer = setTimeout(() => void refreshConversations(), 0);
    return () => clearTimeout(timer);
  }, [refreshConversations, showMenu]);

  /** 发消息时才真正建会话：只点开 App 不该在列表里留下一段空对话。 */
  const ensureConversation = useCallback(async (): Promise<string> => {
    if (conversationId !== null) return conversationId;
    const conversation = await api.startConversation();
    setConversations((current) => [conversation, ...current]);
    setConversationId(conversation.id);
    return conversation.id;
  }, [api, conversationId]);

  const openConversation = useCallback(
    async (id: string) => {
      if (streamController.current !== null) return;
      setShowMenu(false);
      // 点的就是当前这段：不用重拉，否则整屏消息会闪一下。
      if (id === conversationId && historyReady) return;
      setConversationId(id);
      dispatch({ type: 'history-loading' });
      try {
        applyLatest(await api.getMessages(null, id));
      } catch (error) {
        dispatch({ type: 'history-failed', message: errorMessage(error) });
      }
    },
    [api, applyLatest, conversationId, historyReady],
  );

  const startNewConversation = useCallback(async () => {
    if (streamController.current !== null) return;
    try {
      const conversation = await api.startConversation();
      setConversations((current) => [conversation, ...current]);
      setConversationId(conversation.id);
      setShowMenu(false);
      dispatch({
        type: 'history-replaced',
        messages: [],
        nextBefore: null,
      });
      setHistoryReady(true);
    } catch (error) {
      dispatch({ type: 'history-failed', message: errorMessage(error) });
    }
  }, [api]);

  const notifications = useNotifications({
    api,
    userId: user.id,
    conversationId,
    historyReady: historyReady && !state.historyLoading,
    streamActive: state.pendingRequestId !== null,
    onMessages: mergeNotificationMessages,
    onError: reportNotificationError,
  });

  const lastMessage = state.messages[state.messages.length - 1];
  useEffect(() => {
    if (lastMessage === undefined) return;
    if (!nearEnd.current && state.pendingRequestId === null) return;
    const frame = requestAnimationFrame(() =>
      listRef.current?.scrollToEnd({ animated: true }),
    );
    return () => cancelAnimationFrame(frame);
  }, [lastMessage, state.activity, state.pendingRequestId]);

  useEffect(() => {
    if (targetMessageId === null) return;
    const index = state.messages.findIndex(
      (message) => message.id === targetMessageId,
    );
    if (index < 0) return;
    requestAnimationFrame(() =>
      listRef.current?.scrollToIndex({
        animated: true,
        index,
        viewPosition: 0.5,
      }),
    );
    const timer = setTimeout(() => setTargetMessageId(null), 3000);
    return () => clearTimeout(timer);
  }, [state.messages, targetMessageId]);

  const busy = state.sending || state.historyLoading || uploadingAttachment;
  const transcribeVoice = useCallback(
    (recording: { mimeType: string; name: string; uri: string }) =>
      api.transcribeVoice(recording),
    [api],
  );
  const handleTranscript = useCallback((text: string) => {
    setDraft((current) => mergeTranscriptIntoDraft(current, text));
  }, []);
  const voice = useVoiceInput(transcribeVoice, handleTranscript);
  const voiceBusy = voice.phase !== 'idle';
  const showVoiceButton =
    VOICE_INPUT_ENABLED &&
    (voiceBusy || (draft.trim().length === 0 && attachment === null && !busy));
  async function loadOlder() {
    if (state.nextBefore === null || state.historyLoading) return;
    dispatch({ type: 'history-loading' });
    try {
      const page = await api.getMessages(state.nextBefore, conversationId);
      dispatch({
        type: 'history-prepended',
        messages: page.messages,
        nextBefore: page.nextBefore,
      });
    } catch (error) {
      dispatch({ type: 'history-failed', message: errorMessage(error) });
    }
  }

  async function runChatStream(
    requestId: string,
    start: () => void,
    request: (
      onEvent: (event: ChatStreamEvent) => void,
      signal: AbortSignal,
    ) => Promise<void>,
  ) {
    if (busy || streamController.current !== null) return;
    const controller = new AbortController();
    streamController.current = controller;
    start();

    try {
      await request(
        (event) => dispatch({ type: 'stream-event', event }),
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted) {
        dispatch({ type: 'request-cancelled' });
      } else if (error instanceof ApiError) {
        const refreshHistory =
          error.code === 'duplicate_client_message' ||
          error.code === 'chat_retry_not_allowed' ||
          error.code === 'retry_message_not_found';
        let message: string | null = errorMessage(error);
        if (error.code === 'active_agent_run') {
          message = '上一条回复仍在生成，请刷新聊天记录查看状态。';
        } else if (refreshHistory) {
          message = null;
        }
        dispatch({ type: 'request-rejected', requestId, message });
        if (refreshHistory) await loadLatest();
      } else {
        dispatch({ type: 'transport-failed', message: errorMessage(error) });
      }
    } finally {
      if (streamController.current === controller)
        streamController.current = null;
      await refreshHealthProfileCards();
    }
  }

  async function sendContent(rawContent: string) {
    let content = rawContent.trim();
    if (content.length === 0 && attachment === null) return;

    const selectedAttachment = attachment;
    let uploadedAttachment: ChatAttachment | null = null;
    if (selectedAttachment !== null) {
      setUploadingAttachment(true);
      try {
        // 照片先缩到长边 1568 再上传：模型那边一整张图看得清，也省流量。
        const prepared = await shrinkPhoto(selectedAttachment);
        uploadedAttachment = await api.uploadChatAttachment(prepared);
        uploadedAttachment.localUri = prepared.uri;
      } catch (error) {
        dispatch({ type: 'history-failed', message: errorMessage(error) });
        return;
      } finally {
        setUploadingAttachment(false);
      }
      if (content.length === 0) {
        content =
          selectedAttachment.kind === 'report'
            ? '请帮我解读这份报告。'
            : '请帮我看看这张照片。';
      }
    }

    const clientMessageId = randomUUID();
    let targetConversationId: string;
    try {
      targetConversationId = await ensureConversation();
    } catch (error) {
      dispatch({ type: 'history-failed', message: errorMessage(error) });
      return;
    }
    await runChatStream(
      clientMessageId,
      () => {
        setDraft('');
        setAttachment(null);
        dispatch({
          type: 'send-started',
          clientMessageId,
          content,
          attachment: uploadedAttachment,
          now: new Date().toISOString(),
        });
      },
      (onEvent, signal) =>
        api.streamMessage(
          clientMessageId,
          content,
          uploadedAttachment?.id ?? null,
          onEvent,
          signal,
          liveHeartRate === null ? null : currentLiveHeartRate(liveHeartRate),
          targetConversationId,
        ),
    );
    void refreshConversations();
  }

  function cancelCurrentResponse() {
    streamController.current?.abort();
  }

  async function retry(failedAssistantMessageId: string) {
    const message = state.messages[state.messages.length - 1];
    if (
      message?.id !== failedAssistantMessageId ||
      message.role !== 'assistant' ||
      (message.status !== 'failed' && message.status !== 'cancelled')
    ) {
      dispatch({ type: 'history-failed', message: '只能重试最后一条失败的回复。' });
      return;
    }

    const localPrefix = 'local-assistant-';
    if (failedAssistantMessageId.startsWith(localPrefix)) {
      const clientMessageId = failedAssistantMessageId.slice(localPrefix.length);
      const userMessage = state.messages.find(
        (candidate) => candidate.id === `local-user-${clientMessageId}`,
      );
      if (userMessage?.role !== 'user') {
        dispatch({ type: 'history-failed', message: '找不到这次发送的原消息。' });
        return;
      }
      let targetConversationId: string;
      try {
        targetConversationId = await ensureConversation();
      } catch (error) {
        dispatch({ type: 'history-failed', message: errorMessage(error) });
        return;
      }
      await runChatStream(
        clientMessageId,
        () =>
          dispatch({
            type: 'send-started',
            clientMessageId,
            content: userMessage.content,
            attachment: userMessage.attachments[0] ?? null,
            now: new Date().toISOString(),
          }),
        (onEvent, signal) =>
          api.streamMessage(
            clientMessageId,
            userMessage.content,
            userMessage.attachments[0]?.id ?? null,
            onEvent,
            signal,
            undefined,
            targetConversationId,
          ),
      );
      return;
    }

    const requestId = randomUUID();
    await runChatStream(
      requestId,
      () =>
        dispatch({
          type: 'retry-started',
          requestId,
          failedAssistantMessageId,
          now: new Date().toISOString(),
        }),
      (onEvent, signal) =>
        api.retryMessage(failedAssistantMessageId, onEvent, signal),
    );
  }

  function renderMessage(message: ChatMessage, index: number) {
    const assistant = message.role === 'assistant';
    const canRetry =
      assistant &&
      index === state.messages.length - 1 &&
      (message.status === 'failed' || message.status === 'cancelled');
    const previousMessage = state.messages[index - 1];
    const showDate =
      previousMessage === undefined ||
      localDay(previousMessage.createdAt) !== localDay(message.createdAt);

    const waitingFirstToken =
      assistant && message.status === 'generating' && message.content.length === 0;
    const showBubble =
      message.content.length > 0 ||
      message.status !== 'generating' ||
      assistant;
    const activity =
      assistant && message.status === 'generating' ? state.activity : null;
    const footnote =
      assistant && message.status === 'completed'
        ? medicalFootnote(message.content)
        : null;

    return (
      <>
        {showDate ? (
          <View style={styles.dateDivider}>
            <View style={styles.dateRule} />
            <Text style={styles.dateText}>
              {dayFormatter.format(new Date(message.createdAt))}
            </Text>
            <View style={styles.dateRule} />
          </View>
        ) : null}
        <View
          style={[
            styles.messageRow,
            assistant ? styles.assistantMessageRow : styles.userMessageRow,
          ]}
        >
          <View
            style={[
              styles.messageContent,
              assistant ? styles.assistantContent : styles.userContent,
              message.id === targetMessageId ? styles.targetMessage : undefined,
            ]}
          >
            {message.attachments.map((item) => (
              <MessageAttachmentCard
                attachment={item}
                getImageSource={getAttachmentImageSource}
                key={item.id}
              />
            ))}
            {showBubble ? (
              <View
                style={[
                  styles.bubble,
                  assistant ? styles.assistantBubble : styles.userBubble,
                ]}
              >
                {waitingFirstToken ? (
                  <View style={styles.thinkingRow} accessibilityLiveRegion="polite">
                    <Text style={styles.thinkingText}>糖糖正在想</Text>
                    <View style={styles.thinkingDots}>
                      <View style={styles.thinkingDot} />
                      <View style={styles.thinkingDot} />
                      <View style={styles.thinkingDot} />
                    </View>
                  </View>
                ) : (
                  <MessageSources
                    content={message.content || '无内容'}
                    onOpenError={(error) =>
                      dispatch({ type: 'history-failed', message: error })
                    }
                    sources={
                      assistant && message.status === 'completed'
                        ? message.sources
                        : []
                    }
                    tone={assistant ? 'assistant' : 'user'}
                  />
                )}
                {footnote === null ? null : (
                  <Text style={styles.medicalFootnote}>{footnote}</Text>
                )}
                {!assistant || message.status !== 'completed' ? (
                  <View style={styles.messageMeta}>
                    <Text
                      style={[
                        styles.messageTime,
                        assistant
                          ? styles.assistantMessageTime
                          : styles.userMessageTime,
                      ]}
                    >
                      {timeFormatter.format(new Date(message.createdAt))}
                    </Text>
                    {message.status === 'failed' ? (
                      <Text style={styles.failedStatus}>回复失败</Text>
                    ) : null}
                    {message.status === 'cancelled' ? (
                      <Text style={styles.cancelledStatus}>已停止</Text>
                    ) : null}
                  </View>
                ) : null}
                {canRetry ? (
                  <Pressable
                    accessibilityRole="button"
                    disabled={busy}
                    onPress={() => void retry(message.id)}
                    style={({ pressed }) => [
                      styles.retryButton,
                      pressed && styles.retryButtonPressed,
                    ]}
                  >
                    <Text style={styles.retryText}>重新生成</Text>
                  </Pressable>
                ) : null}
              </View>
            ) : null}
            {activity === null ? null : <ActivityStatus phase={activity} />}
          </View>
        </View>
      </>
    );
  }

  return (
    <KeyboardAvoidingView
      behavior={process.env.EXPO_OS === 'ios' ? 'padding' : 'height'}
      style={styles.screen}
    >
      <SafeAreaView edges={['top']} style={styles.headerSafeArea}>
        <View style={styles.header}>
          <Pressable
            accessibilityLabel="打开菜单"
            accessibilityRole="button"
            onPress={() => setShowMenu(true)}
            style={({ pressed }) => [
              styles.headerButton,
              pressed && styles.headerButtonPressed,
            ]}
          >
            <AppIcon icon={Menu01Icon} size={25} />
          </Pressable>

          <View style={styles.headerIdentity}>
            <Text style={styles.title}>安糖心语</Text>
          </View>

          <View style={styles.headerPlaceholder} />
        </View>
      </SafeAreaView>

      {/* ===== 传感器导入结果 ===== */}
      {importNotice === null ? null : (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="关闭提示"
          onPress={() => setImportNotice(null)}
          style={styles.importNotice}
        >
          <Text style={styles.importNoticeText}>{importNotice}</Text>
          <Text style={styles.healthNoticeAction}>知道了</Text>
        </Pressable>
      )}

      {state.error === null ? null : (
        <Pressable
          accessibilityRole="button"
          onPress={() => dispatch({ type: 'clear-error' })}
          style={styles.errorBanner}
        >
          <View style={styles.errorDot} />
          <Text selectable style={styles.errorText}>
            {state.error}
          </Text>
          <Text style={styles.dismissText}>关闭</Text>
        </Pressable>
      )}

      {showNewMessage ? (
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            nearEnd.current = true;
            setShowNewMessage(false);
            listRef.current?.scrollToEnd({ animated: true });
          }}
          style={styles.newMessageBanner}
        >
          <View style={styles.newMessageDot} />
          <Text style={styles.newMessageText}>有一条新消息</Text>
        </Pressable>
      ) : null}

      <FlatList
        ref={listRef}
        contentContainerStyle={styles.messageList}
        contentInsetAdjustmentBehavior="automatic"
        data={state.messages}
        keyboardShouldPersistTaps="handled"
        keyExtractor={(item) => item.id}
        ListEmptyComponent={
          state.historyLoading ? (
            <View style={styles.emptyState}>
              <ActivityIndicator color={colors.primary} />
              <Text style={styles.emptyLoadingText}>正在读取对话</Text>
            </View>
          ) : (
            <View style={styles.emptyState}>
              <BrandMark size={48} />
              <Text style={styles.emptyTitle}>有什么想和我聊聊？</Text>
              <Text style={styles.emptyText}>
                可以问健康问题，也可以说说最近让你担心的事。
              </Text>
            </View>
          )
        }
        ListHeaderComponent={
          state.nextBefore === null ? null : (
            <Pressable
              accessibilityRole="button"
              disabled={state.historyLoading}
              onPress={() => void loadOlder()}
              style={({ pressed }) => [
                styles.olderButton,
                pressed && styles.olderButtonPressed,
              ]}
            >
              <Text style={styles.olderText}>
                {state.historyLoading ? '正在加载…' : '再往前看看'}
              </Text>
            </Pressable>
          )
        }
        ListFooterComponent={
          <View style={styles.profileCards}>
            <HealthProfileCards
              actingCardId={healthProfileCards.actingCardId}
              cards={healthProfileCards.cards}
              error={healthProfileCards.error}
              loading={healthProfileCards.loading}
              onAnswer={(cardId, answer) =>
                void healthProfileCards.answer(cardId, answer)
              }
            />
          </View>
        }
        onRefresh={state.pendingRequestId === null ? refreshLatest : undefined}
        onScroll={({ nativeEvent }) => {
          const distanceFromEnd =
            nativeEvent.contentSize.height -
            nativeEvent.layoutMeasurement.height -
            nativeEvent.contentOffset.y;
          nearEnd.current = distanceFromEnd < 80;
          if (nearEnd.current && showNewMessage) setShowNewMessage(false);
        }}
        onScrollToIndexFailed={({ averageItemLength, index }) => {
          listRef.current?.scrollToOffset({
            animated: true,
            offset: averageItemLength * index,
          });
        }}
        refreshing={historyReady && state.historyLoading}
        renderItem={({ item, index }) => renderMessage(item, index)}
        scrollEventThrottle={100}
      />

      <SafeAreaView edges={['bottom']} style={styles.composerSafeArea}>
        {!keyboardVisible ? (
          <View
            accessibilityElementsHidden={state.pendingRequestId !== null}
            importantForAccessibility={
              state.pendingRequestId !== null ? 'no-hide-descendants' : 'auto'
            }
            style={[
              styles.quickActions,
              state.pendingRequestId !== null && styles.quickActionsHidden,
            ]}
          >
            <Pressable
              accessibilityLabel="报告解读"
              accessibilityRole="button"
              disabled={busy}
              onPress={() => setShowReportPicker(true)}
              style={({ pressed }) => [
                styles.quickAction,
                pressed && styles.quickActionPressed,
              ]}
            >
              <AppIcon
                color={colors.primaryPressed}
                icon={FileChartLineIcon}
                size={20}
              />
              <Text style={styles.quickActionText}>报告解读</Text>
            </Pressable>
            <Pressable
              accessibilityLabel="健康档案"
              accessibilityRole="button"
              disabled={busy}
              onPress={() => setShowHealthOverview(true)}
              style={({ pressed }) => [
                styles.quickAction,
                pressed && styles.quickActionPressed,
              ]}
            >
              <AppIcon
                color={colors.primaryPressed}
                icon={FolderHeartIcon}
                size={20}
              />
              <Text style={styles.quickActionText}>健康档案</Text>
            </Pressable>
            <Pressable
              accessibilityLabel="拍照"
              accessibilityRole="button"
              disabled={busy}
              onPress={() => setShowCamera(true)}
              style={({ pressed }) => [
                styles.quickAction,
                pressed && styles.quickActionPressed,
              ]}
            >
              <AppIcon color={colors.primaryPressed} icon={Camera01Icon} size={20} />
              <Text style={styles.quickActionText}>拍照</Text>
            </Pressable>
          </View>
        ) : null}
        {attachment === null ? null : (
          <View style={styles.attachmentPreview}>
            <AttachmentPreview
              attachment={attachment}
              onRemove={() => setAttachment(null)}
            />
          </View>
        )}
        {voice.error !== null && (
          <Pressable
            accessibilityLabel="关掉这条提示"
            accessibilityRole="button"
            onPress={voice.dismissError}
            style={styles.voiceError}
          >
            <Text style={styles.voiceErrorText}>{voice.error}</Text>
          </Pressable>
        )}
        <View style={styles.composer}>
          <Pressable
            accessibilityLabel="添加文件"
            accessibilityRole="button"
            disabled={busy}
            onPress={() => setShowReportPicker(true)}
            style={({ pressed }) => [
              styles.attachmentButton,
              pressed && styles.sendButtonPressed,
            ]}
          >
            <AppIcon color={colors.muted} icon={Attachment01Icon} size={22} />
          </Pressable>
          <TextInput
            ref={inputRef}
            editable={!busy && !voiceBusy}
            maxLength={2000}
            multiline
            onChangeText={setDraft}
            placeholder={
              voice.phase === 'recording'
                ? `正在录音 ${formatVoiceDuration(voice.durationMillis)}，说完再按一下`
                : voice.phase === 'transcribing'
                  ? '正在转成文字…'
                  : uploadingAttachment
                    ? '正在上传附件…'
                    : state.pendingRequestId !== null
                      ? '正在回复…'
                      : state.historyLoading
                        ? '正在读取对话…'
                        : '输入健康问题或说说近况…'
            }
            placeholderTextColor={colors.faint}
            style={styles.input}
            value={draft}
          />
          {showVoiceButton && (
            <Pressable
              accessibilityLabel={
                voice.phase === 'recording' ? '结束录音' : '用语音说'
              }
              accessibilityRole="button"
              disabled={voice.phase === 'transcribing'}
              onPress={() =>
                void (voice.phase === 'recording' ? voice.stop() : voice.start())
              }
              style={({ pressed }) => [
                styles.voiceButton,
                voice.phase === 'recording' && styles.voiceButtonActive,
                pressed && styles.sendButtonPressed,
              ]}
            >
              {voice.phase === 'transcribing' ? (
                <ActivityIndicator color={colors.muted} size="small" />
              ) : (
                <AppIcon
                  color={
                    voice.phase === 'recording' ? colors.white : colors.muted
                  }
                  icon={voice.phase === 'recording' ? StopIcon : Mic01Icon}
                  size={22}
                />
              )}
            </Pressable>
          )}
          <Pressable
            accessibilityLabel={
              state.pendingRequestId !== null ? '停止生成回复' : '发送消息'
            }
            accessibilityRole="button"
            disabled={
              state.pendingRequestId === null &&
              ((draft.trim().length === 0 && attachment === null) ||
                state.historyLoading ||
                uploadingAttachment)
            }
            onPress={
              state.pendingRequestId !== null
                ? cancelCurrentResponse
                : () => void sendContent(draft)
            }
            style={({ pressed }) => [
              styles.sendButton,
              state.pendingRequestId === null &&
              ((draft.trim().length === 0 && attachment === null) ||
                state.historyLoading ||
                uploadingAttachment) &&
              styles.sendButtonDisabled,
              pressed && styles.sendButtonPressed,
            ]}
          >
            <AppIcon
              color={colors.white}
              icon={state.pendingRequestId !== null ? StopIcon : ArrowUp01Icon}
              size={state.pendingRequestId !== null ? 18 : 22}
            />
          </Pressable>
        </View>
      </SafeAreaView>

      <Modal
        animationType="slide"
        onRequestClose={() => setShowCamera(false)}
        presentationStyle="fullScreen"
        visible={showCamera}
      >
        <CameraScreen
          onClose={() => setShowCamera(false)}
          onError={(message) => {
            setShowCamera(false);
            dispatch({ type: 'history-failed', message });
          }}
          onUsePhoto={(value) => {
            setAttachment(value);
            setShowCamera(false);
          }}
        />
      </Modal>

      <Modal
        animationType="slide"
        onRequestClose={() => setShowReportPicker(false)}
        presentationStyle="fullScreen"
        visible={showReportPicker}
      >
        <ReportPickerScreen
          onClose={() => setShowReportPicker(false)}
          onError={(message) => {
            setShowReportPicker(false);
            dispatch({ type: 'history-failed', message });
          }}
          onPicked={(value) => {
            setAttachment(value);
            setShowReportPicker(false);
          }}
        />
      </Modal>

      <Modal
        animationType="slide"
        onRequestClose={() => setShowSettings(false)}
        presentationStyle="fullScreen"
        visible={showSettings}
      >
        <SettingsScreen
          onClose={() => setShowSettings(false)}
          onOpenCare={() => {
            setShowSettings(false);
            setShowCareSettings(true);
          }}
          update={updateCheck}
        />
      </Modal>

      {updateCheck.release === null ? null : (
        <UpdatePrompt
          onDismiss={() => void updateCheck.dismiss()}
          release={updateCheck.release}
        />
      )}

      {showCareSettings ? (
        <CareSettingsModal
          api={api}
          backgroundHealthDisabling={healthConnect.backgroundDisabling}
          backgroundHealthEnabling={healthConnect.backgroundEnabling}
          backgroundHealthError={healthConnect.backgroundError}
          backgroundHealthStatus={healthConnect.backgroundStatus}
          notificationPermission={notifications.permission}
          notificationRegistrationFailed={notifications.registrationFailed}
          notificationRequesting={notifications.requestingPermission}
          onClose={() => setShowCareSettings(false)}
          onDisableBackgroundHealth={healthConnect.disableBackground}
          onEnableBackgroundHealth={healthConnect.enableBackground}
          onRequestNotificationPermission={notifications.requestPermission}
          visible
        />
      ) : null}

      <Modal
        animationType="slide"
        onRequestClose={() => setShowHealthOverview(false)}
        presentationStyle="fullScreen"
        visible={showHealthOverview}
      >
        <HealthOverviewScreen
          api={api}
          onClose={() => setShowHealthOverview(false)}
          onProfileChanged={() => void refreshHealthProfileCards()}
          onSyncWearable={healthConnect.sync}
          wearableSyncCompletedAt={healthConnect.lastSyncedAt}
        />
      </Modal>

      <Modal
        animationType="slide"
        onRequestClose={() => setShowGlucose(false)}
        presentationStyle="fullScreen"
        visible={showGlucose}
      >
        <View style={{ flex: 1, backgroundColor: '#F4F6F5' }}>
          <View
            style={{
              flexDirection: 'row',
              justifyContent: 'flex-end',
              paddingHorizontal: 20,
              paddingTop: 16,
            }}
          >
            <Pressable
              accessibilityLabel="关闭血糖记录"
              accessibilityRole="button"
              onPress={() => setShowGlucose(false)}
            >
              <Text style={{ fontSize: 15, color: '#0F766E' }}>关闭</Text>
            </Pressable>
          </View>
          <GlucoseScreen api={api} />
        </View>
      </Modal>

      <Modal
        animationType="slide"
        onRequestClose={() => setShowImportSensor(false)}
        presentationStyle="fullScreen"
        visible={showImportSensor}
      >
        <View style={{ flex: 1, backgroundColor: '#F4F6F5' }}>
          <GlucoseImportScreen
            api={api}
            onBack={() => setShowImportSensor(false)}
            onImported={(inserted) => {
              setShowImportSensor(false);
              setImportNotice(
                inserted > 0
                  ? `已导入 ${inserted} 条传感器数据`
                  : '这份文件里没有新增数据',
              );
            }}
          />
        </View>
      </Modal>

      {/* ===== 新增：健康看板 Modal ===== */}
      <HealthDashboardScreen
        api={api}
        visible={showDashboard}
        onClose={() => setShowDashboard(false)}
        refreshKey={`${healthConnect.lastSyncedAt ?? ''}:${gadgetbridge.lastSyncedAt ?? ''}`}
        importPreview={gadgetbridge.lastBundle}
      />

      {showPatientAccess ? <Modal visible animationType="slide" onRequestClose={() => setShowPatientAccess(false)}>
        <PatientAccessScreen api={api} onClose={() => setShowPatientAccess(false)} />
      </Modal> : null}

      <AppMenu
        conversations={conversationAnchors}
        activeConversationId={conversationId}
        onNewChat={() => void startNewConversation()}
        onClose={() => setShowMenu(false)}
        onOpenCare={() => setShowCareSettings(true)}
        onOpenProfile={() => {
          setShowMenu(false);
          onOpenProfile?.();
        }}
        onSelectConversation={(id) => {
          void openConversation(id);
        }}
        username={user.username}
        visible={showMenu}
      />

      <ExpoStatusBar style="dark" />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  headerSafeArea: {
    backgroundColor: colors.paper,
  },
  header: {
    minHeight: 60,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
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
  headerButtonPressed: { opacity: 0.65 },
  headerIdentity: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 20,
  },
  headerPlaceholder: { width: 44, height: 44 },
  importNotice: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: 9,
    backgroundColor: colors.primarySoft,
  },
  importNoticeText: {
    flex: 1,
    color: colors.text,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
    lineHeight: 16,
  },
  healthNoticeAction: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
  },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingVertical: 9,
    backgroundColor: colors.dangerSoft,
  },
  errorDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: colors.danger,
  },
  errorText: {
    flex: 1,
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 17,
  },
  dismissText: {
    color: colors.danger,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
  },
  newMessageBanner: {
    position: 'absolute',
    zIndex: 2,
    top: 72,
    alignSelf: 'center',
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.primaryPressed,
    boxShadow: '0 5px 18px rgba(24, 58, 49, 0.18)',
  },
  newMessageDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.white,
  },
  newMessageText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
  },
  messageList: {
    flexGrow: 1,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.xs,
    paddingBottom: spacing.lg,
  },
  olderButton: {
    alignSelf: 'center',
    marginBottom: spacing.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.pill,
    backgroundColor: colors.paper,
  },
  olderButtonPressed: { opacity: 0.64 },
  olderText: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
  },
  profileCards: { paddingTop: spacing.xs },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 430,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xxl,
  },
  emptyLoadingText: {
    paddingTop: spacing.sm,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  emptyTitle: {
    paddingTop: spacing.md,
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 24,
    textAlign: 'center',
  },
  emptyText: {
    maxWidth: 330,
    paddingTop: spacing.sm,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 15,
    lineHeight: 23,
    textAlign: 'center',
  },
  dateDivider: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.md,
  },
  dateRule: {
    flex: 1,
    height: StyleSheet.hairlineWidth,
    backgroundColor: colors.line,
  },
  dateText: {
    color: colors.faint,
    fontFamily: typefaces.sansMedium,
    fontSize: 10,
  },
  messageRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.xs,
    marginBottom: spacing.md,
  },
  userMessageRow: { justifyContent: 'flex-end' },
  assistantMessageRow: { justifyContent: 'flex-start' },
  messageContent: { maxWidth: '84%' },
  userContent: { alignItems: 'flex-end', gap: spacing.xs },
  targetMessage: {
    borderWidth: 2,
    borderColor: colors.primary,
    borderRadius: radii.lg,
  },
  assistantContent: { width: '100%', maxWidth: '100%', gap: spacing.xs },
  bubble: {
    minWidth: 82,
    paddingHorizontal: 15,
    paddingTop: 12,
    paddingBottom: 8,
    borderRadius: radii.lg,
    borderCurve: 'continuous',
  },
  userBubble: {
    borderBottomRightRadius: 8,
    backgroundColor: colors.primary,
  },
  assistantBubble: {
    paddingHorizontal: spacing.xs,
    paddingTop: spacing.xs,
    paddingBottom: spacing.xs,
    borderRadius: 0,
    backgroundColor: 'transparent',
  },
  // 第一句话还没出来时先占一个位置，别让患者对着空白以为没发出去。
  thinkingRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    minHeight: 30,
  },
  thinkingText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 15,
  },
  thinkingDots: {
    flexDirection: 'row',
    gap: 3,
    paddingTop: 4,
  },
  thinkingDot: {
    backgroundColor: colors.faint,
    borderRadius: radii.pill,
    height: 4,
    width: 4,
  },
  messageMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'flex-end',
    gap: spacing.xs,
    paddingTop: 5,
  },
  messageTime: {
    fontFamily: typefaces.sans,
    fontSize: 9,
    fontVariant: ['tabular-nums'],
  },
  assistantMessageTime: { color: colors.faint },
  medicalFootnote: {
    marginTop: spacing.xs,
    paddingTop: spacing.xs,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11.5,
    lineHeight: 17,
  },
  userMessageTime: { color: '#D9FFF4' },
  failedStatus: {
    color: colors.danger,
    fontFamily: typefaces.sansMedium,
    fontSize: 10,
  },
  cancelledStatus: {
    color: colors.coralPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 10,
  },
  retryButton: {
    alignSelf: 'flex-start',
    marginTop: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.primarySoft,
  },
  retryButtonPressed: { opacity: 0.65 },
  retryText: {
    color: colors.primary,
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
  },
  composerSafeArea: {
    backgroundColor: colors.background,
  },
  quickActions: {
    flexDirection: 'row',
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingTop: spacing.xs,
  },
  quickActionsHidden: { opacity: 0 },
  quickAction: {
    minHeight: 42,
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingHorizontal: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.primarySoft,
  },
  quickActionPressed: { opacity: 0.7, transform: [{ scale: 0.98 }] },
  quickActionText: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  attachmentPreview: {
    paddingHorizontal: spacing.sm,
    paddingTop: spacing.xs,
  },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: spacing.xs,
    marginHorizontal: spacing.sm,
    marginTop: spacing.xs,
    marginBottom: spacing.xs,
    padding: 5,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.xl,
    borderCurve: 'continuous',
    backgroundColor: colors.paper,
  },
  attachmentButton: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
  },
  input: {
    flex: 1,
    minHeight: 46,
    maxHeight: 120,
    paddingHorizontal: spacing.xs,
    paddingTop: 12,
    paddingBottom: 10,
    color: colors.text,
    fontFamily: typefaces.sans,
    fontSize: 16,
    lineHeight: 22,
    borderWidth: 0,
    backgroundColor: colors.paper,
  },
  voiceButton: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
  },
  voiceButtonActive: { backgroundColor: colors.danger },
  voiceError: {
    marginHorizontal: spacing.sm,
    marginTop: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.md,
    backgroundColor: colors.dangerSoft,
  },
  voiceErrorText: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 18,
  },
  sendButton: {
    width: 42,
    height: 42,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  sendButtonDisabled: { backgroundColor: colors.disabled },
  sendButtonPressed: { opacity: 0.7, transform: [{ scale: 0.96 }] },
});
