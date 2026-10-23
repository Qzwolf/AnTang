import { act, create, type ReactTestRenderer } from 'react-test-renderer';

import type { PsychosocialInstrument, PsychosocialProfile } from '../api/types';
import { PsychosocialSection } from './PsychosocialSection';

const SCALE = [
  { value: 0, label: '从不' },
  { value: 1, label: '很少' },
  { value: 2, label: '有时' },
  { value: 3, label: '经常' },
  { value: 4, label: '几乎每天' },
];

function instrument(
  overrides: Partial<PsychosocialInstrument> = {},
): PsychosocialInstrument {
  return {
    id: 'antang-behavior-v1',
    title: '糖糖怎么跟你说话',
    instructions: '六个问题，约一分钟。选最接近最近一个月实际情况的一项。',
    disclaimer: '这几道题是我们自己写的，不是标准化量表，只用来决定沟通方式，不作为诊断依据。',
    options: SCALE,
    questions: [
      {
        id: 'repeat-test',
        kind: 'behavior',
        text: '过去一个月，我因为担心低血糖而反复测血糖，即使刚测过。',
      },
      {
        id: 'skip-exercise',
        kind: 'behavior',
        text: '过去一个月，我因为怕低血糖而减少或取消了原本要做的运动。',
      },
      {
        id: 'bedtime-snack',
        kind: 'behavior',
        text: '过去一个月，我会在睡前额外吃东西，即使当时血糖正常。',
      },
      {
        id: 'night-worry',
        kind: 'worry',
        text: '过去一个月，我担心夜里睡着以后发生低血糖。',
      },
      {
        id: 'alone-worry',
        kind: 'worry',
        text: '过去一个月，我担心一个人时低血糖会没人帮忙。',
      },
      {
        id: 'anxious-thought',
        kind: 'worry',
        text: '过去一个月，一想到可能低血糖，我就会紧张或心慌。',
      },
    ],
    goldLabel: '你平时能感觉到低血糖开始了吗？',
    goldHint: '1 = 总能感觉到，7 = 完全感觉不到',
    goldOptions: [1, 2, 3, 4, 5, 6, 7].map((value) => ({
      value,
      label: String(value),
    })),
    ...overrides,
  };
}

function profile(overrides: Partial<PsychosocialProfile> = {}): PsychosocialProfile {
  return {
    assessedAt: '2026-10-31T09:00:00+08:00',
    instrument: 'antang-behavior-v1',
    weeklyContactLimit: 4,
    ...overrides,
  };
}

