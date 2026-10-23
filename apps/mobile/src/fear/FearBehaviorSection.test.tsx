import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type {
  BeliefEventRecord,
  FearBehaviorLog,
  FearSummary,
} from '../api/types';
import { FearBehaviorSection } from './FearBehaviorSection';

function summary(overrides: Partial<FearSummary> = {}): FearSummary {
  return {
    windowStart: '2026-10-03T09:00:00+08:00',
    windowEnd: '2026-10-31T09:00:00+08:00',
    behaviorCounts: { extra_check: 6, exercise_avoidance: 2 },
    behaviorTotal: 8,
    monitoringWeekly: 1.5,
    avoidanceWeekly: 0.5,
    beliefs: [],
    assessment: null,
    discrepancy: 'unknown',
    discrepancyNote: '',
    ...overrides,
  };
}

function behaviorLog(overrides: Partial<FearBehaviorLog> = {}): FearBehaviorLog {
  return {
    id: '0f080e32-35c6-4f92-8fee-0cc98dcc7dde',
    kind: 'night_check',
    label: '夜间反复确认血糖',
    count: 6,
    note: null,
    occurredAt: '2026-10-30T23:10:00+08:00',
    ...overrides,
  };
}

function beliefEvent(
  overrides: Partial<BeliefEventRecord> = {},
): BeliefEventRecord {
  return {
    id: '7b1e5e10-2c6f-4a3b-9d1f-2b9c4d5e6f70',
    beliefCode: 'hypo_causes_coma',
    label: '低血糖会让我昏迷',
    quote: '总怕半夜低血糖昏过去没人知道',
    occurrence: 'mentioned',
    detectedAt: '2026-10-29T22:00:00+08:00',
    ...overrides,
  };
}

function buildApi(
  value: FearSummary,
  options: {
    logs?: FearBehaviorLog[];
    events?: BeliefEventRecord[];
    afterDelete?: FearSummary;
    deleteFails?: boolean;
  } = {},
) {
  const logs = options.logs ?? [];
  const events = options.events ?? [];
  return {
    getFearSummary: jest
      .fn()
      .mockResolvedValueOnce(value)
      .mockResolvedValue(options.afterDelete ?? value),
    getFearBehaviorLogs: jest.fn().mockResolvedValue(logs),
    getBeliefEvents: jest.fn().mockResolvedValue(events),
    deleteFearBehaviorLog: options.deleteFails
      ? jest.fn().mockRejectedValue(new Error('删除失败'))
      : jest.fn().mockResolvedValue(undefined),
    deleteBeliefEvent: jest.fn().mockResolvedValue(undefined),
  };
}

function collectText(children: unknown): string {
  if (typeof children === 'string') return children;
  if (typeof children === 'number') return String(children);
  if (Array.isArray(children)) return children.map(collectText).join('');
  if (children !== null && typeof children === 'object' && 'props' in children) {
    const element = children as { props?: { children?: unknown } };
    return collectText(element.props?.children);
  }
  return '';
}

function treeText(tree: ReactTestRenderer): string {
  return tree.root
    .findAll(() => true)
    .map((node) => collectText(node.props?.children))
    .join('\n');
}

function findByLabel(
  tree: ReactTestRenderer,
  label: string,
): ReturnType<ReactTestRenderer['root']['findByProps']> {
  return tree.root.findByProps({ accessibilityLabel: label });
}

async function render(
  api: ReturnType<typeof buildApi>,
): Promise<ReactTestRenderer> {
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(<FearBehaviorSection api={api} />);
  });
  return tree!;
}

