import { act, create } from 'react-test-renderer';

import { GlucoseScreen } from './GlucoseScreen';

const NOW = '2026-10-20T08:30:00+08:00';

function fakeApi(readings: unknown[]) {
  return {
    getGlucoseReadings: async () =>
      readings as never,
    getGlucoseSummary: async () => ({
      days: 7,
      count: 0,
      averageMgDl: null,
      minimumMgDl: null,
      maximumMgDl: null,
      targetLowMgDl: 70,
      targetHighMgDl: 180,
      belowTargetCount: 0,
      inTargetCount: 0,
      aboveTargetCount: 0,
    }),
    createGlucoseReading: async () => {
      throw new Error('not used');
    },
    revokeGlucoseReading: async () => undefined,
  };
}

async function render(readings: unknown[]) {
  let tree: ReturnType<typeof create> | null = null;
  await act(async () => {
    tree = create(<GlucoseScreen api={fakeApi(readings)} />);
  });
  if (tree === null) throw new Error('render failed');
  return JSON.stringify((tree as ReturnType<typeof create>).toJSON());
}

type Node = { props: Record<string, unknown>; children: Node[] | null };

/** 按 accessibilityLabel 找可点的节点，找不到就报错，避免测试静默通过。 */
function byLabel(tree: ReturnType<typeof create>, label: string): Node {
  const found = tree.root.findAll(
    node => node.props.accessibilityLabel === label,
  );
  if (found.length === 0) throw new Error(`找不到 ${label}`);
  return found[0] as unknown as Node;
}

function press(tree: ReturnType<typeof create>, label: string): void {
  (byLabel(tree, label).props.onPress as () => void)();
}

function typeInto(
  tree: ReturnType<typeof create>,
  label: string,
  text: string,
): void {
  (byLabel(tree, label).props.onChangeText as (value: string) => void)(text);
}

/** 时间字段的 label 带着当前时刻（「测量时间：今天 10:47，点按修改」），按前缀找。 */
function byLabelPrefix(tree: ReturnType<typeof create>, prefix: string): Node {
  const found = tree.root.findAll(node =>
    typeof node.props.accessibilityLabel === 'string'
      ? node.props.accessibilityLabel.startsWith(prefix)
      : false,
  );
  if (found.length === 0) throw new Error(`找不到以 ${prefix} 开头的节点`);
  return found[0] as unknown as Node;
}

async function mountWithCapture() {
  const sent: Record<string, unknown>[] = [];
  const api = {
    ...fakeApi([]),
    createGlucoseReading: async (input: Record<string, unknown>) => {
      sent.push(input);
      return { id: 'g-new' } as never;
    },
  };
  let tree: ReturnType<typeof create> | null = null;
  await act(async () => {
    tree = create(<GlucoseScreen api={api as never} />);
  });
  if (tree === null) throw new Error('render failed');
  return { sent, host: tree as ReturnType<typeof create> };
}