function buildApi(
  existing: PsychosocialProfile | null,
  questions: PsychosocialInstrument = instrument(),
) {
  return {
    getPsychosocialInstrument: jest.fn().mockResolvedValue(questions),
    getPsychosocialAssessment: jest.fn().mockResolvedValue(existing),
    recordPsychosocialAssessment: jest.fn().mockResolvedValue(profile()),
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

/** 组件的文案常常被拆成多个子节点，所以按整棵子树拼文本再匹配。 */
function treeText(tree: ReactTestRenderer): string {
  return tree.root
    .findAll(() => true)
    .map((node) => collectText(node.props?.children))
    .join('\n');
}

async function render(
  api: ReturnType<typeof buildApi>,
): Promise<ReactTestRenderer> {
  let tree: ReactTestRenderer | undefined;
  await act(async () => {
    tree = create(<PsychosocialSection api={api} />);
  });
  return tree!;
}

function findPressable(tree: ReactTestRenderer, label: string) {
  const target = tree.root.findAll(
    (node) =>
      node.props?.accessibilityRole !== undefined &&
      typeof node.props?.onPress === 'function' &&
      collectText(node.props.children).includes(label),
  );
  if (target.length === 0) throw new Error(`找不到按钮：${label}`);
  return target[0];
}

function press(tree: ReactTestRenderer, label: string) {
  const target = findPressable(tree, label);
  act(() => {
    target.props.onPress();
  });
}

function radios(tree: ReactTestRenderer) {
  return tree.root.findAll(
    (node) =>
      node.props?.accessibilityRole === 'radio' &&
      typeof node.props?.onPress === 'function',
  );
}

describe('PsychosocialSection', () => {
  it('没有评估时先展示入口，点开后出现服务端给的七道题', async () => {
    const api = buildApi(null);
    const tree = await render(api);

    expect(api.getPsychosocialInstrument).toHaveBeenCalledTimes(1);
    expect(api.getPsychosocialAssessment).toHaveBeenCalledTimes(1);
    expect(treeText(tree)).toContain('开始填写');
    expect(treeText(tree)).toContain('糖糖怎么跟你说话');

    press(tree, '开始填写');

    const text = treeText(tree);
    expect(text).toContain('你平时能感觉到低血糖开始了吗');
    expect(text).toContain('反复测血糖');
    expect(text).toContain('不是标准化量表');
  });

  it('题目、选项与说明都跟着服务端走，改动不需要重新打包', async () => {
    const api = buildApi(
      null,
      instrument({
        id: 'antang-behavior-v2',
        title: '糖糖的说话方式',
        instructions: '三个问题，半分钟。',
        disclaimer: '这是新题库，换题只改后端。',
        options: [
          { value: 0, label: '没有' },
          { value: 3, label: '经常' },
        ],
        questions: [
          { id: 'new-one', kind: 'behavior', text: '我会因为害怕而多测。' },
          { id: 'new-two', kind: 'worry', text: '我夜里睡不踏实。' },
        ],
        goldLabel: '你能感觉到低血糖吗？',
        goldHint: '1 = 能，5 = 不能',
        goldOptions: [1, 5].map((value) => ({ value, label: String(value) })),
      }),
    );
    const tree = await render(api);
    press(tree, '开始填写');

    const text = treeText(tree);
    expect(text).toContain('三个问题，半分钟。');
    expect(text).toContain('我会因为害怕而多测。');
    expect(text).toContain('你能感觉到低血糖吗？');
    expect(text).toContain('这是新题库，换题只改后端。');
    // 旧题库的题干不该再出现。
    expect(text).not.toContain('反复测血糖');
    // 两道题各两个选项，加上察觉评分两个选项。
    expect(radios(tree).length).toBe(2 * 2 + 2);
    expect(treeText(tree)).toContain('还差 3 题。');
  });

  it('答完题并选好察觉评分后，按服务端选项的最大值折算百分比', async () => {
    const api = buildApi(null);
    const tree = await render(api);
    press(tree, '开始填写');

    // 六道题各五个选项，第七题（察觉评分）七个选项。
    expect(radios(tree).length).toBe(6 * 5 + 7);

    // 前三题（行为）都选「几乎每天」= 4；后三题（担忧）都选「从不」= 0。
    for (let index = 0; index < 3; index += 1) {
      act(() => {
        radios(tree)[index * 5 + 4].props.onPress();
      });
    }
    for (let index = 3; index < 6; index += 1) {
      act(() => {
        radios(tree)[index * 5].props.onPress();
      });
    }
    act(() => {
      radios(tree)[30 + 4].props.onPress();
    });

    expect(treeText(tree)).toContain('已经填完了');

    await act(async () => {
      findPressable(tree, '保存').props.onPress();
    });

    expect(api.recordPsychosocialAssessment).toHaveBeenCalledTimes(1);
    const input = api.recordPsychosocialAssessment.mock.calls[0][0];
    expect(input.fearBehaviorPercent).toBe(100);
    expect(input.fearWorryPercent).toBe(0);
    expect(input.goldScore).toBe(5);
    // 存的是服务端给的题库 id，不是 App 里写的字符串。
    expect(input.instrument).toBe('antang-behavior-v1');
    expect(typeof input.assessedAt).toBe('string');
    // 逐题选择一起交上去，服务端按题库算分。
    expect(input.answers).toHaveLength(6);
    expect(
      input.answers.every(
        (answer: { id: string; value: number }) =>
          typeof answer.id === 'string' && typeof answer.value === 'number',
      ),
    ).toBe(true);
  });

  it('没填完时保存按钮不可用', async () => {
    const api = buildApi(null);
    const tree = await render(api);
    press(tree, '开始填写');

    expect(
      findPressable(tree, '保存').props.accessibilityState?.disabled,
    ).toBe(true);
  });

  it('题库读不到时给出错误，不装成可以填写', async () => {
    const api = buildApi(null);
    api.getPsychosocialInstrument.mockRejectedValue(new Error('服务器没有题库'));
    const tree = await render(api);

    expect(treeText(tree)).toContain('服务器没有题库');
    expect(treeText(tree)).not.toContain('开始填写');
  });

  it('已有评估时展示每周联系次数，而不是分数', async () => {
    const api = buildApi(profile());
    const tree = await render(api);

    const text = treeText(tree);
    expect(text).toContain('每周主动联系你约 4 次');
    expect(text).toContain('上次填写：2026-10-31');
    // 不向用户复述分数或量表名。
    expect(text).not.toContain('HFS');
    expect(text).not.toContain('75');
  });
});
