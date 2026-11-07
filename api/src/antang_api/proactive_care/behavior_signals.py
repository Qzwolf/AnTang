"""行为信号：从「已经记下来的次数与时间」里算出主动关怀的候选。

这一层只算、不发。理由写在 `docs/behavior-triggers.md` 里：行为触发的消息最容易变成
指责（「你今天又测了八次」），必须先看清算得对不对，再谈怎么开口。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import FearBehaviorLog, GlucoseReading

BehaviorSignalKind = Literal[
    "repeated_check", "exercise_avoidance", "night_rechecking"
]

# 「反复确认」看的是形状不是总量：一天量十几次分散在早中晚，和集中在两小时里，性质
# 不一样。6 次/3 小时已经是常规测糖频率（每天 4–10 次）之外，所以先拿它当起点。
REPEATED_CHECK_LOOKBACK = timedelta(hours=24)
REPEATED_CHECK_WINDOW = timedelta(hours=3)
REPEATED_CHECK_COUNT = 6

# 「回避运动」只用患者自己在对话里说过的话数，不从步数反推——步数下降可能是生病、
# 下雨、通勤变了。一次可能只是当天状态不好，两次以上才像模式。
EXERCISE_AVOIDANCE_LOOKBACK = timedelta(days=14)
EXERCISE_AVOIDANCE_COUNT = 2

# 「夜里反复确认」= 凌晨那一段醒来测。05:00 之后混进了正常起床，所以卡在 5 点。
NIGHT_START_HOUR = 0
NIGHT_END_HOUR = 5
NIGHT_RECHECK_LOOKBACK = timedelta(days=3)
NIGHT_RECHECK_NIGHTS = 2
NIGHT_RECHECK_COUNT = 2

SIGNAL_LABELS: dict[str, str] = {
    "repeated_check": "同一段三小时里测得比平时密",
    "exercise_avoidance": "这两周因为怕低血糖回避过运动",
    "night_rechecking": "最近几个夜里反复醒来测",
}


@dataclass(frozen=True)
class BehaviorSignal:
    """一条候选。它说明「数据长这个样子」，不说明「他焦虑」。"""

    kind: str
    label: str
    occurred: int
    window_days: int
    evidence: tuple[datetime, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label": self.label,
            "occurred": self.occurred,
            "window_days": self.window_days,
            "latest": self.evidence[-1].isoformat() if self.evidence else None,
        }


def _densest_window(
    times: list[datetime], window: timedelta
) -> list[datetime]:
    """在已排序的时间点里找落点最多的一段固定长度窗口。"""

    best: list[datetime] = []
    start = 0
    for end, moment in enumerate(times):
        while moment - times[start] > window:
            start += 1
        if end - start + 1 > len(best):
            best = times[start : end + 1]
    return best


def repeated_check(
    times: list[datetime], *, now: datetime
) -> BehaviorSignal | None:
    """最近 24 小时里，有没有一段三小时内测了 ≥ 6 次。"""

    recent = sorted(
        moment
        for moment in times
        if now - REPEATED_CHECK_LOOKBACK <= moment <= now
    )
    if len(recent) < REPEATED_CHECK_COUNT:
        return None
    densest = _densest_window(recent, REPEATED_CHECK_WINDOW)
    if len(densest) < REPEATED_CHECK_COUNT:
        return None
    return BehaviorSignal(
        kind="repeated_check",
        label=SIGNAL_LABELS["repeated_check"],
        occurred=len(densest),
        window_days=1,
        evidence=tuple(densest),
    )


def exercise_avoidance(
    logged_at: list[datetime], *, now: datetime
) -> BehaviorSignal | None:
    """最近 14 天里，他有没有两次以上说过「因为怕低血糖不跑步了」。"""

    recent = sorted(
        moment
        for moment in logged_at
        if now - EXERCISE_AVOIDANCE_LOOKBACK <= moment <= now
    )
    if len(recent) < EXERCISE_AVOIDANCE_COUNT:
        return None
    return BehaviorSignal(
        kind="exercise_avoidance",
        label=SIGNAL_LABELS["exercise_avoidance"],
        occurred=len(recent),
        window_days=14,
        evidence=tuple(recent),
    )


def _local_night(measured_at: datetime, zone: ZoneInfo) -> date | None:
    local = measured_at.astimezone(zone)
    if NIGHT_START_HOUR <= local.hour < NIGHT_END_HOUR:
        return local.date()
    return None


def night_rechecking(
    times: list[datetime], *, now: datetime, timezone_name: str
) -> BehaviorSignal | None:
    """最近 3 个夜里，有没有两个夜里各测了 ≥ 2 次（按患者本地时间判夜）。"""

    zone = ZoneInfo(timezone_name)
    since = now - NIGHT_RECHECK_LOOKBACK
    per_night: dict[date, list[datetime]] = {}
    for moment in times:
        if not (since <= moment <= now):
            continue
        night = _local_night(moment, zone)
        if night is None:
            continue
        per_night.setdefault(night, []).append(moment)
    busy_nights = [
        sorted(moments)
        for moments in per_night.values()
        if len(moments) >= NIGHT_RECHECK_COUNT
    ]
    if len(busy_nights) < NIGHT_RECHECK_NIGHTS:
        return None
    evidence = tuple(sorted(moment for moments in busy_nights for moment in moments))
    return BehaviorSignal(
        kind="night_rechecking",
        label=SIGNAL_LABELS["night_rechecking"],
        occurred=len(busy_nights),
        window_days=3,
        evidence=evidence,
    )


def _since(now: datetime, lookback: timedelta) -> datetime:
    return now - lookback


async def behavior_signals(
    session: AsyncSession,
    *,
    user_id,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> list[BehaviorSignal]:
    """算出这个患者此刻命中的候选（0–3 条）。撤回的读数不算。"""

    moment = now if now is not None else datetime.now(timezone.utc)

    reading_times = list(
        (
            await session.scalars(
                select(GlucoseReading.measured_at).where(
                    GlucoseReading.user_id == user_id,
                    GlucoseReading.revoked_at.is_(None),
                    GlucoseReading.measured_at
                    >= _since(moment, REPEATED_CHECK_LOOKBACK),
                    GlucoseReading.measured_at <= moment,
                )
            )
        ).all()
    )
    skipped = list(
        (
            await session.scalars(
                select(FearBehaviorLog.occurred_at).where(
                    FearBehaviorLog.user_id == user_id,
                    FearBehaviorLog.kind == "exercise_avoidance",
                    FearBehaviorLog.deleted_at.is_(None),
                    FearBehaviorLog.occurred_at
                    >= _since(moment, EXERCISE_AVOIDANCE_LOOKBACK),
                    FearBehaviorLog.occurred_at <= moment,
                )
            )
        ).all()
    )
    # 夜里那一段要看三天的读数，比「反复确认」的一天窗口更长，单独查一次。
    night_times = list(
        (
            await session.scalars(
                select(GlucoseReading.measured_at).where(
                    GlucoseReading.user_id == user_id,
                    GlucoseReading.revoked_at.is_(None),
                    GlucoseReading.measured_at
                    >= _since(moment, NIGHT_RECHECK_LOOKBACK),
                    GlucoseReading.measured_at <= moment,
                )
            )
        ).all()
    )

    found: list[BehaviorSignal] = []
    for signal in (
        repeated_check(reading_times, now=moment),
        exercise_avoidance(skipped, now=moment),
        night_rechecking(night_times, now=moment, timezone_name=timezone_name),
    ):
        if signal is not None:
            found.append(signal)
    return found


def render_behavior_signal_context(signals: list[BehaviorSignal]) -> str:
    """给模型看的一段：他最近的行为长什么样，以及不许怎么用它。"""

    if not signals:
        return ""
    lines = ["[他最近的行为痕迹]"]
    for signal in signals:
        lines.append(f"- {signal.label}（{signal.occurred} 次 / {signal.window_days} 天）")
    lines.append(
        "这不是诊断，也不要在回复里报次数或念成结论（「你三天里量了十八次」会变成指责）。"
        "他主动提起这类担心时，用它理解他为什么这么做；他没提，就先不说。"
    )
    return "\n".join(lines)


__all__ = [
    "BehaviorSignal",
    "BehaviorSignalKind",
    "EXERCISE_AVOIDANCE_COUNT",
    "EXERCISE_AVOIDANCE_LOOKBACK",
    "NIGHT_RECHECK_COUNT",
    "NIGHT_RECHECK_LOOKBACK",
    "NIGHT_RECHECK_NIGHTS",
    "NIGHT_END_HOUR",
    "NIGHT_START_HOUR",
    "REPEATED_CHECK_COUNT",
    "REPEATED_CHECK_LOOKBACK",
    "REPEATED_CHECK_WINDOW",
    "SIGNAL_LABELS",
    "behavior_signals",
    "render_behavior_signal_context",
    "exercise_avoidance",
    "night_rechecking",
    "repeated_check",
]
