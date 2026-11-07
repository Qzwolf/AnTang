"""给「这次为什么是我先开口」写一句人话。

这句话由程序生成，不由模型写：它要说明的是任务为什么被排出来，模型能写得更自然，
但也可能写成诊断、写成指责，或者干脆编一个原因。事实边界放程序这边，措辞留给界面。

句子可以带「什么时候」，但不带「几次」「多少」：

- 带时间是因为它让这句话有据可查——「今天下午手环上有一段心率偏高」比「手环上有一段
  心率偏高」更像在说一件发生过的事，患者能自己回想那会儿在做什么。
- 不带次数/数值是因为数值在我们这边是证据，摆到他眼前就变成了被盯着看；而且 3 次还是
  5 次对他的处境没有区别，只会变成要达标的东西。
- 时间的粒度只到「哪天 + 哪个时段」，不给钟点：钟点精确到分钟会让人误以为我们在实时监控，
  而它其实是一段数值的汇总。

行为痕迹那几句仍然刻意不用 `behavior_signals.SIGNAL_LABELS`：那些是给模型看的口径
（含窗口与条件），这里要的是患者自己能认出来的一句话。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from antang_api.models import ProactiveCareTask, ProactiveCareTaskKind

DEFAULT_TIMEZONE = "Asia/Shanghai"

# 没有时间可用时的兜底句（也用于「这会儿是深夜」这种不适合按点说话的时段）。
ROUTINE_REASON = "到了我们平时说说话的时间"
PLAN_REASON = "你答应过这时候回来说一声"
HEART_OTHER_REASON = "手环记录里有一段和平时不太一样"
SIGNAL_FALLBACK_REASON = "这几天的记录有点不一样"

_DAY_PARTS: tuple[tuple[int, int, str], ...] = (
    (5, 9, "早上"),
    (9, 11, "上午"),
    (11, 13, "中午"),
    (13, 17, "下午"),
    (17, 19, "傍晚"),
    (19, 22, "晚上"),
    (22, 24, "深夜"),
)

# 夜里 0–5 点归到「昨天」那一夜，否则凌晨三点说「今天夜里」会让人以为是在说以后。
_NIGHT_END_HOUR = 5


def _day_part(hour: int) -> str:
    for start, end, name in _DAY_PARTS:
        if start <= hour < end:
            return name
    return "夜里"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _day_word(moment: datetime, now: datetime) -> str:
    days = (now.date() - moment.date()).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days == 2:
        return "前天"
    return "前几天"


def _when(value: object, now: datetime, zone: ZoneInfo) -> str:
    """把某一刻说成「今天下午」这样的词；拿不到时间就返回空串。"""

    moment = _as_datetime(value)
    if moment is None:
        return ""
    local = moment.astimezone(zone)
    if local.hour < _NIGHT_END_HOUR:
        return f"{_day_word(local - timedelta(days=1), now)}{_day_part(local.hour)}"
    return f"{_day_word(local, now)}{_day_part(local.hour)}"


def _routine_reason(now: datetime) -> str:
    part = _day_part(now.hour)
    if now.hour < _NIGHT_END_HOUR or now.hour >= 22:
        # 深夜不适合说「这会儿我们平时会聊几句」——那个点我们本来就不说话。
        return ROUTINE_REASON
    return f"{part}这会儿，我们平时会聊几句"


def _health_reason(task: ProactiveCareTask, now: datetime, zone: ZoneInfo) -> str:
    evidence = task.health_evidence or {}
    direction = str(evidence.get("direction"))
    if direction == "high":
        subject = "有一段心率偏高"
    elif direction == "low":
        subject = "有一段心率偏低"
    else:
        subject = "有一段和平时不太一样"
    when = _when(evidence.get("sample_start") or evidence.get("received_at"), now, zone)
    return f"{when}手环记录里{subject}" if when else f"手环记录里{subject}"


def _signal_reason(task: ProactiveCareTask, now: datetime, zone: ZoneInfo) -> str:
    evidence = task.health_evidence or {}
    signal = str(evidence.get("signal"))
    if signal == "repeated_check":
        when = _when(evidence.get("latest"), now, zone)
        return f"{when}有一段时间测得比平时密" if when else "这几天测得比平时密"
    if signal == "exercise_avoidance":
        # 窗口本身就是「这两周」，不再叠加日期词，否则句子会变长且信息重复。
        return "最近两周你好像在躲着运动"
    if signal == "night_rechecking":
        return "这几天的夜里你常常醒"
    return SIGNAL_FALLBACK_REASON


def _dual_risk_reason(task: ProactiveCareTask, now: datetime, zone: ZoneInfo) -> str:
    evidence = task.health_evidence or {}
    when = _when(evidence.get("measured_at"), now, zone)
    # 只说「那一晚」和「后来」：不复述数值，也不说「你没察觉到」——那句话本身
    # 就是他没做到的事，放在原因里像在提醒他的失误。句子刻意不带「次」字，
    # 否则读起来像在记数。
    if when:
        return f"{when}低了却没什么感觉，想问问后来怎么样"
    return "前几天有一晚低了却没什么感觉，想问问后来怎么样"


def message_reason(
    task: ProactiveCareTask,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> str:
    """按任务来源给出一句可显示的原因；不认识的来源退回一句中性的。"""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    zone = _zone(timezone_name)
    local_now = current.astimezone(zone)

    if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
        return _routine_reason(local_now)
    if task.kind == ProactiveCareTaskKind.PLAN_FOLLOW_UP:
        when = _day_word(
            (_as_datetime(task.due_at) or current).astimezone(zone),
            local_now,
        )
        return f"你答应过{when}回来说一声" if when else PLAN_REASON
    if task.kind == ProactiveCareTaskKind.HEALTH_EVENT:
        return _health_reason(task, local_now, zone)
    if task.kind == ProactiveCareTaskKind.BEHAVIOR_SIGNAL:
        return _signal_reason(task, local_now, zone)
    if task.kind == ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP:
        return _dual_risk_reason(task, local_now, zone)
    return SIGNAL_FALLBACK_REASON
