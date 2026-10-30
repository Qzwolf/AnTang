import { act, create } from 'react-test-renderer';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { ApiClient } from '../api/client';
import type { ProactiveCareSettings } from '../api/types';
import { CareSettingsModal } from './care-settings-modal';

// React Native exposes these components through lazy getters. Resolve them while
// the test module loads so Jest's timeout measures modal behavior, not one-time
// transformation of React Native internals on a clean cache.
void [ActivityIndicator, Modal, Pressable, ScrollView, Switch, Text, TextInput, View];

const settings: ProactiveCareSettings = {
  routineCadence: 'disabled',
  planFollowUpEnabled: true,
  healthEventsEnabled: false,
  timezone: 'Asia/Shanghai',
  quietHoursStart: '22:00',
  quietHoursEnd: '08:00',
  healthNotificationPreviewEnabled: false,
  weeklyContactCount: 3,
  weeklyContactLimit: 4,
  autoPaused: false,
  autoDowngradedAt: null,
};

describe('CareSettingsModal', () => {
  test('loads settings without requesting notification permission', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest.fn(),
    } as unknown as ApiClient;
    const requestNotificationPermission = jest.fn();
    const enableBackgroundHealth = jest.fn();
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthEnabling={false}
          backgroundHealthDisabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="disabled"
          notificationPermission="undetermined"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onEnableBackgroundHealth={enableBackgroundHealth}
          onDisableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={requestNotificationPermission}
          visible
        />,
      );
    });

    expect(api.getProactiveCareSettings).toHaveBeenCalledTimes(1);
    expect(requestNotificationPermission).not.toHaveBeenCalled();
    expect(enableBackgroundHealth).not.toHaveBeenCalled();
    expect(tree!.root.findByProps({ children: '每三天（推荐）' })).toBeTruthy();
    expect(
      tree!.root.findByProps({
        children:
          '只在聊天中明确约定的时间复盘你同意的计划，不会变成通用闹钟或服药提醒。',
      }),
    ).toBeTruthy();
    expect(
      tree!.root.findByProps({
        children:
          '同步到近期、持续偏快或偏慢，且不属于运动或睡眠的心率记录后，安糖会在合适时间发起关怀。这不是实时监护，也不能代替医疗判断。',
      }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '尚未选择' })).toBeTruthy();
    expect(
      tree!.root.findByProps({ children: '后台检查尚未开启' }),
    ).toBeTruthy();
    expect(
      tree!.root.findByProps({ accessibilityLabel: '日常关怀频率：关闭' })
        .props.accessibilityState,
    ).toEqual({ selected: true });
    act(() => tree!.unmount());
  });

  test('shows how often it may contact you and how many were used', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest.fn(),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthEnabling={false}
          backgroundHealthDisabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="disabled"
          notificationPermission="undetermined"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onEnableBackgroundHealth={jest.fn()}
          onDisableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={jest.fn()}
          visible
        />,
      );
    });

    const text = tree!.root
      .findAllByType(Text)
      .map((node) => node.props.children)
      .flat(Infinity)
      .join('');
    expect(text).toContain('本周已主动联系 3 次');
    expect(text).toContain('上限 4 次');
    expect(
      tree!.root.findAllByProps({ accessibilityLabel: '继续接收日常问候' }),
    ).toHaveLength(0);
    act(() => tree!.unmount());
  });

  test('lets a user who stopped answering resume the routine', async () => {
    const paused = {
      ...settings,
      routineCadence: 'disabled' as const,
      autoPaused: true,
      autoDowngradedAt: '2026-10-20T01:00:00Z',
    };
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(paused),
      putProactiveCareSettings: jest.fn().mockResolvedValue({
        ...settings,
        routineCadence: 'daily',
      }),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthEnabling={false}
          backgroundHealthDisabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="disabled"
          notificationPermission="undetermined"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onEnableBackgroundHealth={jest.fn()}
          onDisableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={jest.fn()}
          visible
        />,
      );
    });
    expect(
      tree!.root.findByProps({ accessibilityLabel: '继续接收日常问候' }),
    ).toBeTruthy();

    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '继续接收日常问候' })
        .props.onPress();
    });

    expect(api.putProactiveCareSettings).toHaveBeenCalledWith(
      expect.objectContaining({ routineCadence: 'daily' }),
    );
    act(() => tree!.unmount());
  });

  test('distinguishes an enabled system permission from failed push registration', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest.fn(),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthDisabling={false}
          backgroundHealthEnabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="disabled"
          notificationPermission="granted"
          notificationRegistrationFailed
          notificationRequesting={false}
          onClose={jest.fn()}
          onDisableBackgroundHealth={jest.fn()}
          onEnableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={jest.fn()}
          visible
        />,
      );
    });

    expect(
      tree!.root.findByProps({
        children: '系统通知已开启，但推送暂未连接',
      }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '重新连接推送' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('requests permission only from its button and saves a complete document', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest
        .fn()
        .mockImplementation((value: ProactiveCareSettings) =>
          Promise.resolve(value),
        ),
    } as unknown as ApiClient;
    const requestNotificationPermission = jest.fn().mockResolvedValue(undefined);
    const enableBackgroundHealth = jest.fn().mockResolvedValue(undefined);
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthEnabling={false}
          backgroundHealthDisabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="disabled"
          notificationPermission="undetermined"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onEnableBackgroundHealth={enableBackgroundHealth}
          onDisableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={requestNotificationPermission}
          visible
        />,
      );
    });

    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '开启后台周期检查' })
        .props.onPress();
      tree!.root
        .findByProps({ accessibilityLabel: '申请系统通知权限' })
        .props.onPress();
    });
    expect(enableBackgroundHealth).toHaveBeenCalledTimes(1);
    expect(requestNotificationPermission).toHaveBeenCalledTimes(1);

    act(() => {
      tree!.root
        .findByProps({ accessibilityLabel: '日常关怀频率：每三天（推荐）' })
        .props.onPress();
      tree!.root
        .findAllByType(Switch)
        .find((node) => node.props.accessibilityLabel === '心率关怀')!
        .props.onValueChange(true);
      tree!.root
        .findAllByType(Switch)
        .find((node) => node.props.accessibilityLabel === '健康消息显示完整正文')!
        .props.onValueChange(true);
      tree!.root
        .findAllByType(TextInput)
        .find((node) => node.props.accessibilityLabel === '免打扰开始时间')!
        .props.onChangeText('21:30');
      tree!.root
        .findAllByType(TextInput)
        .find((node) => node.props.accessibilityLabel === '免打扰结束时间')!
        .props.onChangeText('07:15');
    });
    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '保存关怀设置' })
        .props.onPress();
    });

    expect(api.putProactiveCareSettings).toHaveBeenCalledWith({
      routineCadence: 'every_3_days',
      planFollowUpEnabled: true,
      healthEventsEnabled: true,
      timezone,
      quietHoursStart: '21:30',
      quietHoursEnd: '07:15',
      healthNotificationPreviewEnabled: true,
    });
    act(() => tree!.unmount());
  });

  test('lets the user explicitly turn off an enabled background check', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest.fn(),
    } as unknown as ApiClient;
    const disableBackgroundHealth = jest.fn().mockResolvedValue(undefined);
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthDisabling={false}
          backgroundHealthEnabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="enabled"
          notificationPermission="granted"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onDisableBackgroundHealth={disableBackgroundHealth}
          onEnableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={jest.fn()}
          visible
        />,
      );
    });

    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '关闭后台周期检查' })
        .props.onPress();
    });

    expect(disableBackgroundHealth).toHaveBeenCalledTimes(1);
    act(() => tree!.unmount());
  });

  test('still lets the user turn off a registered check after permission is revoked', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest.fn(),
    } as unknown as ApiClient;
    const disableBackgroundHealth = jest.fn().mockResolvedValue(undefined);
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthDisabling={false}
          backgroundHealthEnabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="permission_missing"
          notificationPermission="granted"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onDisableBackgroundHealth={disableBackgroundHealth}
          onEnableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={jest.fn()}
          visible
        />,
      );
    });

    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '关闭后台周期检查' })
        .props.onPress();
    });

    expect(disableBackgroundHealth).toHaveBeenCalledTimes(1);
    act(() => tree!.unmount());
  });

  test('explains how foreground history recovery restores an expired cursor', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest.fn(),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthDisabling={false}
          backgroundHealthEnabling={false}
          backgroundHealthError={null}
          backgroundHealthStatus="needs_foreground"
          notificationPermission="granted"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onDisableBackgroundHealth={jest.fn().mockResolvedValue(undefined)}
          onEnableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={jest.fn()}
          visible
        />,
      );
    });

    expect(
      tree!.root.findByProps({
        children:
          '后台同步游标已失效。打开聊天页并授权读取历史健康数据，核对完成后会恢复后台检查。',
      }),
    ).toBeTruthy();
    expect(
      tree!.root.findByProps({ accessibilityLabel: '关闭后台周期检查' }),
    ).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('lets the user turn off a registered check when native support becomes unavailable', async () => {
    const api = {
      getProactiveCareSettings: jest.fn().mockResolvedValue(settings),
      putProactiveCareSettings: jest.fn(),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <CareSettingsModal
          api={api}
          backgroundHealthDisabling={false}
          backgroundHealthEnabling={false}
          backgroundHealthError="Health Connect 需要更新后才能使用"
          backgroundHealthStatus="unavailable"
          notificationPermission="granted"
          notificationRegistrationFailed={false}
          notificationRequesting={false}
          onClose={jest.fn()}
          onDisableBackgroundHealth={jest.fn().mockResolvedValue(undefined)}
          onEnableBackgroundHealth={jest.fn()}
          onRequestNotificationPermission={jest.fn()}
          visible
        />,
      );
    });

    expect(
      tree!.root.findByProps({ accessibilityLabel: '关闭后台周期检查' }),
    ).toBeTruthy();
    act(() => tree!.unmount());
  });
});