import type { ChatMessage } from '../api/types';
import { chatReducer, initialChatState } from './chat-state';

const clientId = '11111111-1111-4111-8111-111111111111';
const userId = '019b1111-1111-7111-8111-111111111111';
const assistantId = '019b2222-2222-7222-8222-222222222222';
const runId = '019b3333-3333-7333-8333-333333333333';
const retryRequestId = '22222222-2222-4222-8222-222222222222';
const retryAssistantId = '019b4444-4444-7444-8444-444444444444';

function startedState() {
  return chatReducer(initialChatState, {
    type: 'send-started',
    clientMessageId: clientId,
    content: '我有点担心低血糖',
    attachment: null,
    now: '2026-07-13T10:00:00.000Z',
  });
}

describe('chatReducer', () => {
  test('starts with history loading until the first server page arrives', () => {
    expect(initialChatState.historyLoading).toBe(true);
  });

  test('builds an optimistic pair and appends streamed text', () => {
    let state = startedState();
    expect(state.sending).toBe(true);
    expect(state.activity).toBe('thinking');
    expect(state.messages.map((message) => message.role)).toEqual(['user', 'assistant']);

    state = chatReducer(state, {
      type: 'stream-event',
      event: {
        type: 'message_started',
        userMessageId: userId,
        assistantMessageId: assistantId,
        runId,
      },
    });
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'text_delta', delta: '我在这里。' },
    });
    expect(state.activity).toBeNull();
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'agent_activity', phase: 'organizing' },
    });
    expect(state.activity).toBe('organizing');
    state = chatReducer(state, {
      type: 'stream-event',
      event: {
        type: 'message_completed',
        inputTokens: 20,
        outputTokens: 5,
        sources: [
          {
            sourceId: 'S1',
            title: '低血糖资料',
            url: 'https://example.com/hypoglycemia',
          },
        ],
      },
    });

    expect(state.messages[0].id).toBe(userId);
    expect(state.messages[1]).toMatchObject({
      id: assistantId,
      content: '我在这里。',
      status: 'completed',
      sources: [
        {
          sourceId: 'S1',
          title: '低血糖资料',
          url: 'https://example.com/hypoglycemia',
        },
      ],
    });
    expect(state.sending).toBe(false);
    expect(state.pendingRequestId).toBeNull();
    expect(state.activity).toBeNull();
  });

  test('keeps partial text when the agent reports failure', () => {
    let state = startedState();
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'text_delta', delta: '已经生成的部分' },
    });
    state = chatReducer(state, {
      type: 'stream-event',
      event: {
        type: 'message_failed',
        code: 'agent_run_failed',
        errorType: 'ModelError',
      },
    });

    expect(state.messages[1]).toMatchObject({
      content: '已经生成的部分',
      status: 'failed',
    });
    expect(state.error).toBe('回复失败了，再试一次。');
    expect(state.activity).toBeNull();
  });

  test('tells the patient what to do instead of naming the error type', () => {
    const failed = (errorType: string) =>
      chatReducer(
        chatReducer(startedState(), {
          type: 'stream-event',
          event: { type: 'text_delta', delta: '在' },
        }),
        {
          type: 'stream-event',
          event: { type: 'message_failed', code: 'agent_run_failed', errorType },
        },
      ).error;

    expect(failed('PermissionDeniedError')).toBe('服务暂时不可用，稍后再试。');
    expect(failed('ConnectionError')).toBe('网络连不上，检查一下网络再试。');
    expect(failed('SomeNewError')).toBe('回复失败了，再试一次。');
  });

  test('new optimistic messages start without sources', () => {
    const state = startedState();

    expect(state.messages.map((message) => message.sources)).toEqual([[], []]);
  });

  test('merges server messages by id without disturbing an active stream', () => {
    const firstMessage: ChatMessage = {
      id: userId,
      clientMessageId: null,
      role: 'user',
      status: 'completed',
      content: '旧内容',
      proactiveReason: null,
      sources: [],
      attachments: [],
      createdAt: '2026-07-13T09:00:00.000Z',
      completedAt: '2026-07-13T09:00:00.000Z',
    };
    const proactiveMessage: ChatMessage = {
      id: assistantId,
      clientMessageId: null,
      role: 'assistant',
      status: 'completed',
      content: '今天感觉怎么样？',
      proactiveReason: null,
      sources: [],
      attachments: [],
      createdAt: '2026-07-13T09:30:00.000Z',
      completedAt: '2026-07-13T09:30:01.000Z',
    };
    let state = chatReducer(initialChatState, {
      type: 'history-replaced',
      messages: [firstMessage],
      nextBefore: null,
    });
    state = chatReducer(state, {
      type: 'send-started',
      clientMessageId: clientId,
      content: '好多了',
      attachment: null,
      now: '2026-07-13T10:00:00.000Z',
    });
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'text_delta', delta: '真好，' },
    });

    state = chatReducer(state, {
      type: 'history-merged',
      messages: [
        { ...firstMessage, content: '服务器正式内容' },
        proactiveMessage,
        proactiveMessage,
      ],
    });

    expect(state.messages.map((message) => message.id)).toEqual([
      userId,
      assistantId,
      `local-user-${clientId}`,
      `local-assistant-${clientId}`,
    ]);
    expect(state.messages[0].content).toBe('服务器正式内容');
    expect(state.messages[3].content).toBe('真好，');
    expect(state.sending).toBe(true);
    expect(state.pendingRequestId).toBe(clientId);
    expect(state.activity).toBeNull();
  });

  test('a retry replaces the failed assistant response after the server starts', () => {
    const originalMessages: ChatMessage[] = [
      {
        id: userId,
        clientMessageId: clientId,
        role: 'user',
        status: 'completed',
        content: '我有点担心低血糖',
        proactiveReason: null,
        sources: [],
      attachments: [],
        createdAt: '2026-07-13T10:00:00.000Z',
        completedAt: null,
      },
      {
        id: assistantId,
        clientMessageId: null,
        role: 'assistant',
        status: 'failed',
        content: '生成到一半',
        proactiveReason: null,
        sources: [],
      attachments: [],
        createdAt: '2026-07-13T10:00:01.000Z',
        completedAt: '2026-07-13T10:00:02.000Z',
      },
    ];
    let state = chatReducer(initialChatState, {
      type: 'history-replaced',
      messages: originalMessages,
      nextBefore: null,
    });

    state = chatReducer(state, {
      type: 'retry-started',
      requestId: retryRequestId,
      failedAssistantMessageId: assistantId,
      now: '2026-07-13T10:01:00.000Z',
    });

    expect(state.messages.map((message) => message.role)).toEqual([
      'user',
      'assistant',
      'assistant',
    ]);
    expect(state.messages[1]).toEqual(originalMessages[1]);
    expect(state.messages[2]).toMatchObject({
      id: `local-assistant-${retryRequestId}`,
      status: 'generating',
      content: '',
    });

    state = chatReducer(state, {
      type: 'stream-event',
      event: {
        type: 'message_started',
        userMessageId: userId,
        assistantMessageId: retryAssistantId,
        runId,
      },
    });
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'text_delta', delta: '这次回复完成了。' },
    });

    expect(state.messages[0].id).toBe(userId);
    expect(state.messages).toHaveLength(2);
    expect(state.messages[1]).toMatchObject({
      id: retryAssistantId,
      content: '这次回复完成了。',
      status: 'generating',
    });
  });

  test('a rejected retry removes only its new assistant placeholder', () => {
    const failedAssistant: ChatMessage = {
      id: assistantId,
      clientMessageId: null,
      role: 'assistant',
      status: 'failed',
      content: '生成失败',
      proactiveReason: null,
      sources: [],
      attachments: [],
      createdAt: '2026-07-13T10:00:00.000Z',
      completedAt: '2026-07-13T10:00:01.000Z',
    };
    let state = chatReducer(initialChatState, {
      type: 'history-replaced',
      messages: [failedAssistant],
      nextBefore: null,
    });
    state = chatReducer(state, {
      type: 'retry-started',
      requestId: retryRequestId,
      failedAssistantMessageId: assistantId,
      now: '2026-07-13T10:01:00.000Z',
    });

    state = chatReducer(state, {
      type: 'request-rejected',
      requestId: retryRequestId,
      message: '现在不能重试',
    });

    expect(state.messages).toEqual([failedAssistant]);
    expect(state.error).toBe('现在不能重试');
    expect(state.sending).toBe(false);
  });

  test.each([
    { type: 'request-cancelled' as const },
    { type: 'transport-failed' as const, message: '网络中断' },
  ])('a retry can start again when $type happens before the server responds', (action) => {
    const failedAssistant: ChatMessage = {
      id: assistantId,
      clientMessageId: null,
      role: 'assistant',
      status: 'failed',
      content: '生成失败',
      proactiveReason: null,
      sources: [],
      attachments: [],
      createdAt: '2026-07-13T10:00:00.000Z',
      completedAt: '2026-07-13T10:00:01.000Z',
    };
    let state = chatReducer(initialChatState, {
      type: 'history-replaced',
      messages: [failedAssistant],
      nextBefore: null,
    });
    state = chatReducer(state, {
      type: 'retry-started',
      requestId: retryRequestId,
      failedAssistantMessageId: assistantId,
      now: '2026-07-13T10:01:00.000Z',
    });

    state = chatReducer(state, action);

    expect(state.messages).toEqual([failedAssistant]);
    expect(state.sending).toBe(false);
  });

  test('history with a generating assistant disables sending', () => {
    const generating: ChatMessage = {
      id: assistantId,
      clientMessageId: null,
      role: 'assistant',
      status: 'generating',
      content: '部分内容',
      proactiveReason: null,
      sources: [],
      attachments: [],
      createdAt: '2026-07-13T10:00:00.000Z',
      completedAt: null,
    };

    const state = chatReducer(initialChatState, {
      type: 'history-replaced',
      messages: [generating],
      nextBefore: null,
    });

    expect(state.sending).toBe(true);
  });

  test('a rejected request removes only its optimistic messages', () => {
    const state = chatReducer(startedState(), {
      type: 'request-rejected',
      requestId: clientId,
      message: '请求冲突',
    });

    expect(state.messages).toEqual([]);
    expect(state.error).toBe('请求冲突');
    expect(state.sending).toBe(false);
    expect(state.activity).toBeNull();
  });

  test.each([
    { action: { type: 'request-cancelled' as const }, error: '本次回复已取消。' },
    {
      action: { type: 'transport-failed' as const, message: '网络中断' },
      error: '网络中断',
    },
  ])('clears activity after $action.type', ({ action, error }) => {
    let state = startedState();
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'agent_activity', phase: 'reading' },
    });

    state = chatReducer(state, action);

    expect(state.activity).toBeNull();
    expect(state.sending).toBe(false);
    expect(state.error).toBe(error);
  });

  test('restarts an unacknowledged send with the same optimistic messages', () => {
    let state = chatReducer(startedState(), {
      type: 'transport-failed',
      message: '网络中断',
    });

    state = chatReducer(state, {
      type: 'send-started',
      clientMessageId: clientId,
      content: '我有点担心低血糖',
      attachment: null,
      now: '2026-07-13T10:01:00.000Z',
    });

    expect(state.messages).toHaveLength(2);
    expect(state.messages.map((message) => message.id)).toEqual([
      `local-user-${clientId}`,
      `local-assistant-${clientId}`,
    ]);
    expect(state.messages[1].status).toBe('generating');
    expect(state.pendingRequestId).toBe(clientId);
  });
});
