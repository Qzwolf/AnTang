"""一句话之后，再给一件今天就能做的小事。

主动关怀最容易滑向「注意休息」「保持好心情」这类空话：听起来温和，实际上把该
做的事又交回给他自己。这里放的都是具体的、十分钟内能做完、不需要额外设备的
动作，一次只说一件。

三条边界（写在这里是为了以后加动作时别再犯）：
- 不给剂量、不给运动处方、不做诊断——那是医生的事，这里只帮着把小事变具体。
- 不要求他「多测几次」：对怕低血糖的人来说，多测往往正是问题本身。
- 一次只给一件。给三件等于没给，他一件也想不起做。
"""

from __future__ import annotations

from dataclasses import dataclass

from antang_api.models import ProactiveCareTaskKind


@dataclass(frozen=True)
class MicroAction:
    """一个具体动作，附一句「为什么现在说这个」。"""

    code: str
    text: str
    why: str


ACTIONS: dict[str, MicroAction] = {
    "carry_sugar": MicroAction(
        code="carry_sugar",
        text="出门前在口袋里放两片糖，到地方先摸一下还在不在。",
        why="他说过低下去的时候身上没给信号",
    ),
    "bedside_snack": MicroAction(
        code="bedside_snack",
        text="床头放一小包饼干，夜里醒了先吃再测，不用下床去找。",
        why="他这几个夜里反复起来测",
    ),
    "wait_ten_minutes": MicroAction(
        code="wait_ten_minutes",
        text="下一次想测的时候，先看表等十分钟，再去拿血糖仪。",
        why="他最近测得很密",
    ),
    "tell_one_person": MicroAction(
        code="tell_one_person",
        text="今晚找一个人说一句「我最近有点怕低血糖」。",
        why="他很怕，但一直自己扛着",
    ),
    "walk_with_someone": MicroAction(
        code="walk_with_someone",
        text="饭后找个人一起走十分钟，回来再测一次。",
        why="他最近在躲着运动",
    ),
}


def _signal_code(signal: object) -> str | None:
    if signal is None:
        return None
    kind = getattr(signal, "kind", None)
    if isinstance(kind, str):
        return kind
    return None


def suggested_action(
    *,
    kind: ProactiveCareTaskKind,
    signal: object | None = None,
    silent_lows: int = 0,
) -> MicroAction | None:
    """按「这次为什么开口」挑一件小事；挑不出来就不硬凑。"""

    if kind is ProactiveCareTaskKind.ROUTINE_CHECK_IN:
        return None

    signal_kind = _signal_code(signal)
    if silent_lows > 0 and kind is not ProactiveCareTaskKind.BEHAVIOR_SIGNAL:
        # 双风险回访和日常问候都可能用上「带着糖」这件小事。
        return ACTIONS["carry_sugar"]
    if signal_kind == "night_rechecking":
        return ACTIONS["bedside_snack"]
    if signal_kind == "repeated_check":
        return ACTIONS["wait_ten_minutes"]
    if signal_kind == "exercise_avoidance":
        return ACTIONS["walk_with_someone"]
    if kind is ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP:
        return ACTIONS["tell_one_person"]
    return None


__all__ = ["ACTIONS", "MicroAction", "suggested_action"]
