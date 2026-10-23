import { act, create } from 'react-test-renderer';

import { AppMenu } from './app-menu';

describe('AppMenu', () => {
  test('opens health, settings and positions in the continuous conversation', async () => {
    const onClose = jest.fn();
    const onOpenHealth = jest.fn();
    const onOpenSettings = jest.fn();
    const onSelectConversation = jest.fn();

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <AppMenu
          conversations={[
            {
              group: '今天',
              id: 'message-1',
              preview: '最近有点担心低血糖',
              time: '17:30',
            },
          ]}
          onClose={onClose}
          onOpenHealth={onOpenHealth}
          onOpenSettings={onOpenSettings}
          onSelectConversation={onSelectConversation}
          username="demo-user"
          visible
          // ===== 新增：Gadgetbridge props（测试占位） =====
          gadgetbridgeChecking={false}
          gadgetbridgeConnected={false}
          gadgetbridgeSyncing={false}
          gadgetbridgeError={null}
          gadgetbridgeProgress={null}
          gadgetbridgeLastSyncedAt={null}
          onGadgetbridgeConnect={() => { }}
          onGadgetbridgeSync={() => { }}
        />,
      );
    });

    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '健康档案' }).props.onPress();
      tree!.root.findByProps({ accessibilityLabel: '打开设置' }).props.onPress();
      tree!.root
        .findByProps({ accessibilityLabel: '17:30 最近有点担心低血糖' })
        .props.onPress();
    });

    expect(onOpenHealth).toHaveBeenCalledTimes(1);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(onSelectConversation).toHaveBeenCalledWith('message-1');

    // 「检查更新」搬进设置页了，侧边栏不再挂这一行。
    expect(tree!.root.findAllByProps({ accessibilityLabel: '检查更新' })).toHaveLength(0);

    act(() => tree!.unmount());
  });
});
