"""两稿比对用的小集子：第一稿与第二稿成对，标出「数字该不该判成变了」。

判的是 `ensure_same_numbers` 这一关，不是模型的措辞好坏。所以每条都只关心
一件事：两稿里出现的数字（以及全角、末尾的 0、单位写法）算不算同一个数。

这些句子是我们自己写的，覆盖几种容易出错的写法：

- 只是换了说法、数字没动 → 该放行
- 改了数字、删了数字、加了数字 → 该拦下
- `7.20` 与 `7.2`、`１１` 与 `11` 是同一个数 → 该放行
- 数字后面的单位换了（mmol/L ↔ mg/dL）→ 数字一样也放行，单位不在这一关管

`why` 里写清楚这条为什么值得放进来，跑挂了能一眼看出是哪一类坏了。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TwoDraftEvalCase:
    """一条比对样例。"""

    case_id: str
    first: str
    second: str
    # True = 两稿数字一致、该放行；False = 数字被动过、该拦下。
    same: bool
    why: str


def build_cases() -> list[TwoDraftEvalCase]:
    """16 条样例：8 条该放行、8 条该拦下。"""

    return [
        TwoDraftEvalCase(
            case_id="wording-only",
            first="餐后 12.6，有点高，比目标高一些。",
            second="餐后 12.6，比目标高一点。",
            same=True,
            why="只改了措辞，数字没动。",
        ),
        TwoDraftEvalCase(
            case_id="trailing-zero",
            first="今天空腹 7.20 mmol/L。",
            second="今天空腹是 7.2 mmol/L。",
            same=True,
            why="末尾的 0 不算变化。",
        ),
        TwoDraftEvalCase(
            case_id="full-width",
            first="夜里有１１ mmol/L。",
            second="夜里有 11 mmol/L。",
            same=True,
            why="全角数字要归一化。",
        ),
        TwoDraftEvalCase(
            case_id="unit-rewrite",
            first="那次 3.2 mmol/L，偏低。",
            second="那次 3.2 mmol/L，比目标低。",
            same=True,
            why="数字一致、只是后半句换了说法。",
        ),
        TwoDraftEvalCase(
            case_id="reordered",
            first="早餐前 6.4，早餐后 9.1。",
            second="早餐后 9.1，早餐前 6.4。",
            same=True,
            why="换顺序不影响数字集合。",
        ),
        TwoDraftEvalCase(
            case_id="extra-note-no-number",
            first="今天测得比平时密。",
            second="这几天测得比平时密一些，记得歇口气。",
            same=True,
            why="两稿都没有数字，这一关管不到——留着提醒：它只管数字，多出来的一句它不会拦。",
        ),
        TwoDraftEvalCase(
            case_id="same-times",
            first="11-02 03:10 那次 3.2。",
            second="11-02 凌晨 3 点 10 分那次 3.2。",
            same=True,
            why="时间换了说法，但 11、02、3、2、10 都在，集合一致。",
        ),
        TwoDraftEvalCase(
            case_id="rounding",
            first="大约 8.0 mmol/L。",
            second="大约 8 mmol/L。",
            same=True,
            why="8.0 与 8 是同一个数。",
        ),
        TwoDraftEvalCase(
            case_id="changed-number",
            first="餐后 12.6，有点高。",
            second="餐后 11.6，有点高。",
            same=False,
            why="小数位改了一位，必须拦。",
        ),
        TwoDraftEvalCase(
            case_id="dropped-number",
            first="昨晚 3.2，今天 6.8。",
            second="昨晚 3.2 那次挺吓人。",
            same=False,
            why="第二稿把 6.8 删了。",
        ),
        TwoDraftEvalCase(
            case_id="added-number",
            first="今天血糖有点高。",
            second="今天血糖有点高，别超过 10。",
            same=False,
            why="凭空多出一个数，等于加了条界限。",
        ),
        TwoDraftEvalCase(
            case_id="shifted-decimal",
            first="血糖 5.6 mmol/L。",
            second="血糖 56 mmol/L。",
            same=False,
            why="小数点丢了，量级差十倍。",
        ),
        TwoDraftEvalCase(
            case_id="changed-count",
            first="这周有 3 次低血糖。",
            second="这周有 4 次低血糖。",
            same=False,
            why="次数被改了。",
        ),
        TwoDraftEvalCase(
            case_id="changed-target",
            first="目标是 3.9 到 10.0。",
            second="目标是 4.0 到 10.0。",
            same=False,
            why="目标下限被改了，这是他自己的设定。",
        ),
        TwoDraftEvalCase(
            case_id="unit-only-change",
            first="3.2 mmol/L。",
            second="58 mg/dL。",
            same=False,
            why="换了单位却没换算，数字集合变了——这一关按数字判，正好拦下。",
        ),
        TwoDraftEvalCase(
            case_id="hidden-digit-in-word",
            first="1 型糖尿病，血糖 6.6。",
            second="一型糖尿病，血糖 6.6。",
            same=False,
            why="「1」被写成「一」，数字集合少了一个——知道这一关的边界：中文数字它看不懂。",
        ),
    ]
