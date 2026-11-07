"""把「上周三晚饭前那次」这种话，折成一个时间窗口 + 场景筛选。

「用你的话问你的血糖」原来只有固定参数：按天数取读数。患者真会说的却是
「上周三晚饭前那次多少」「这两天的夜里是不是偏低」——这类话里有两个东西要拆开：
一段时间，和一类场景。

这里只做确定性的解析，不调模型：模型负责把患者的话交给它，解析结果再交给数据库。
解析不出来的部分不猜，留在 `notes` 里，让模型知道「这句我只认出了这一段」——
宁可承认没听懂，也不要悄悄换成「最近七天」。

支持的说法（写在这里，也写在评测集里）：

- 单个日子：今天、昨天、前天、大前天
- 星期：上周三、周三、本周一
- 区间：上周、这周、上个月、这个月、最近三天、过去五天、上周一到上周五
- 时段：早上、上午、中午、下午、傍晚、晚上、夜里、半夜、凌晨
- 场景：空腹、早饭前、午饭前、晚饭前、餐前、饭后、餐后、睡前、夜间
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"

# 星期几的字：周一 = 0。
_WEEKDAYS = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}

# 时段 → (开始小时, 结束小时)。结束是开区间，所以 23 点算「晚上」到 23:00。
_DAYPARTS: tuple[tuple[tuple[str, ...], int, int], ...] = (
    (("早上", "早晨", "清晨"), 5, 9),
    (("上午",), 9, 11),
    (("中午",), 11, 13),
    (("下午",), 13, 17),
    (("傍晚",), 17, 19),
    (("晚上",), 19, 23),
    (("夜里", "半夜", "凌晨", "夜间"), 0, 5),
)

# 场景词 → glucose_readings.context 的取值。
_CONTEXTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("空腹",), "fasting"),
    (("早饭前", "早餐前", "午饭前", "午餐前", "晚饭前", "晚餐前", "餐前"), "pre_meal"),
    (("早饭后", "早餐后", "午饭后", "午餐后", "晚饭后", "晚餐后", "餐后", "饭后"), "post_meal"),
    (("睡前",), "bedtime"),
)

# 「晚上」这种说法同时是时段与场景：夜里作场景时按 night 存，时段仍然取 0–5 点。
_NIGHT_CONTEXT = "night"

# 说到哪顿饭，就按时段收窄到那顿饭附近：早饭 5–9、午饭 11–13、晚饭 17–19。
_MEAL_HOURS: tuple[tuple[tuple[str, ...], tuple[int, int]], ...] = (
    (("早饭", "早餐"), (5, 9)),
    (("午饭", "午餐"), (11, 13)),
    (("晚饭", "晚餐"), (17, 19)),
)

_CN_NUMBERS = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass(frozen=True)
class GlucoseQuery:
    """一句话折出来的查询条件。"""

    start: datetime
    end: datetime
    contexts: tuple[str, ...] = ()
    label: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        """给模型看的一句说明：这段时间里、这几类场景。"""

        span = f"{_fmt(self.start)} 到 {_fmt(self.end)}"
        if not self.contexts:
            return f"{self.label}（{span}）"
        return f"{self.label}（{span}，只看 {('、'.join(self.contexts))}）"


def _fmt(value: datetime) -> str:
    return value.strftime("%m-%d %H:%M")


def _day_start(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=zone)


def _day_end(day: date, zone: ZoneInfo) -> datetime:
    return _day_start(day + timedelta(days=1), zone)


def _daypart(text: str) -> tuple[int, int] | None:
    # 「午饭前」「晚饭后」里的那顿饭本身就说明了时段，比泛指的「下午」更准。
    for names, hours in _MEAL_HOURS:
        if any(name in text for name in names):
            return hours
    for names, start_hour, end_hour in _DAYPARTS:
        if any(name in text for name in names):
            return start_hour, end_hour
    return None


def _context(text: str) -> str | None:
    for names, context in _CONTEXTS:
        if any(name in text for name in names):
            return context
    if any(name in text for name in ("夜里", "半夜", "凌晨", "夜间")):
        return _NIGHT_CONTEXT
    return None


def _number(text: str) -> int | None:
    """取出「三天」「5 天」里的数字。"""

    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
        elif digits:
            break
    if digits:
        return int(digits)
    for char in text:
        if char in _CN_NUMBERS:
            return _CN_NUMBERS[char]
    return None


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _last_weekday(today: date, weekday: int) -> date:
    """上一个星期几：今天之后的那一天不算（说「上周三」时今天可能已经是周四）。"""

    this_week = _week_start(today)
    return this_week - timedelta(days=7) + timedelta(days=weekday)


def _recent_weekday(today: date, weekday: int) -> date:
    """最近的那个星期几（含今天）。"""

    delta = (today.weekday() - weekday) % 7
    return today - timedelta(days=delta)


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _previous_month_start(day: date) -> date:
    first = _month_start(day)
    return _month_start(first - timedelta(days=1))


def _parse_day_span(text: str, today: date) -> tuple[date, date, str] | None:
    """先认日子，认不出返回 None。区间右端是「最后一天」（含当天）。"""

    if "大前天" in text:
        return today - timedelta(days=3), today - timedelta(days=3), "大前天"
    if "前天" in text:
        return today - timedelta(days=2), today - timedelta(days=2), "前天"
    if "昨天" in text:
        return today - timedelta(days=1), today - timedelta(days=1), "昨天"
    if "今天" in text or "今日" in text:
        return today, today, "今天"

    # 上周一到上周五：两个「上周X」连用。
    if "上周" in text:
        found = [
            (index, _last_weekday(today, _WEEKDAYS[char]))
            for index, char in enumerate(text)
            if char in _WEEKDAYS
        ]
        if len(found) >= 2:
            days = sorted(day for _index, day in found[:2])
            return days[0], days[1], "上周那两天"
        if found:
            day = found[0][1]
            return day, day, f"上周{text[found[0][0]]}"
        monday = _week_start(today) - timedelta(days=7)
        return monday, monday + timedelta(days=6), "上周"

    if "这周" in text or "本周" in text:
        return _week_start(today), today, "这周"

    for index, char in enumerate(text):
        if char in _WEEKDAYS and (
            index == 0 or text[index - 1] in ("周", "星期", "礼拜")
        ):
            day = _recent_weekday(today, _WEEKDAYS[char])
            prefix = text[max(0, index - 2) : index + 1]
            return day, day, prefix or f"周{char}"

    if "上个月" in text:
        start = _previous_month_start(today)
        return start, _month_start(today) - timedelta(days=1), "上个月"
    if "这个月" in text or "本月" in text:
        return _month_start(today), today, "这个月"

    for marker in ("最近", "过去", "近"):
        if marker in text:
            count = _number(text[text.find(marker) + len(marker) :])
            if count:
                return today - timedelta(days=count - 1), today, f"最近 {count} 天"
    return None


def _apply_daypart(
    start_day: date, end_day: date, daypart: tuple[int, int] | None, zone: ZoneInfo
) -> tuple[datetime, datetime]:
    if daypart is None:
        return _day_start(start_day, zone), _day_end(end_day, zone)
    start_hour, end_hour = daypart
    if start_hour == 0 and end_hour == 5:
        # 夜里跨天：从第一天 00:00 起，到最后一夜的 05:00 为止
        # （「最近三天的夜里」= 三个夜，不是三个白天）。
        return (
            datetime.combine(start_day, time(0, 0), tzinfo=zone),
            datetime.combine(end_day, time(5, 0), tzinfo=zone),
        )
    return (
        datetime.combine(start_day, time(start_hour, 0), tzinfo=zone),
        datetime.combine(end_day, time(end_hour, 0), tzinfo=zone),
    )


def parse_glucose_phrase(
    phrase: str,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> GlucoseQuery | None:
    """把一句话折成查询条件；认不出日子就返回 None（让调用方说不知道）。

    时段与场景是「顺带认出多少算多少」，认不出不影响日子；认出了日子但认不出
    时段，就是那天的整天。
    """

    text = (phrase or "").strip()
    if not text:
        return None
    zone = ZoneInfo(timezone_name)
    moment = (now or datetime.now(zone)).astimezone(zone)
    today = moment.date()

    span = _parse_day_span(text, today)
    if span is None:
        return None
    start_day, end_day, label = span

    notes: list[str] = []
    daypart = _daypart(text)
    context = _context(text)
    contexts: tuple[str, ...] = ()
    if context is not None:
        contexts = (context,)
    start, end = _apply_daypart(start_day, end_day, daypart, zone)
    if end > moment:
        # 「今天夜里」「这周」还没走完：结束时间截到此刻，别把未来算进去。
        end = min(end, moment)
        notes.append("这段时间还没走完，只取到此刻。")

    if daypart is None and context is None and start_day != end_day:
        notes.append("没说具体时段，取的是整段时间。")
    if daypart is not None and context is None and daypart[0] == 0:
        # 「夜里」既是时段也可能是场景，数据库里存的是 night。
        contexts = (_NIGHT_CONTEXT,)
    if start >= end:
        return None
    return GlucoseQuery(
        start=start,
        end=end,
        contexts=contexts,
        label=label,
        notes=tuple(notes),
    )


__all__ = ["GlucoseQuery", "parse_glucose_phrase"]
