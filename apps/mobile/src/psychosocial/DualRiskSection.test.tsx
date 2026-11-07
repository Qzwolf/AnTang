import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { DualRiskEntry } from '../api/types';
import { DualRiskSection } from './DualRiskSection';

function collectText(children: unknown): string {
  if (typeof children === 'string') return children;
  if (typeof children === 'number') return String(children);
  if (Array.isArray(children)) return children.map(collectText).join('');
  if (
    children !== null &&
    typeof children === 'object' &&
    'props' in (children as { props?: unknown })
  ) {
    const props = (children as { props?: { children?: unknown } }).props;
    return collectText(props?.children);
  }
  return '';
}

function treeText(tree: ReactTestRenderer): string {
  return tree.root
    .findAll(() => true)
    .map((node) => collectText(node.props?.children))
    .join('\n');
}

function buildApi(entries: DualRiskEntry[]) {
  return {
    getDualRiskEntries: async () => entries,
  };
}

async function render(
  entries: DualRiskEntry[],
  onOpenRecord?: (measuredAt: string) => void,
): Promise<ReactTestRenderer> {
  const api = buildApi(entries);
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(<DualRiskSection api={api} onOpenRecord={onOpenRecord} />);
  });
  return tree!;
}

const ENTRIES: DualRiskEntry[] = [
  {
    id: 'r1',
    measuredAt: '2026-11-02T03:10:00+08:00',
    valueMgDl: 58,
    label: 'silent',
    labelText: '血糖偏离得厉害，你没觉得',
  },
  {
    id: 'r2',
    measuredAt: '2026-10-29T15:40:00+08:00',
    valueMgDl: 65,
    label: 'felt_only',
    labelText: '血糖不算离谱，你却很怕',
  },
];

describe('DualRiskSection', () => {
  it('shows each moment with its own sentence, time and number', async () => {
    const tree = await render(ENTRIES);
    const text = treeText(tree);

    expect(text).toContain('血糖偏离得厉害，你没觉得');
    expect(text).toContain('3.2 mmol/L');
    expect(text).toContain('血糖不算离谱，你却很怕');
    expect(text).toContain('3.6 mmol/L');
  });

  it('does not summarise the list into a count', async () => {
    const tree = await render(ENTRIES);
    const text = treeText(tree);

    // 「再说一次」里有「次」字，所以盯的是「几次」这种计数，不是这个字。
    expect(text).not.toMatch(/\d+\s*次/);
    expect(text).not.toContain('%');
  });

  it('says where the entries come from instead of scoring them', async () => {
    const tree = await render(ENTRIES);
    const text = treeText(tree);

    expect(text).toContain('不打分，也不给建议');
    expect(text).toContain('要改，就在对话里再说一次');
  });

  it('tells him what to say when there is nothing recorded yet', async () => {
    const tree = await render([]);

    expect(treeText(tree)).toContain('还没有记下这样的对照');
  });

  it('opens the record of that moment when a row is tapped', async () => {
    const opened: string[] = [];
    const tree = await render(ENTRIES, (measuredAt) => opened.push(measuredAt));

    const rows = tree.root.findAll(
      (node) =>
        typeof node.props?.onPress === 'function' &&
        node.props?.accessibilityHint === '回到那天的记录',
    );
    expect(rows).toHaveLength(2);

    await act(async () => {
      rows[0].props.onPress();
    });

    expect(opened).toEqual(['2026-11-02T03:10:00+08:00']);
    expect(treeText(tree)).toContain('点一行，能回到那天的记录');
  });

  it('stays plain text when nothing can be opened', async () => {
    const tree = await render(ENTRIES);

    expect(treeText(tree)).not.toContain('点一行');
    expect(
      tree.root.findAll((node) => node.props?.accessibilityHint === '回到那天的记录'),
    ).toHaveLength(0);
  });
});
