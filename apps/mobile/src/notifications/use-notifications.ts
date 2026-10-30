import * as Notifications from 'expo-notifications';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { errorMessage, type ApiClient } from '../api/client';
import {
  parseProactiveCareNotificationData,
  type ChatMessage,
  type ProactiveCareNotificationData,
} from '../api/types';
import {
  addPushTokenSyncListener,
  requestAndSync,
  syncCurrentNotificationState,
  type PushPermission,
} from './notifications';

type Options = {
  api: ApiClient;
  userId: string;
  /** 正在看的这一段对话：回到前台刷新时只拉它，别把别的会话塞进来。 */
  conversationId: string | null;
  historyReady: boolean;
  streamActive: boolean;
  onMessages: (messages: ChatMessage[], targetMessageId: string | null) => void;
  onError: (message: string) => void;
};

type PendingAction =
  | {
      type: 'window';
      data: ProactiveCareNotificationData;
      opened: boolean;
      clearLastResponse: boolean;
    }
  | { type: 'latest' };

export function useNotifications({
  api,
  userId,
  conversationId,
  historyReady,
  streamActive,
  onMessages,
  onError,
}: Options) {
  const [permission, setPermission] = useState<PushPermission | null>(null);
  const [registrationFailed, setRegistrationFailed] = useState(false);
  const [requestingPermission, setRequestingPermission] = useState(false);
  const automaticSync = useRef<Promise<PushPermission> | null>(null);
  const observedPermission = useRef<PushPermission | null>(null);
  const pending = useRef<PendingAction[]>([]);
  const handledResponses = useRef(new Set<string>());
  const processing = useRef(false);
  const mounted = useRef(true);
  const currentUserId = useRef(userId);
  const conversationIdRef = useRef(conversationId);
  const historyReadyRef = useRef(historyReady);
  const streamActiveRef = useRef(streamActive);
  const onMessagesRef = useRef(onMessages);
  const onErrorRef = useRef(onError);

  const processPending = useCallback(async () => {
    if (
      processing.current ||
      !historyReadyRef.current ||
      streamActiveRef.current
    ) {
      return;
    }
    processing.current = true;
    try {
      while (
        mounted.current &&
        currentUserId.current === userId &&
        historyReadyRef.current &&
        !streamActiveRef.current
      ) {
        const next = pending.current.shift();
        if (next === undefined) return;
        try {
          const page =
            next.type === 'latest'
              ? await api.getMessages(null, conversationIdRef.current)
              : await api.getMessageWindow(next.data.messageId);
          if (!mounted.current || currentUserId.current !== userId) return;
          if (!historyReadyRef.current || streamActiveRef.current) {
            pending.current.unshift(next);
            return;
          }
          onMessagesRef.current(
            page.messages,
            next.type === 'window' && next.opened ? next.data.messageId : null,
          );
          if (next.type === 'window' && next.opened) {
            await api.markPushDeliveryOpened(next.data.deliveryId);
          }
        } catch (error) {
          if (mounted.current && currentUserId.current === userId) {
            onErrorRef.current(errorMessage(error));
          }
        } finally {
          if (next.type === 'window' && next.clearLastResponse) {
            Notifications.clearLastNotificationResponse();
          }
        }
      }
    } finally {
      processing.current = false;
    }
  }, [api, userId]);

  const enqueue = useCallback(
    (
      notification: Notifications.Notification,
      opened: boolean,
      clearLastResponse = false,
    ) => {
      try {
        const data = parseProactiveCareNotificationData(
          notification.request.content.data,
        );
        if (opened && handledResponses.current.has(data.deliveryId)) {
          if (clearLastResponse) Notifications.clearLastNotificationResponse();
          return;
        }
        if (opened) handledResponses.current.add(data.deliveryId);
        const existing = pending.current.find(
          (item): item is Extract<PendingAction, { type: 'window' }> =>
            item.type === 'window' && item.data.deliveryId === data.deliveryId,
        );
        if (existing === undefined) {
          pending.current.push({
            type: 'window',
            data,
            opened,
            clearLastResponse,
          });
        } else {
          if (opened) existing.opened = true;
          if (clearLastResponse) existing.clearLastResponse = true;
        }
        void processPending();
      } catch (error) {
        onErrorRef.current(errorMessage(error));
        if (clearLastResponse) Notifications.clearLastNotificationResponse();
      }
    },
    [processPending],
  );

  const enqueueLatest = useCallback(() => {
    if (!pending.current.some((item) => item.type === 'latest')) {
      pending.current.push({ type: 'latest' });
    }
    void processPending();
  }, [processPending]);

  const recordPermission = useCallback(
    (status: PushPermission) => {
      if (mounted.current && currentUserId.current === userId) {
        observedPermission.current = status;
      }
    },
    [userId],
  );

  const syncAutomatically = useCallback(() => {
    if (automaticSync.current !== null) return automaticSync.current;
    observedPermission.current = null;
    const request = syncCurrentNotificationState(api, recordPermission);
    automaticSync.current = request;
    void request.then(
      (status) => {
        if (mounted.current && currentUserId.current === userId) {
          setPermission(status);
          setRegistrationFailed(false);
        }
        if (automaticSync.current === request) automaticSync.current = null;
      },
      (error) => {
        if (mounted.current && currentUserId.current === userId) {
          const status = observedPermission.current;
          if (status !== null) {
            setPermission(status);
            setRegistrationFailed(status === 'granted');
          }
          onErrorRef.current(errorMessage(error));
        }
        if (automaticSync.current === request) automaticSync.current = null;
      },
    );
    return request;
  }, [api, recordPermission, userId]);

  useEffect(() => {
    mounted.current = true;
    const responses = handledResponses.current;
    return () => {
      mounted.current = false;
      pending.current = [];
      responses.clear();
    };
  }, []);

  useEffect(() => {
    currentUserId.current = userId;
    conversationIdRef.current = conversationId;
    historyReadyRef.current = historyReady;
    streamActiveRef.current = streamActive;
    onMessagesRef.current = onMessages;
    onErrorRef.current = onError;
    void processPending();
  }, [
    conversationId,
    historyReady,
    onError,
    onMessages,
    processPending,
    streamActive,
    userId,
  ]);

  useEffect(() => {
    void syncAutomatically();
    const received = Notifications.addNotificationReceivedListener(
      (notification) => enqueue(notification, false),
    );
    const responded = Notifications.addNotificationResponseReceivedListener(
      (response) => enqueue(response.notification, true),
    );
    const lastResponse = Notifications.getLastNotificationResponse();
    if (lastResponse !== null) {
      enqueue(lastResponse.notification, true, true);
    }
    const appState = AppState.addEventListener('change', (nextState) => {
      if (nextState !== 'active') return;
      enqueueLatest();
      void syncAutomatically();
    });
    return () => {
      automaticSync.current = null;
      received.remove();
      responded.remove();
      appState.remove();
    };
  }, [api, enqueue, enqueueLatest, syncAutomatically, userId]);

  useEffect(() => {
    if (permission !== 'granted') return;
    const token = addPushTokenSyncListener(
      api,
      () => {
        if (mounted.current && currentUserId.current === userId) {
          setRegistrationFailed(false);
        }
      },
      (error) => {
        if (mounted.current && currentUserId.current === userId) {
          setRegistrationFailed(true);
          onErrorRef.current(errorMessage(error));
        }
      },
    );
    return () => token.remove();
  }, [api, permission, userId]);

  const requestPermission = useCallback(async () => {
    setRequestingPermission(true);
    try {
      const current = automaticSync.current;
      let status = current === null ? null : await current;
      if (status === null || status === 'undetermined') {
        observedPermission.current = null;
        status = await requestAndSync(api, recordPermission);
      }
      if (mounted.current && currentUserId.current === userId) {
        setPermission(status);
        setRegistrationFailed(false);
      }
      return status;
    } catch (error) {
      if (mounted.current && currentUserId.current === userId) {
        const status = observedPermission.current;
        if (status !== null) {
          setPermission(status);
          setRegistrationFailed(status === 'granted');
        }
      }
      throw error;
    } finally {
      if (mounted.current && currentUserId.current === userId) {
        setRequestingPermission(false);
      }
    }
  }, [api, recordPermission, userId]);

  return {
    permission,
    registrationFailed,
    requestPermission,
    requestingPermission,
  };
}
