import type {
  AgentActivityPhase,
  ChatAttachment,
  ChatMessage,
  ChatStreamEvent,
} from '../api/types';

export type ChatState = {
  messages: ChatMessage[];
  nextBefore: string | null;
  historyLoading: boolean;
  sending: boolean;
  activity: AgentActivityPhase | null;
  error: string | null;
  pendingRequestId: string | null;
  retrySourceMessageId: string | null;
};

export const initialChatState: ChatState = {
  messages: [],
  nextBefore: null,
  historyLoading: true,
  sending: false,
  activity: null,
  error: null,
  pendingRequestId: null,
  retrySourceMessageId: null,
};

/**
 * 患者看到的失败文案。错误类型是给我们看的，不该原样出现在手机屏幕上；
 * 按「患者下一步能做什么」分组：换个时间再试、重发一次、检查网络。
 */
const FAILURE_MESSAGES: Record<string, string> = {
  AuthenticationError: '服务暂时不可用，稍后再试。',
  PermissionDeniedError: '服务暂时不可用，稍后再试。',
  RateLimitError: '现在有点忙，等一下再发一次。',
  APITimeoutError: '等太久了，再发一次试试。',
  TimeoutError: '等太久了，再发一次试试。',
  ConnectionError: '网络连不上，检查一下网络再试。',
  RemoteProtocolError: '连接断了，再发一次试试。',
};

export function failureMessage(errorType: string): string {
  return FAILURE_MESSAGES[errorType] ?? '回复失败了，再试一次。';
}

type ChatAction =
  | { type: 'history-loading' }
  | { type: 'history-replaced'; messages: ChatMessage[]; nextBefore: string | null }
  | { type: 'history-prepended'; messages: ChatMessage[]; nextBefore: string | null }
  | { type: 'history-merged'; messages: ChatMessage[] }
  | { type: 'history-failed'; message: string }
  | {
      type: 'send-started';
      clientMessageId: string;
      content: string;
      attachment: ChatAttachment | null;
      now: string;
    }
  | {
      type: 'retry-started';
      requestId: string;
      failedAssistantMessageId: string;
      now: string;
    }
  | { type: 'stream-event'; event: ChatStreamEvent }
  | { type: 'request-rejected'; requestId: string; message: string | null }
  | { type: 'request-cancelled' }
  | { type: 'transport-failed'; message: string }
  | { type: 'clear-error' };

function hasGeneratingAssistant(messages: ChatMessage[]): boolean {
  return messages.some(
    (message) => message.role === 'assistant' && message.status === 'generating',
  );
}

function mergeMessages(
  current: ChatMessage[],
  incoming: ChatMessage[],
): ChatMessage[] {
  const messages = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) messages.set(message.id, message);
  return [...messages.values()].sort((left, right) =>
    left.createdAt.localeCompare(right.createdAt),
  );
}

