"""血糖记录的读写与撤回。

只做存取与最基础的汇总，不做任何临床判断：什么算低、什么时候该提醒，
都在别处按阈值决定，这里只负责把数字和时间如实保存。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models.glucose import GlucoseReading


async def record_glucose_reading(
    session: AsyncSession,
    *,
    user_id: UUID,
    value_mg_dl: int,
    measured_at: datetime,
    context: str = "other",
    source: str = "manual",
    note: str | None = None,
) -> GlucoseReading:
    reading = GlucoseReading(
        user_id=user_id,
        value_mg_dl=value_mg_dl,
        measured_at=measured_at,
        context=context,
        source=source,
        note=note,
    )
    session.add(reading)
    await session.flush()
    return reading


async def list_glucose_readings(
    session: AsyncSession,
    *,
    user_id: UUID,
    days: int = 14,
    limit: int = 200,
) -> list[GlucoseReading]:
    """按时间倒序返回未撤回的读数。"""

    since = datetime.now(UTC) - timedelta(days=days)
    result = await session.scalars(
        select(GlucoseReading)
        .where(
            GlucoseReading.user_id == user_id,
            GlucoseReading.revoked_at.is_(None),
            GlucoseReading.measured_at >= since,
        )
        .order_by(GlucoseReading.measured_at.desc())
        .limit(limit)
    )
    return list(result)


async def revoke_glucose_reading(
    session: AsyncSession,
    *,
    user_id: UUID,
    reading_id: UUID,
) -> GlucoseReading | None:
    """撤回一条读数：打标记，不删行。

    撤回后数据不再被读取，但"记过又撤回"这件事本身保留下来——对低血糖恐惧
    来说，撤回行为是回避的一部分，删干净反而看不见。
    """

    reading = await session.scalar(
        select(GlucoseReading).where(
            GlucoseReading.id == reading_id,
            GlucoseReading.user_id == user_id,
            GlucoseReading.revoked_at.is_(None),
        )
    )
    if reading is None:
        return None
    reading.revoked_at = datetime.now(UTC)
    await session.flush()
    return reading


class GlucoseSummary(TypedDict):
    """窗口统计的固定结构。

    模型段子和血糖工具都读它，写成 TypedDict 是为了让类型检查能发现
    「summary 里没有这个键」这类错误，而不是运行时 KeyError。
    """

    days: int
    count: int
    average_mg_dl: float | None
    minimum_mg_dl: int | None
    maximum_mg_dl: int | None
    below_70_count: int


async def summarize_glucose(
    session: AsyncSession,
    *,
    user_id: UUID,
    days: int = 7,
) -> GlucoseSummary:
    """窗口内的读数统计。低于 70 mg/dL 的条数单独给出，供界面展示。"""

    since = datetime.now(UTC) - timedelta(days=days)
    row = (
        await session.execute(
            select(
                func.count(GlucoseReading.id),
                func.avg(GlucoseReading.value_mg_dl),
                func.min(GlucoseReading.value_mg_dl),
                func.max(GlucoseReading.value_mg_dl),
                func.count(GlucoseReading.id).filter(GlucoseReading.value_mg_dl < 70),
            ).where(
                GlucoseReading.user_id == user_id,
                GlucoseReading.revoked_at.is_(None),
                GlucoseReading.measured_at >= since,
            )
        )
    ).one()

    count = int(row[0] or 0)
    average: float | None = (
        round(float(row[1]), 1) if row[1] is not None else None
    )
    minimum: int | None = int(row[2]) if row[2] is not None else None
    maximum: int | None = int(row[3]) if row[3] is not None else None
    return {
        "days": days,
        "count": count,
        "average_mg_dl": average,
        "minimum_mg_dl": minimum,
        "maximum_mg_dl": maximum,
        "below_70_count": int(row[4] or 0),
    }


async def pending_hypo_review(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime,
    low_mg_dl: int = 70,
    window: timedelta = timedelta(hours=24),
) -> GlucoseReading | None:
    """找出还在复盘窗口里、且没问过的那一条偏低记录。

    只取最近一条：同一次事件问第二遍比不问更伤人，两条记录连着问更像审问。
    """

    statement = (
        select(GlucoseReading)
        .where(
            GlucoseReading.user_id == user_id,
            GlucoseReading.revoked_at.is_(None),
            GlucoseReading.reviewed_at.is_(None),
            GlucoseReading.value_mg_dl < low_mg_dl,
            GlucoseReading.measured_at >= now - window,
            GlucoseReading.measured_at <= now,
        )
        .order_by(GlucoseReading.measured_at.desc())
        .limit(1)
    )
    return await session.scalar(statement)


async def mark_hypo_reviewed(
    session: AsyncSession, *, reading: GlucoseReading, now: datetime
) -> None:
    """标记这次偏低已经复盘过，避免反复追问。"""

    reading.reviewed_at = now
    await session.commit()


GLUCOSE_CONTEXT_DAYS = 7
GLUCOSE_LOW_MG_DL = 70

GLUCOSE_CONTEXT_LABELS: dict[str, str] = {
    "fasting": "空腹",
    "pre_meal": "餐前",
    "post_meal": "餐后",
    "bedtime": "睡前",
    "night": "夜间",
    "other": "随时",
}


def to_mmol(value_mg_dl: int) -> str:
    """国内患者说的是 mmol/L；库里存 mg/dL，只在这里换算一次。"""

    return f"{value_mg_dl / 18:.1f}"


def render_glucose_context(
    *,
    days: int,
    summary: GlucoseSummary,
    recent: list[GlucoseReading],
) -> str:
    """把患者自己记的血糖摊成模型能读的一段。

    只陈述数字与情境，不给判断：模型据此接话，判读留给对话本身。
    """

    count = summary["count"]
    if count == 0:
        return ""

    lines = [f"[血糖记录：患者自己记的，近 {days} 天 {count} 次]"]
    average = summary["average_mg_dl"]
    parts = []
    if average is not None:
        parts.append(f"平均 {to_mmol(round(average))}")
    minimum = summary["minimum_mg_dl"]
    if minimum is not None:
        parts.append(f"最低 {to_mmol(minimum)}")
    maximum = summary["maximum_mg_dl"]
    if maximum is not None:
        parts.append(f"最高 {to_mmol(maximum)}")
    low_count = summary["below_70_count"]
    if low_count > 0:
        parts.append(f"低于 3.9 的 {low_count} 次")
    if parts:
        lines.append("，".join(parts) + " mmol/L。")

    if recent:
        lines.append("最近几次（时间 | 数值 | 情境）：")
        for reading in recent:
            label = GLUCOSE_CONTEXT_LABELS.get(reading.context, reading.context)
            lines.append(
                f"- {reading.measured_at:%m-%d %H:%M} | "
                f"{to_mmol(reading.value_mg_dl)} | {label}"
            )

    lines.append(
        "这些是患者自己记的，可能漏记，不是连续监测；不要拿一两次的波动说他"
        "「失控」，也不要替他判读高低。"
    )
    return "\n".join(lines)
