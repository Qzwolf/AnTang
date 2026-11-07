"""「用你的话问你的血糖」的评测集。

每一条是一句患者可能真的会说出口的话（不是我们教程里的句子），加上它应该折成
什么。期望值都相对同一个基准时刻（2026-11-07 周六 10:00，+08:00）写死，所以这
份集子在任何一天跑都一样。

集子的作用不是证明「准确率 100%」，而是把「哪些说法认得出、认成什么」钉住：
改规则时哪一条变了，会立刻在这里露出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 7, 10, 0, tzinfo=ZONE)


@dataclass(frozen=True)
class GlucoseQueryCase:
    """一句话 + 它应该折成的窗口（日期时间按 `%Y-%m-%d %H:%M` 写）。"""

    case_id: str
    phrase: str
    start: str
    end: str
    contexts: tuple[str, ...] = ()
    # 说清这句为什么这么折——后来的人看集子时先看这里。
    why: str = ""


def build_cases() -> tuple[GlucoseQueryCase, ...]:
    return (
        GlucoseQueryCase(
            case_id="today",
            phrase="今天这会儿血糖多少",
            start="2026-11-07 00:00",
            end="2026-11-07 10:00",
            why="今天还没走完，窗口截到此刻。",
        ),
        GlucoseQueryCase(
            case_id="yesterday",
            phrase="昨天怎么样",
            start="2026-11-06 00:00",
            end="2026-11-07 00:00",
            why="整天。",
        ),
        GlucoseQueryCase(
            case_id="yesterday_dinner_before",
            phrase="昨天晚饭前那次多少",
            start="2026-11-06 17:00",
            end="2026-11-06 19:00",
            contexts=("pre_meal",),
            why="说了哪顿饭，就按时段收窄，并且只看餐前。",
        ),
        GlucoseQueryCase(
            case_id="last_wednesday_lunch_before",
            phrase="上周三午饭前那次",
            start="2026-10-28 11:00",
            end="2026-10-28 13:00",
            contexts=("pre_meal",),
            why="上周三 = 10-28；午饭前 = 11–13 点 + 餐前。",
        ),
        GlucoseQueryCase(
            case_id="wednesday_recent",
            phrase="周三晚上那次",
            start="2026-11-04 19:00",
            end="2026-11-04 23:00",
            why="只说星期几，取最近那个周三（含今天）。",
        ),
        GlucoseQueryCase(
            case_id="last_week",
            phrase="上周整体是不是偏低",
            start="2026-10-26 00:00",
            end="2026-11-02 00:00",
            why="上周 = 上一整周。",
        ),
        GlucoseQueryCase(
            case_id="last_week_fasting",
            phrase="上周空腹那几个数",
            start="2026-10-26 00:00",
            end="2026-11-02 00:00",
            contexts=("fasting",),
            why="认出了场景，就不猜时段。",
        ),
        GlucoseQueryCase(
            case_id="two_weekdays",
            phrase="上周一到上周五",
            start="2026-10-26 00:00",
            end="2026-10-31 00:00",
            why="两个「上周X」连用当区间，右端含当天。",
        ),
        GlucoseQueryCase(
            case_id="recent_three_days_night",
            phrase="最近三天的夜里是不是偏低",
            start="2026-11-05 00:00",
            end="2026-11-07 05:00",
            contexts=("night",),
            why="夜里既收窄时段（0–5 点）也当场景。",
        ),
        GlucoseQueryCase(
            case_id="recent_five_days",
            phrase="最近5天",
            start="2026-11-03 00:00",
            end="2026-11-07 10:00",
            why="数字与中文数字都认。",
        ),
        GlucoseQueryCase(
            case_id="this_week",
            phrase="这周几次低血糖",
            start="2026-11-02 00:00",
            end="2026-11-07 10:00",
            why="这周从周一算，到此刻。",
        ),
        GlucoseQueryCase(
            case_id="last_month",
            phrase="上个月的平均",
            start="2026-10-01 00:00",
            end="2026-11-01 00:00",
            why="上个月 = 上一个自然月。",
        ),
        GlucoseQueryCase(
            case_id="this_month",
            phrase="这个月睡前那次",
            start="2026-11-01 00:00",
            end="2026-11-07 10:00",
            contexts=("bedtime",),
            why="这个月到今天，只看睡前。",
        ),
        GlucoseQueryCase(
            case_id="yesterday_morning",
            phrase="昨天早上那个数",
            start="2026-11-06 05:00",
            end="2026-11-06 09:00",
            why="早上 = 5–9 点。",
        ),
        GlucoseQueryCase(
            case_id="day_before_after_meal",
            phrase="前天饭后两小时",
            start="2026-11-05 00:00",
            end="2026-11-06 00:00",
            contexts=("post_meal",),
            why="没说是哪顿饭，就整天 + 餐后，不猜时段。",
        ),
        GlucoseQueryCase(
            case_id="last_week_friday_morning",
            phrase="上周五早上空腹",
            start="2026-10-30 05:00",
            end="2026-10-30 09:00",
            contexts=("fasting",),
            why="空腹优先于「早上」当场景，时段仍是早上。",
        ),
        GlucoseQueryCase(
            case_id="before_yesterday",
            phrase="大前天的",
            start="2026-11-04 00:00",
            end="2026-11-05 00:00",
            why="大前天。",
        ),
        GlucoseQueryCase(
            case_id="night_yesterday",
            phrase="昨天半夜醒了测的那次",
            start="2026-11-06 00:00",
            end="2026-11-06 05:00",
            contexts=("night",),
            why="半夜 = 0–5 点 + night。",
        ),
    )


__all__ = ["GlucoseQueryCase", "NOW", "ZONE", "build_cases"]