function updatePendingAssistant(
  state: ChatState,
  update: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const requestId = state.pendingRequestId;
  if (requestId === null) throw new Error('收到流事件时没有待处理消息');

  const localAssistantId = `local-assistant-${requestId}`;
  let found = false;
  const messages = state.messages.map((message) => {
    if (message.id !== localAssistantId && message.status !== 'generating') return message;
    if (message.role !== 'assistant') return message;
    found = true;
    return update(message);
  });
  if (!found) throw new Error('找不到正在生成的 AI 消息');
  return messages;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'history-loading':
      return { ...state, historyLoading: true, error: null };

    case 'history-replaced':
      return {
        ...state,
        messages: action.messages,
        nextBefore: action.nextBefore,
        historyLoading: false,
        sending: hasGeneratingAssistant(action.messages),
        activity: null,
        pendingRequestId: null,
        retrySourceMessageId: null,
        error: null,
      };

    case 'history-prepended': {
      const knownIds = new Set(state.messages.map((message) => message.id));
      const older = action.messages.filter((message) => !knownIds.has(message.id));
      return {
        ...state,
        messages: [...older, ...state.messages],
        nextBefore: action.nextBefore,
        historyLoading: false,
        error: null,
      };
    }

    case 'history-merged':
      return {
        ...state,
        messages: mergeMessages(state.messages, action.messages),
      };

    case 'history-failed':
      return { ...state, historyLoading: false, error: action.message };

    case 'send-started': {
      if (state.sending) throw new Error('已有消息正在发送');
      const userMessage: ChatMessage = {
        id: `local-user-${action.clientMessageId}`,
        clientMessageId: action.clientMessageId,
        role: 'user',
        status: 'completed',
        content: action.content,
        proactiveReason: null,
        sources: [],
        attachments: action.attachment === null ? [] : [action.attachment],
        createdAt: action.now,
        completedAt: null,
      };
      const assistantMessage: ChatMessage = {
        id: `local-assistant-${action.clientMessageId}`,
        clientMessageId: null,
        role: 'assistant',
        status: 'generating',
        content: '',
        proactiveReason: null,
        sources: [],
        attachments: [],
        createdAt: action.now,
        completedAt: null,
      };
      const previousAttemptIds = new Set([userMessage.id, assistantMessage.id]);
      return {
        ...state,
        messages: [
          ...state.messages.filter((message) => !previousAttemptIds.has(message.id)),
          userMessage,
          assistantMessage,
        ],
        sending: true,
        activity: 'thinking',
        pendingRequestId: action.clientMessageId,
        retrySourceMessageId: null,
        error: null,
      };
    }

    case 'retry-started': {
      if (state.sending) throw new Error('已有消息正在发送');
      const assistantMessage: ChatMessage = {
        id: `local-assistant-${action.requestId}`,
        clientMessageId: null,
        role: 'assistant',
        status: 'generating',
        content: '',
        proactiveReason: null,
        sources: [],
        attachments: [],
        createdAt: action.now,
        completedAt: null,
      };
      return {
        ...state,
        messages: [...state.messages, assistantMessage],
        sending: true,
        activity: 'thinking',
        pendingRequestId: action.requestId,
        retrySourceMessageId: action.failedAssistantMessageId,
        error: null,
      };
    }

    case 'stream-event': {
      const event = action.event;

      if (event.type === 'message_started') {
        const requestId = state.pendingRequestId;
        if (requestId === null) throw new Error('message_started 没有对应的本地消息');
        return {
          ...state,
          messages: state.messages
            .filter((message) => message.id !== state.retrySourceMessageId)
            .map((message) => {
              if (message.id === `local-user-${requestId}`) {
                return { ...message, id: event.userMessageId };
              }
              if (message.id === `local-assistant-${requestId}`) {
                return { ...message, id: event.assistantMessageId };
              }
              return message;
            }),
          retrySourceMessageId: null,
        };
      }

      if (event.type === 'text_delta') {
        return {
          ...state,
          activity: null,
          messages: updatePendingAssistant(state, (message) => ({
            ...message,
            content: message.content + event.delta,
          })),
        };
      }

      if (event.type === 'agent_activity') {
        return { ...state, activity: event.phase };
      }

      if (event.type === 'message_completed') {
        return {
          ...state,
          messages: updatePendingAssistant(state, (message) => ({
            ...message,
            status: 'completed',
            sources: event.sources,
            completedAt: new Date().toISOString(),
          })),
          sending: false,
          activity: null,
          pendingRequestId: null,
          retrySourceMessageId: null,
        };
      }

      return {
        ...state,
        messages: updatePendingAssistant(state, (message) => ({
          ...message,
          status: 'failed',
          completedAt: new Date().toISOString(),
        })),
        sending: false,
        activity: null,
        pendingRequestId: null,
        error: failureMessage(event.errorType),
      };
    }

    case 'request-rejected':
      return {
        ...state,
        messages: state.messages.filter(
          (message) =>
            message.id !== `local-user-${action.requestId}` &&
            message.id !== `local-assistant-${action.requestId}`,
        ),
        sending: false,
        activity: null,
        pendingRequestId: null,
        retrySourceMessageId: null,
        error: action.message,
      };

    case 'transport-failed':
    case 'request-cancelled': {
      const requestId = state.pendingRequestId;
      if (requestId === null) throw new Error('结束请求时没有待处理消息');
      const localAssistantId = `local-assistant-${requestId}`;
      const unstartedRetry =
        state.messages.some((message) => message.id === localAssistantId) &&
        !state.messages.some((message) => message.id === `local-user-${requestId}`);
      const error =
        action.type === 'transport-failed' ? action.message : '本次回复已取消。';

      return {
        ...state,
        messages: unstartedRetry
          ? state.messages.filter((message) => message.id !== localAssistantId)
          : updatePendingAssistant(state, (message) => ({
              ...message,
              status: action.type === 'transport-failed' ? 'failed' : 'cancelled',
              completedAt: new Date().toISOString(),
            })),
        sending: false,
        activity: null,
        pendingRequestId: null,
        retrySourceMessageId: null,
        error,
      };
    }

    case 'clear-error':
      return { ...state, error: null };
  }
}
