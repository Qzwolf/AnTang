"""评测集本身就是回归测试：集子里的话，规则必须一条都对得上。"""

from __future__ import annotations

from evals.glucose_query.cases import build_cases
from evals.glucose_query.runner import evaluate, summarize


def test_the_eval_set_is_big_enough_to_mean_something() -> None:
    cases = build_cases()
    assert len(cases) >= 15
    assert len({case.case_id for case in cases}) == len(cases)
    # 每一句都要说清它为什么这么折，否则后来的人不知道能不能改。
    assert all(case.why for case in cases)


def test_every_phrase_in_the_set_still_folds_the_same_way() -> None:
    results = [evaluate(case) for case in build_cases()]
    report = summarize(results)
    assert report["understood_rate"] == 1.0
    assert report["window_accuracy"] == 1.0
    assert report["context_accuracy"] == 1.0
    assert report["misses"] == []


def test_the_set_covers_each_kind_of_expression() -> None:
    phrases = " ".join(case.phrase for case in build_cases())
    for marker in ("昨天", "上周", "最近", "这周", "上个月", "夜里", "睡前", "空腹", "饭前"):
        assert marker in phrases, f"评测集里没有「{marker}」这种说法"

    contexts = {context for case in build_cases() for context in case.contexts}
    assert {"pre_meal", "post_meal", "fasting", "bedtime", "night"} <= contexts
