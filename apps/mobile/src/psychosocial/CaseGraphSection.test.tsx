import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { CaseGraph } from '../api/types';
import { CaseGraphSection } from './CaseGraphSection';

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

function buildApi(graph: CaseGraph) {
  return {
    getCaseGraph: async () => graph,
  };
}

async function render(graph: CaseGraph): Promise<ReactTestRenderer> {
  const api = buildApi(graph);
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(<CaseGraphSection api={api} />);
  });
  return tree!;
}

const GRAPH: CaseGraph = {
  nodes: [
    {
      id: 'n1',
      layer: 'cognitive',
      kindLabel: '念头',
      text: '我一到晚上就害怕',
      intensity: null,
    },
    {
      id: 'n2',
      layer: 'affective',
      kindLabel: '情绪',
      text: '心里发紧',
      intensity: 3,
    },
  ],
  edges: [
    {
      fromNodeId: 'n1',
      toNodeId: 'n2',
      relationLabel: '触发',
      note: '因为怕夜里低血糖',
    },
  ],
};

describe('CaseGraphSection', () => {
  it('says plainly that there is nothing recorded yet', async () => {
    const tree = await render({ nodes: [], edges: [] });

    expect(treeText(tree)).toContain('还没有记下什么');
  });

  it('lists what he said, with the intensity only where it belongs', async () => {
    const tree = await render(GRAPH);
    const text = treeText(tree);

    expect(text).toContain('我一到晚上就害怕');
    expect(text).toContain('心里发紧');
    expect(text).toContain('情绪 · 强度 3/5');
    expect(text).not.toContain('念头 · 强度');
  });

  it('draws the causal link with his own words on both ends', async () => {
    const tree = await render(GRAPH);

    expect(treeText(tree)).toContain(
      '我一到晚上就害怕 → 触发 → 心里发紧',
    );
  });

  it('keeps the explanation at the bottom instead of over the list', async () => {
    const tree = await render(GRAPH);
    const text = treeText(tree);

    expect(text).toContain('灰绿是念头，浅蓝是身上和心里的感觉');
    expect(text).toContain('想改，就在对话里再说一次');
  });
});
