import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import * as SecureStore from 'expo-secure-store';

import { checkForUpdate, type ReleaseInfo } from './release';

const LAST_CHECK_KEY = 'antang.update.lastCheckDay';
const DISMISSED_KEY = 'antang.update.dismissedCode';
const AUTO_CHECK_KEY = 'antang.update.autoCheck';

export type UpdateCheckState = {
  /** 有新版本且用户还没说「今天先不更新」时为这份清单，否则 null。 */
  release: ReleaseInfo | null;
  checking: boolean;
  error: string | null;
  /** 最近一次手动检查的结论，用来在设置页上写「已是最新版本」。 */
  lastResult: 'available' | 'up-to-date' | null;
  autoCheck: boolean;
  check: (options?: { manual?: boolean }) => Promise<void>;
  dismiss: () => Promise<void>;
  setAutoCheck: (enabled: boolean) => Promise<void>;
};

function todayStamp(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}

/**
 * 检查更新。
 *
 * 自动检查正常只在「打开 App 或回到前台且今天还没查过」时跑一次；手动检查
 * 忽略节流。节流时间和「今天先不更新」都存本地：读不到只会多查一次或再问
 * 一次，不影响正确性，所以这里的 catch 不是吞错，是让本地缓存失效时继续走。
 */
export function useUpdateCheck(): UpdateCheckState {
  const [release, setRelease] = useState<ReleaseInfo | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<'available' | 'up-to-date' | null>(null);
  const [autoCheck, setAutoCheckState] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);

  const readFlag = useCallback(async (key: string): Promise<string | null> => {
    try {
      return await SecureStore.getItemAsync(key);
    } catch {
      return null;
    }
  }, []);

  const check = useCallback(
    async (options: { manual?: boolean } = {}): Promise<void> => {
      if (inFlight.current) return;
      const manual = options.manual === true;
      if (!manual) {
        const last = await readFlag(LAST_CHECK_KEY);
        if (last === todayStamp()) return;
      }
      inFlight.current = true;
      if (mounted.current) {
        setChecking(true);
        setError(null);
      }
      try {
        const result = await checkForUpdate();
        if (result.kind === 'up-to-date') {
          if (mounted.current) {
            setRelease(null);
            setLastResult('up-to-date');
          }
          return;
        }
        const dismissed = await readFlag(DISMISSED_KEY);
        const { release: available } = result;
        if (mounted.current) {
          setLastResult('available');
          setRelease(
            dismissed === String(available.versionCode) ? null : available,
          );
        }
      } catch (cause) {
        if (mounted.current) {
          setError(cause instanceof Error ? cause.message : '检查更新失败');
        }
      } finally {
        inFlight.current = false;
        try {
          await SecureStore.setItemAsync(LAST_CHECK_KEY, todayStamp());
        } catch {
          // 记不住时间只会导致下次启动多查一次。
        }
        if (mounted.current) setChecking(false);
      }
    },
    [readFlag],
  );

  const dismiss = useCallback(async (): Promise<void> => {
    const current = release;
    setRelease(null);
    if (current === null) return;
    try {
      await SecureStore.setItemAsync(DISMISSED_KEY, String(current.versionCode));
    } catch {
      // 记不住「先不更新」只会让它下次启动再问一次。
    }
  }, [release]);

  const setAutoCheck = useCallback(async (enabled: boolean): Promise<void> => {
    setAutoCheckState(enabled);
    try {
      await SecureStore.setItemAsync(AUTO_CHECK_KEY, enabled ? '1' : '0');
    } catch {
      // 开关状态只在本次运行内生效。
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      const stored = await readFlag(AUTO_CHECK_KEY);
      if (mounted.current && stored === '0') setAutoCheckState(false);
      if (mounted.current) setLoaded(true);
    })();
    return () => {
      mounted.current = false;
    };
  }, [readFlag]);

  useEffect(() => {
    // 等本地的开关状态读回来再决定要不要自动查，否则用户关掉了开关也会在
    // 启动那一刻先查一次。用定时器把发起检查挪出 effect 体，避免在 effect
    // 里同步改状态引发级联渲染。
    if (!loaded || !autoCheck) return;
    const timer = setTimeout(() => void check(), 0);
    const subscription = AppState.addEventListener('change', (next) => {
      if (next === 'active') void check();
    });
    return () => {
      clearTimeout(timer);
      subscription.remove();
    };
  }, [autoCheck, check, loaded]);

  return { release, checking, error, lastResult, autoCheck, check, dismiss, setAutoCheck };
}
