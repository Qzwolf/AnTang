"""「这次为什么是我先开口」那句话的测试。

这句话给患者看，所以有三件事必须成立：①每类任务都有话说，不认识的来源也要退到
一句中性的；②句子里不能出现次数与数值——数值在我们这边是证据，在他那边就成了
被盯着的感觉；③时间只说「哪天 + 哪个时段」，不给钟点，也不把凌晨三点说成「今天夜里」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from antang_api.models import ProactiveCareTask, ProactiveCareTaskKind
from antang_api.proactive_care.reasons import (
    HEART_OTHER_REASON,
    ROUTINE_REASON,
    SIGNAL_FALLBACK_REASON,
    message_reason,
)

SHANGHAI = timezone(timedelta(hours=8))


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SHANGHAI)


def task(kind: ProactiveCareTaskKind, evidence: dict[str, object] | None = None, **extra):
    return ProactiveCareTask(kind=kind, health_evidence=evidence or {}, **extra)


def test_a_routine_check_in_names_the_part_of_the_day_we_usually_talk():
    reason = message_reason(
        task(ProactiveCareTaskKind.ROUTINE_CHECK_IN),
        now=local(2026, 11, 5, 8, 30),
    )

    assert reason == "早上这会儿，我们平时会聊几句"


def test_a_routine_check_in_in_the_deep_night_does_not_pretend_we_chat_then():
    reason = message_reason(
        task(ProactiveCareTaskKind.ROUTINE_CHECK_IN),
        now=local(2026, 11, 5, 23, 10),
    )

    assert reason == ROUTINE_REASON


def test_a_plan_follow_up_reminds_him_of_the_promise_not_of_us():
    due = local(2026, 11, 5, 20, 0)
    reason = message_reason(
        task(ProactiveCareTaskKind.PLAN_FOLLOW_UP, due_at=due),
        now=local(2026, 11, 5, 20, 5),
    )

    assert reason == "你答应过今天回来说一声"


def test_a_heart_rate_event_says_when_it_happened():
    evidence: dict[str, object] = {
        "direction": "low",
        "sample_start": local(2026, 11, 4, 23, 10).isoformat(),
    }
    reason = message_reason(
        task(ProactiveCareTaskKind.HEALTH_EVENT, evidence),
        now=local(2026, 11, 5, 9, 0),
    )

    assert reason == "昨天深夜手环记录里有一段心率偏低"


def test_a_heart_rate_event_after_midnight_belongs_to_last_night():
    evidence: dict[str, object] = {
        "direction": "high",
        "sample_start": local(2026, 11, 5, 2, 30).isoformat(),
    }
    reason = message_reason(
        task(ProactiveCareTaskKind.HEALTH_EVENT, evidence),
        now=local(2026, 11, 5, 8, 0),
    )

    assert reason == "昨天夜里手环记录里有一段心率偏高"


def test_a_heart_rate_event_without_a_time_still_says_something():
    reason = message_reason(
        task(ProactiveCareTaskKind.HEALTH_EVENT, {"direction": "low"}),
        now=local(2026, 11, 5, 9, 0),
    )

    assert reason == "手环记录里有一段心率偏低"


def test_an_unknown_direction_does_not_invent_a_direction():
    reason = message_reason(
        task(ProactiveCareTaskKind.HEALTH_EVENT, {"direction": "sideways"}),
        now=local(2026, 11, 5, 9, 0),
    )

    assert reason == HEART_OTHER_REASON


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ("night_rechecking", "这几天的夜里你常常醒"),
        ("exercise_avoidance", "最近两周你好像在躲着运动"),
        ("something_new", SIGNAL_FALLBACK_REASON),
        (None, SIGNAL_FALLBACK_REASON),
    ],
)
def test_a_behaviour_signal_borrows_the_label_he_would_recognise(signal, expected):
    evidence: dict[str, object] = {} if signal is None else {"signal": signal}

    assert (
        message_reason(
            task(ProactiveCareTaskKind.BEHAVIOR_SIGNAL, evidence),
            now=local(2026, 11, 5, 9, 0),
        )
        == expected
    )


def test_a_dense_day_of_reading_says_which_part_of_the_day():
    evidence: dict[str, object] = {
        "signal": "repeated_check",
        "latest": local(2026, 11, 5, 15, 40).isoformat(),
    }
    reason = message_reason(
        task(ProactiveCareTaskKind.BEHAVIOR_SIGNAL, evidence),
        now=local(2026, 11, 5, 16, 0),
    )

    assert reason == "今天下午有一段时间测得比平时密"


@pytest.mark.parametrize(
    "kind",
    [
        ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        ProactiveCareTaskKind.PLAN_FOLLOW_UP,
        ProactiveCareTaskKind.HEALTH_EVENT,
        ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
    ],
)
def test_no_reason_ever_carries_numbers_or_counts(kind):
    evidence: dict[str, object] = {
        "direction": "low",
        "signal": "repeated_check",
        "sample_start": local(2026, 11, 5, 2, 5).isoformat(),
        "latest": local(2026, 11, 5, 10, 0).isoformat(),
    }
    reason = message_reason(
        task(kind, evidence, due_at=local(2026, 11, 5, 9, 0)),
        now=local(2026, 11, 5, 10, 0),
    )

    assert not any(character.isdigit() for character in reason)
    assert "次" not in reason


def test_the_same_task_reads_the_same_way_twice():
    evidence: dict[str, object] = {"direction": "high", "sample_start": local(2026, 11, 5, 9, 0).isoformat()}
    once = message_reason(
        task(ProactiveCareTaskKind.HEALTH_EVENT, evidence),
        now=local(2026, 11, 5, 11, 0),
    )
    again = message_reason(
        task(ProactiveCareTaskKind.HEALTH_EVENT, evidence),
        now=local(2026, 11, 5, 11, 0),
    )

    assert once == again == "今天上午手环记录里有一段心率偏高"
