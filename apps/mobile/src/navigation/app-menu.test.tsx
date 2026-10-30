import { act, create } from 'react-test-renderer';

import { AppMenu } from './app-menu';

describe('AppMenu', () => {
  test('only lists conversations, and can start a new one', async () => {
    const onClose = jest.fn();
    const onSelectConversation = jest.fn();
    const onNewChat = jest.fn();

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <AppMenu
          conversations={[
            {
              group: '今天',
              id: 'conversation-1',
              title: '最近有点担心低血糖',
              time: '17:30',
            },
          ]}
          activeConversationId="conversation-1"
          onNewChat={onNewChat}
          onClose={onClose}
          onSelectConversation={onSelectConversation}
          username="demo-user"
          visible
        />,
      );
    });

    act(() => {
      tree!.root
        .findByProps({ accessibilityLabel: '最近有点担心低血糖 17:30' })
        .props.onPress();
      tree!.root
        .findByProps({ accessibilityLabel: '开始新的对话' })
        .props.onPress();
    });

    expect(onSelectConversation).toHaveBeenCalledWith('conversation-1');
    expect(onNewChat).toHaveBeenCalledTimes(1);

    // 导航类入口已经搬到底部 tab / 「我的」页，侧边栏不再重复。
    for (const label of ['健康档案', '健康看板', '血糖记录', '医护授权', '检查更新']) {
      expect(tree!.root.findAllByProps({ accessibilityLabel: label })).toHaveLength(
        0,
      );
    }

    act(() => tree!.unmount());
  });
});
