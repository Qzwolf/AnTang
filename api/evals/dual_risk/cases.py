"""双风险标注的小规模评测集。

这里是人工标的对照表：一条读数 + 患者当时说的话，标出「这个数当时危不危险」
和「他说了什么感觉」。用它检查两件事：程序判危险用的固定阈值（54 / 250 mg/dL）
与目标范围，是否和临床上真正需要处理的时刻对得上；以及最容易漏的那一档
（`silent`，血糖已经偏离、他却什么都没感觉到）能不能被挑出来。

规模刻意只有二十几条：这是给自己看的回归集，不是经过验证的量表。标的人只有我
一个，病例来自产品里真实出现过的情形（夜里 3.2 没感觉、饭后 13.9 心慌、因为怕低
血糖把目标范围调宽到 80–200 的人）。结论只能说明「规则没有走样」，不能说明
「这样分档是对的」——后者要靠患者与医生一起标。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DualRiskEvalCase:
    """一条人工标的对照。

    `value_mg_dl` 与 `felt` 是输入；`dangerous` 与 `label` 是人工判断的答案。
    `target_low_mg_dl` / `target_high_mg_dl` 是他自己设的目标范围——有的人调宽到
    80–200，所以同样的数值在他身上算不算偏离并不一样。
    """

    case_id: str
    value_mg_dl: int
    felt: str
    dangerous: bool
    label: str
    target_low_mg_dl: int = 70
    target_high_mg_dl: int = 180
    said: str = ""
    note: str = ""


_CASES: tuple[DualRiskEvalCase, ...] = (
    DualRiskEvalCase(
        case_id="night-3.2-no-feeling",
        value_mg_dl=58,
        felt="none",
        dangerous=True,
        label="silent",
        said="刚测了 3.2，可我一点感觉都没有",
        note="产品里真实出现过的那一条：数值已经该处理，身体却没报警。",
    ),
    DualRiskEvalCase(
        case_id="deep-low-no-feeling",
        value_mg_dl=50,
        felt="none",
        dangerous=True,
        label="silent",
        said="2.8，没什么感觉，就是有点困",
        note="3.0 以下仍无感觉，是这一档里最需要提醒的情形。",
    ),
    DualRiskEvalCase(
        case_id="severe-low-no-feeling",
        value_mg_dl=39,
        felt="none",
        dangerous=True,
        label="silent",
        said="2.2，我没事",
        note="2.2 还说自己没事，处理与察觉两件事都要说。",
    ),
    DualRiskEvalCase(
        case_id="just-below-target-no-feeling",
        value_mg_dl=65,
        felt="none",
        dangerous=True,
        label="silent",
        said="3.6 吧，没什么感觉",
        note="只比目标低一点：仍算偏离，但语气不必像 2.2 那样急。",
    ),
    DualRiskEvalCase(
        case_id="in-range-no-feeling",
        value_mg_dl=80,
        felt="none",
        dangerous=False,
        label="neither",
        said="4.4，没什么感觉",
        note="在范围内又没感觉：什么都不用做。",
    ),
    DualRiskEvalCase(
        case_id="in-range-a-bit-uneasy",
        value_mg_dl=85,
        felt="some",
        dangerous=False,
        label="felt_only",
        said="4.7，有点心慌",
        note="数不低但人难受：要处理的是怕，不是补糖。",
    ),
    DualRiskEvalCase(
        case_id="normal-but-very-afraid",
        value_mg_dl=100,
        felt="strong",
        dangerous=False,
        label="felt_only",
        said="5.5，我一低就完了，手都抖",
        note="典型的高恐惧低风险：不能顺着把「我真的要低了」讲重。",
    ),
    DualRiskEvalCase(
        case_id="high-but-no-feeling",
        value_mg_dl=240,
        felt="none",
        dangerous=True,
        label="silent",
        said="13.3，没什么感觉",
        note="高值也可能没感觉：偏离就要说，但不必按低血糖处理。",
    ),
    DualRiskEvalCase(
        case_id="very-high-no-feeling",
        value_mg_dl=260,
        felt="none",
        dangerous=True,
        label="silent",
        said="14.5，没感觉",
        note="超过 250 归到「马上要处理」。",
    ),
    DualRiskEvalCase(
        case_id="very-high-with-thirst",
        value_mg_dl=300,
        felt="some",
        dangerous=True,
        label="both",
        said="16.7，渴得厉害",
        note="高值且有感觉：两边都成立，说得出口的那种。",
    ),
    DualRiskEvalCase(
        case_id="low-with-shaking",
        value_mg_dl=68,
        felt="some",
        dangerous=True,
        label="both",
        said="3.8，手抖、出汗",
        note="最常见的低血糖：偏低且身体报警。",
    ),
    DualRiskEvalCase(
        case_id="low-with-strong-symptoms",
        value_mg_dl=45,
        felt="strong",
        dangerous=True,
        label="both",
        said="2.5，整个人发虚，站不住",
        note="需要马上处理，且他清楚知道自己难受。",
    ),
    DualRiskEvalCase(
        case_id="exactly-at-lower-target",
        value_mg_dl=70,
        felt="none",
        dangerous=False,
        label="neither",
        said="3.9，没感觉",
        note="正好压在下限：不算偏离，但值得说一句「贴着线」。",
    ),
    DualRiskEvalCase(
        case_id="just-below-lower-target",
        value_mg_dl=69,
        felt="none",
        dangerous=True,
        label="silent",
        said="3.8，没什么感觉",
        note="比下限低 1 mg/dL：规则上算偏离，语气要轻。",
    ),
    DualRiskEvalCase(
        case_id="critical-boundary-low",
        value_mg_dl=54,
        felt="none",
        dangerous=True,
        label="silent",
        said="3.0，没感觉",
        note="正好 54：不算 critical，仍是偏离目标。",
    ),
    DualRiskEvalCase(
        case_id="below-critical-low",
        value_mg_dl=53,
        felt="none",
        dangerous=True,
        label="silent",
        said="2.9，没感觉",
        note="53 落到 critical：处理优先级升高。",
    ),
    DualRiskEvalCase(
        case_id="critical-boundary-high",
        value_mg_dl=250,
        felt="none",
        dangerous=True,
        label="silent",
        said="13.9，没感觉",
        note="正好 250：不算 critical high。",
    ),
    DualRiskEvalCase(
        case_id="above-critical-high",
        value_mg_dl=251,
        felt="none",
        dangerous=True,
        label="silent",
        said="13.9 多一点，没感觉",
        note="251 落到 critical high：与 250 只差 1，档位变了。",
    ),
    DualRiskEvalCase(
        case_id="upper-target-boundary",
        value_mg_dl=180,
        felt="none",
        dangerous=False,
        label="neither",
        said="10.0，没感觉",
        note="正好压在上限，不算偏离。",
    ),
    DualRiskEvalCase(
        case_id="just-above-upper-target",
        value_mg_dl=181,
        felt="none",
        dangerous=True,
        label="silent",
        said="10.1，没什么感觉",
        note="超出上限 1 mg/dL：规则上偏离。",
    ),
    DualRiskEvalCase(
        case_id="wide-target-in-range",
        value_mg_dl=190,
        felt="none",
        dangerous=False,
        label="neither",
        target_low_mg_dl=80,
        target_high_mg_dl=200,
        said="10.5，没感觉",
        note="他自己把上限设到 200：同一个数值，在他身上不算偏离。",
    ),
    DualRiskEvalCase(
        case_id="wide-target-below",
        value_mg_dl=75,
        felt="none",
        dangerous=True,
        label="silent",
        target_low_mg_dl=80,
        target_high_mg_dl=200,
        said="4.2，没什么感觉",
        note="目标下限调到 80 之后，4.2 也算偏低了。",
    ),
    DualRiskEvalCase(
        case_id="wide-target-low-felt",
        value_mg_dl=90,
        felt="strong",
        dangerous=False,
        label="felt_only",
        target_low_mg_dl=80,
        target_high_mg_dl=200,
        said="5.0，我心里发慌，怕待会儿就低",
        note="范围宽的人更容易出现「数没问题、人很怕」。",
    ),
    DualRiskEvalCase(
        case_id="normal-but-worried-after-sport",
        value_mg_dl=120,
        felt="some",
        dangerous=False,
        label="felt_only",
        said="6.7，刚跑完步有点发虚，怕等下低",
        note="运动后的预期性害怕：不是低血糖，也不能说「你想多了」。",
    ),
    DualRiskEvalCase(
        case_id="rebound-high-after-treatment",
        value_mg_dl=210,
        felt="none",
        dangerous=True,
        label="silent",
        said="11.7，刚吃了糖才上来，现在没感觉",
        note="补糖后的反跳高值：偏离成立，但不该再催他补糖。",
    ),
    DualRiskEvalCase(
        case_id="mild-low-felt-slightly",
        value_mg_dl=62,
        felt="some",
        dangerous=True,
        label="both",
        said="3.4，有点发虚",
        note="偏低且隐约有感觉：这一档最容易被他自己忽略。",
    ),
)


def build_cases() -> tuple[DualRiskEvalCase, ...]:
    """返回固定的双风险对照集。"""

    return _CASES


__all__ = ["DualRiskEvalCase", "build_cases"]
