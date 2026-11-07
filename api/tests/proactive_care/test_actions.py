"""「一句话之后给一件小事」的选法。

这些断言真正在意的是边界：不能一次给三件、不能给剂量、不能对日常问候也硬凑。
"""

from __future__ import annotations

from antang_api.models import ProactiveCareTaskKind
from antang_api.proactive_care.actions import ACTIONS, suggested_action


class FakeSignal:
    def __init__(self, kind: str) -> None:
        self.kind = kind


def test_a_plain_hello_never_carries_an_action() -> None:
    # 日常问候是「聊几句」，塞任务进去会变成每天提醒他做得不够。
    assert suggested_action(kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN) is None


def test_a_low_he_could_not_feel_brings_the_sugar_along() -> None:
    action = suggested_action(
        kind=ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP, silent_lows=1
    )

    assert action is not None
    assert action.code == "carry_sugar"
    assert "糖" in action.text


def test_an_old_danger_needs_no_action_ever_while_rewriting_the_text() -> None:
    # 他自己没感觉到，更要落到「手边有糖」这件具体事上。
    action = suggested_action(kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN)
    assert action is None


def test_each_signal_gets_the_action_that_fits_it() -> None:
    night = suggested_action(
        kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL, signal=FakeSignal("night_rechecking")
    )
    repeated = suggested_action(
        kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL, signal=FakeSignal("repeated_check")
    )
    avoidance = suggested_action(
        kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
        signal=FakeSignal("exercise_avoidance"),
    )

    assert night is not None and night.code == "bedside_snack"
    assert repeated is not None and repeated.code == "wait_ten_minutes"
    assert avoidance is not None and avoidance.code == "walk_with_someone"


def test_a_follow_up_without_a_silent_low_still_offers_one_small_thing() -> None:
    action = suggested_action(
        kind=ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP, silent_lows=0
    )

    assert action is not None
    assert action.code == "tell_one_person"


def test_an_unknown_signal_gets_nothing_rather_than_a_guess() -> None:
    assert (
        suggested_action(
            kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL, signal=FakeSignal("something_new")
        )
        is None
    )
    assert suggested_action(kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL) is None


def test_no_action_talks_like_a_prescription() -> None:
    for action in ACTIONS.values():
        for forbidden in ("单位", "毫克", "剂量", "处方"):
            assert forbidden not in action.text
        assert len(action.text) <= 40
        assert action.text.endswith("。")