describe('GlucoseScreen', () => {
  it('shows the empty hint when nothing has been logged', async () => {
    const rendered = await render([]);
    expect(rendered).toContain('记一条血糖');
    expect(rendered).toContain('还没有记录');
  });

  it('shows a revoked row as revoked instead of hiding it', async () => {
    const rendered = await render([
      {
        id: 'g1',
        valueMgDl: 62,
        measuredAt: NOW,
        context: 'bedtime',
        source: 'manual',
        note: null,
        revokedAt: NOW,
        createdAt: NOW,
      },
    ]);
    expect(rendered).toContain('62');
    expect(rendered).toContain('已撤回');
    expect(rendered).toContain('睡前');
  });

  it('shows the context label and note for a live row', async () => {
    const rendered = await render([
      {
        id: 'g2',
        valueMgDl: 148,
        measuredAt: NOW,
        context: 'post_meal',
        source: 'manual',
        note: '刚吃了两碗饭',
        revokedAt: null,
        createdAt: NOW,
      },
    ]);
    expect(rendered).toContain('148');
    expect(rendered).toContain('餐后');
    expect(rendered).toContain('刚吃了两碗饭');
    expect(rendered).not.toContain('已撤回');
  });

  it('sends the meter with a fingerstick reading', async () => {
    const sent: Record<string, unknown>[] = [];
    const api = {
      ...fakeApi([]),
      createGlucoseReading: async (input: Record<string, unknown>) => {
        sent.push(input);
        return { id: 'g3' } as never;
      },
    };
    let tree: ReturnType<typeof create> | null = null;
    await act(async () => {
      tree = create(<GlucoseScreen api={api as never} />);
    });
    if (tree === null) throw new Error('render failed');

    await act(async () => {
      typeInto(tree as ReturnType<typeof create>, '血糖数值', '96');
    });
    await act(async () => {
      press(tree as ReturnType<typeof create>, '血糖仪 三诺');
    });
    await act(async () => {
      press(tree as ReturnType<typeof create>, '保存这条血糖记录');
    });

    expect(sent).toHaveLength(1);
    expect(sent[0].valueMgDl).toBe(96);
    expect(sent[0].source).toBe('fingerstick');
    expect(sent[0].sourceDevice).toBe('sannuo');
  });

  it('hides the meter row for a lab reading and sends no meter', async () => {
    const sent: Record<string, unknown>[] = [];
    const api = {
      ...fakeApi([]),
      createGlucoseReading: async (input: Record<string, unknown>) => {
        sent.push(input);
        return { id: 'g4' } as never;
      },
    };
    let tree: ReturnType<typeof create> | null = null;
    await act(async () => {
      tree = create(<GlucoseScreen api={api as never} />);
    });
    if (tree === null) throw new Error('render failed');
    const host = tree as ReturnType<typeof create>;

    // 先在指尖血下选一台机器，再切到静脉血：行要消失，值也不能带出去。
    await act(async () => {
      press(host, '血糖仪 罗氏');
    });
    await act(async () => {
      press(host, '血糖来源 静脉血');
    });
    await act(async () => {
      typeInto(host, '血糖数值', '104');
    });
    await act(async () => {
      press(host, '保存这条血糖记录');
    });

    expect(
      host.root.findAll(node => node.props.accessibilityLabel === '血糖仪 三诺'),
    ).toHaveLength(0);
    expect(sent).toHaveLength(1);
    expect(sent[0].source).toBe('lab');
    expect(sent[0].sourceDevice).toBeUndefined();
  });

  it('sends the measurement time the patient picked, not the moment of saving', async () => {
    const { sent, host } = await mountWithCapture();

    await act(async () => {
      typeInto(host, '血糖数值', '132');
    });
    // 打开「这次测量是什么时候」，往回拨两小时（模拟测完过一会儿才记）。
    await act(async () => {
      (byLabelPrefix(host, '测量时间：').props.onPress as () => void)();
    });
    await act(async () => {
      press(host, '小时减一');
    });
    await act(async () => {
      press(host, '小时减一');
    });
    await act(async () => {
      press(host, '用这个时间');
    });
    await act(async () => {
      press(host, '保存这条血糖记录');
    });

    expect(sent).toHaveLength(1);
    const measured = new Date(String(sent[0].measuredAt)).getTime();
    const twoHoursAgo = Date.now() - 2 * 60 * 60_000;
    expect(Math.abs(measured - twoHoursAgo)).toBeLessThan(180_000);
  });

  it('refuses a measurement time in the future', async () => {
    const { sent, host } = await mountWithCapture();

    await act(async () => {
      typeInto(host, '血糖数值', '90');
    });
    await act(async () => {
      (byLabelPrefix(host, '测量时间：').props.onPress as () => void)();
    });
    await act(async () => {
      press(host, '小时加一');
    });
    await act(async () => {
      press(host, '用这个时间');
    });
    await act(async () => {
      press(host, '保存这条血糖记录');
    });

    expect(sent).toHaveLength(0);
    expect(JSON.stringify(host.toJSON())).toContain('测量时间不能是将来');
  });

  it('separates the measurement time from the record time in the list', async () => {
    const rendered = await render([
      {
        id: 'g5',
        valueMgDl: 100,
        measuredAt: '2026-10-25T10:47:00+08:00',
        context: 'fasting',
        source: 'fingerstick',
        sourceDevice: 'sannuo',
        note: null,
        revokedAt: null,
        createdAt: '2026-10-26T10:47:00+08:00',
      },
    ]);
    expect(rendered).toContain('测量');
    expect(rendered).toContain('记录于');
    expect(rendered).toContain('（补记）');
    expect(rendered).toContain('三诺');
  });
});
