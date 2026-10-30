import { act, create } from 'react-test-renderer';

import { GlucoseTargetSheet } from './target-sheet';

type Node = { props: Record<string, unknown> };

async function mount(props: {
  onCancel?: () => void;
  onSave?: (target: { highMgDl: number; lowMgDl: number }) => void;
  target?: { highMgDl: number; lowMgDl: number };
}) {
  let tree: ReturnType<typeof create> | null = null;
  await act(async () => {
    tree = create(
      <GlucoseTargetSheet
        onCancel={props.onCancel ?? (() => undefined)}
        onSave={props.onSave ?? (() => undefined)}
        target={props.target ?? { highMgDl: 180, lowMgDl: 70 }}
        visible
      />,
    );
  });
  if (tree === null) throw new Error('render failed');
  return tree as ReturnType<typeof create>;
}

async function press(tree: ReturnType<typeof create>, label: string) {
  const found = tree.root.findAll(
    (node) => (node.props as Node['props']).accessibilityLabel === label,
  );
  if (found.length === 0) throw new Error(`找不到 ${label}`);
  await act(async () => {
    (found[0].props as { onPress: () => void }).onPress();
  });
}

describe('GlucoseTargetSheet', () => {
  it('starts from the range the patient already has', async () => {
    const tree = await mount({ target: { highMgDl: 160, lowMgDl: 80 } });

    const body = JSON.stringify(tree.toJSON());
    expect(body).toContain('4.4 mmol/L');
    expect(body).toContain('8.9 mmol/L');
  });

  it('hands the edited range back when the patient saves', async () => {
    const onSave = jest.fn();
    const tree = await mount({ onSave });

    await press(tree, '上限加 0.1');
    await press(tree, '保存目标范围');

    expect(onSave).toHaveBeenCalledWith({ highMgDl: 182, lowMgDl: 70 });
  });

  it('drops the edits when the patient cancels', async () => {
    const onCancel = jest.fn();
    const onSave = jest.fn();
    const tree = await mount({ onCancel, onSave });

    await press(tree, '下限减 0.1');
    await press(tree, '取消修改目标范围');

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
    const body = JSON.stringify(tree.toJSON());
    // 草稿丢掉了：又显示回 3.9，而不是调低后的 3.8。
    expect(body).toContain('3.9 mmol/L');
  });

  it('keeps the low end below the high end while stepping', async () => {
    // 上下限只差 0.1 时再往下限按「+」：不能推成和上限一样高，否则算不出高低。
    const onSave = jest.fn();
    const tree = await mount({ onSave, target: { highMgDl: 72, lowMgDl: 70 } });

    await press(tree, '下限加 0.1');

    const body = JSON.stringify(tree.toJSON());
    expect(body).toContain('3.9 mmol/L');
    expect(body).not.toContain('下限要小于上限。');
    await press(tree, '保存目标范围');
    expect(onSave).toHaveBeenCalledWith({ highMgDl: 72, lowMgDl: 70 });
  });
});
