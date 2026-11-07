"""「上周三晚饭前那次」这种话，能不能折成一个能查的时间窗口。

用例都钉在 2026-11-07（周六）10:00（+08:00）这一天，所以期望值是写死的日期，
不随跑测试的那天变化。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from antang_api.health_profile.query_parse import parse_glucose_phrase

ZONE = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 7, 10, 0, tzinfo=ZONE)


def _parse(phrase: str):
    return parse_glucose_phrase(phrase, now=NOW, timezone_name="Asia/Shanghai")


def _span(phrase: str) -> tuple[str, str]:
    query = _parse(phrase)
    assert query is not None
    return (
        query.start.strftime("%Y-%m-%d %H:%M"),
        query.end.strftime("%Y-%m-%d %H:%M"),
    )


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("今天", ("2026-11-07 00:00", "2026-11-07 10:00")),
        ("昨天", ("2026-11-06 00:00", "2026-11-07 00:00")),
        ("前天", ("2026-11-05 00:00", "2026-11-06 00:00")),
        ("大前天", ("2026-11-04 00:00", "2026-11-05 00:00")),
        ("上周三", ("2026-10-28 00:00", "2026-10-29 00:00")),
        ("周三", ("2026-11-04 00:00", "2026-11-05 00:00")),
        ("上周", ("2026-10-26 00:00", "2026-11-02 00:00")),
        ("这周", ("2026-11-02 00:00", "2026-11-07 10:00")),
        ("上个月", ("2026-10-01 00:00", "2026-11-01 00:00")),
        ("这个月", ("2026-11-01 00:00", "2026-11-07 10:00")),
        ("最近三天", ("2026-11-05 00:00", "2026-11-07 10:00")),
        ("最近5天", ("2026-11-03 00:00", "2026-11-07 10:00")),
    ],
)
def test_the_day_part_of_a_phrase(phrase: str, expected: tuple[str, str]) -> None:
    assert _span(phrase) == expected


def test_a_daypart_narrows_the_same_day() -> None:
    assert _span("昨天晚饭前") == ("2026-11-06 17:00", "2026-11-06 19:00")
    assert _span("前天早上") == ("2026-11-05 05:00", "2026-11-05 09:00")
    assert _span("昨天下午") == ("2026-11-06 13:00", "2026-11-06 17:00")


def test_a_weekday_with_a_daypart() -> None:
    assert _span("上周三午饭前") == ("2026-10-28 11:00", "2026-10-28 13:00")
    assert _span("上周五早上") == ("2026-10-30 05:00", "2026-10-30 09:00")


def test_a_range_of_two_weekdays() -> None:
    assert _span("上周一到上周五") == ("2026-10-26 00:00", "2026-10-31 00:00")


def test_the_scene_comes_along_separately() -> None:
    query = _parse("昨天晚饭前")
    assert query is not None
    assert query.contexts == ("pre_meal",)
    assert _parse("上周空腹") is not None
    assert _parse("上周空腹").contexts == ("fasting",)  # type: ignore[union-attr]
    assert _parse("前天饭后").contexts == ("post_meal",)  # type: ignore[union-attr]
    assert _parse("昨天睡前").contexts == ("bedtime",)  # type: ignore[union-attr]


def test_the_night_is_both_a_span_and_a_scene() -> None:
    query = _parse("昨天夜里")
    assert query is not None
    assert query.start.strftime("%H:%M") == "00:00"
    assert query.end.strftime("%H:%M") == "05:00"
    assert query.contexts == ("night",)


def test_a_phrase_without_a_day_is_not_guessed() -> None:
    for phrase in ("", "   ", "血糖怎么样", "那两个数"):
        assert _parse(phrase) is None


def test_the_label_and_the_note_are_written_for_a_human() -> None:
    query = _parse("上周三午饭前")
    assert query is not None
    assert "上周三" in query.label
    assert "pre_meal" in query.describe()

    rule = _parse("上周")
    assert rule is not None
    assert any("整段" in note for note in rule.notes)
