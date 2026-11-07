"""双风险标注的小规模评测（规则层）。

只测规则与人工标的答案是否一致，不调模型：
- 这个数当时该不该处理（固定阈值 + 患者自己的目标范围）
- 四档标签是否与人工判断一致
- `silent`（低了却没感觉）一条都不能漏

单一标注者、二十几条病例，结论只到「规则没有走样」这一层。
"""

from __future__ import annotations

from evals.dual_risk.cases import build_cases
from evals.dual_risk.runner import evaluate, summarize


def test_the_case_set_is_big_enough_to_mean_something() -> None:
    cases = build_cases()
    assert len(cases) >= 20
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.note for case in cases)
    assert all(case.said for case in cases)


def test_every_case_has_a_silent_neighbourhood() -> None:
    labels = {case.label for case in build_cases()}
    assert labels == {"both", "silent", "felt_only", "neither"}
    # silent 是这一档里最要紧的，样本不能只有一两条。
    assert sum(1 for case in build_cases() if case.label == "silent") >= 8


def test_the_rules_still_agree_with_the_hand_labels() -> None:
    summary = summarize([evaluate(case) for case in build_cases()])
    assert summary["label_accuracy"] >= 0.9
    assert summary["dangerous_accuracy"] >= 0.9


def test_no_silent_low_is_missed() -> None:
    results = [evaluate(case) for case in build_cases()]
    missed = [
        row["case_id"]
        for row in results
        if row["label"] == "silent" and row["predicted_label"] != "silent"
    ]
    assert missed == []


def test_the_boundary_cases_are_pinned() -> None:
    by_id = {case.case_id: evaluate(case) for case in build_cases()}
    # 54 mg/dL 与 250 mg/dL 是固定阈值的边界，改动了这里必须一起改测试。
    assert by_id["critical-boundary-low"]["predicted_physiological"] == "moderate"
    assert by_id["below-critical-low"]["predicted_physiological"] == "high"
    assert by_id["critical-boundary-high"]["predicted_physiological"] == "moderate"
    assert by_id["above-critical-high"]["predicted_physiological"] == "high"
    # 患者自己的目标范围优先于通用 70–180。
    assert by_id["wide-target-in-range"]["predicted_dangerous"] is False
    assert by_id["wide-target-below"]["predicted_dangerous"] is True