describe('FearBehaviorSection', () => {
  it('说出最近提到过什么，但不报次数', async () => {
    const tree = await render(buildApi(summary()));
    const text = treeText(tree);
    expect(text).toContain('最近 28 天');
    expect(text).toContain('额外查血糖');
    expect(text).toContain('回避运动');
    // 大号计数会被读成审计，所以只在没被要求的细节里保留
    expect(text).not.toContain('6 次');
    expect(text).not.toContain('平均每周 1.5 次');
    expect(text).toContain('不是量表，也不打分');
  });

  it('念头默认收起，点开才看到原话与次数', async () => {
    const tree = await render(
      buildApi(
        summary({
          beliefs: [
            {
              beliefCode: 'hypo_causes_coma',
              label: '低血糖会让我昏迷',
              mentions: 3,
              reframes: 1,
              denials: 0,
              latestQuote: '总怕半夜低血糖昏过去没人知道',
              latestDetectedAt: '2026-10-30T23:10:00+08:00',
              resolved: false,
            },
          ],
        }),
      ),
    );
    expect(treeText(tree)).not.toContain('总怕半夜低血糖昏过去没人知道');

    await act(async () => {
      findByLabel(tree, '展开反复出现的念头').props.onPress();
    });

    const text = treeText(tree);
    expect(text).toContain('低血糖会让我昏迷');
    expect(text).toContain('提到 3 次');
    expect(text).toContain('总怕半夜低血糖昏过去没人知道');
    expect(text).not.toContain('%');
  });

  it('没有记录时给出空状态说明', async () => {
    const tree = await render(
      buildApi(
        summary({
          behaviorCounts: {},
          behaviorTotal: 0,
          monitoringWeekly: 0,
          avoidanceWeekly: 0,
        }),
      ),
    );
    expect(treeText(tree)).toContain('还没有记录');
  });

  it('读取失败时显示错误文案', async () => {
    const api = {
      getFearSummary: jest.fn().mockRejectedValue(new Error('网络不可用')),
      getFearBehaviorLogs: jest.fn().mockResolvedValue([]),
      getBeliefEvents: jest.fn().mockResolvedValue([]),
      deleteFearBehaviorLog: jest.fn(),
      deleteBeliefEvent: jest.fn(),
    };
    const tree = await render(api);
    expect(treeText(tree)).toContain('网络不可用');
  });

  it('逐条列出可删除的记录，并带上原话', async () => {
    const tree = await render(
      buildApi(summary(), {
        logs: [behaviorLog()],
        events: [beliefEvent()],
      }),
    );
    const text = treeText(tree);
    expect(text).toContain('记录');
    expect(text).toContain('夜间反复确认血糖');
    expect(text).toContain('低血糖会让我昏迷');
    expect(text).toContain('总怕半夜低血糖昏过去没人知道');
    expect(text).toContain('删除');
  });

  it('删除前必须确认，确认后才调用接口并刷新汇总', async () => {
    const afterDelete = summary({
      behaviorCounts: { extra_check: 6 },
      behaviorTotal: 6,
    });
    const api = buildApi(summary(), { logs: [behaviorLog()], afterDelete });
    const tree = await render(api);

    await act(async () => {
      findByLabel(tree, '删除夜间反复确认血糖').props.onPress();
    });
    expect(treeText(tree)).toContain('删掉这条？');
    expect(api.deleteFearBehaviorLog).not.toHaveBeenCalled();

    await act(async () => {
      findByLabel(tree, '确认删除').props.onPress();
    });
    expect(api.deleteFearBehaviorLog).toHaveBeenCalledWith(behaviorLog().id);
    expect(treeText(tree)).not.toContain('删掉这条？');
    expect(treeText(tree)).not.toContain('夜间反复确认血糖');
    expect(api.getFearSummary).toHaveBeenCalledTimes(2);
  });

  it('取消确认时什么都不删', async () => {
    const api = buildApi(summary(), { logs: [behaviorLog()] });
    const tree = await render(api);

    await act(async () => {
      findByLabel(tree, '删除夜间反复确认血糖').props.onPress();
    });
    await act(async () => {
      findByLabel(tree, '取消删除').props.onPress();
    });

    expect(api.deleteFearBehaviorLog).not.toHaveBeenCalled();
    expect(treeText(tree)).toContain('夜间反复确认血糖');
  });

  it('删除失败时保留记录并说明原因', async () => {
    const api = buildApi(summary(), { logs: [behaviorLog()], deleteFails: true });
    const tree = await render(api);

    await act(async () => {
      findByLabel(tree, '删除夜间反复确认血糖').props.onPress();
    });
    await act(async () => {
      findByLabel(tree, '确认删除').props.onPress();
    });

    expect(treeText(tree)).toContain('删除失败');
    expect(treeText(tree)).toContain('夜间反复确认血糖');
  });

  it('信念记录也能逐条删掉', async () => {
    const api = buildApi(summary(), { events: [beliefEvent()] });
    const tree = await render(api);

    await act(async () => {
      findByLabel(tree, '删除低血糖会让我昏迷').props.onPress();
    });
    await act(async () => {
      findByLabel(tree, '确认删除').props.onPress();
    });

    expect(api.deleteBeliefEvent).toHaveBeenCalledWith(beliefEvent().id);
    expect(treeText(tree)).not.toContain('总怕半夜低血糖昏过去没人知道');
  });
});
