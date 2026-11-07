"""两稿比对评测集的回归测试。

测三件事：集子本身有覆盖（该放行的、该拦下的都有足够条数）、
跑出来全对、以及那个「中文数字看不见」的缺口被集子钉在明处。
"""

from __future__ import annotations

from evals.two_draft.cases import build_cases
from evals.two_draft.runner import evaluate, summarize


def test_the_set_covers_both_outcomes() -> None:
    cases = build_cases()

    assert len(cases) >= 12
    assert len({case.case_id for case in cases}) == len(cases)
    same = [case for case in cases if case.same]
    changed = [case for case in cases if not case.same]
    assert len(same) >= 5
    assert len(changed) >= 5
    for case in cases:
        assert case.first.strip()
        assert case.second.strip()
        assert case.why.strip()


def test_every_case_is_judged_the_way_a_person_would() -> None:
    results = [evaluate(case) for case in build_cases()]
    summary = summarize(results)

    assert summary["agreement"] == 1.0, summary["misses"]
    assert summary["blocked_rate"] == 1.0


def test_a_changed_number_always_comes_back_with_the_difference() -> None:
    results = [evaluate(case) for case in build_cases()]

    changed = [item for item in results if not item["predicted_same"]]
    assert changed
    for item in changed:
        assert item["mismatch"] is not None
        assert "第二稿改了数字" in item["mismatch"]


def test_chinese_numerals_are_still_out_of_reach() -> None:
    """这一关只认阿拉伯数字，集子里专门留了这条把缺口写明。"""

    case = next(case for case in build_cases() if case.case_id == "hidden-digit-in-word")
    result = evaluate(case)

    assert result["predicted_same"] is False
    assert result["mismatch"] is not None
    assert "只有第一稿有：1" in result["mismatch"]
