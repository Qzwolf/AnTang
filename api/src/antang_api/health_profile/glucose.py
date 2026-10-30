"""血糖记录的读写与撤回。

只做存取与最基础的汇总，不做任何临床判断：什么算低、什么时候该提醒，
都在别处按阈值决定，这里只负责把数字和时间如实保存。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TypedDict
from uuid import UUID

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models.glucose import GlucoseReading, GlucoseReport
from antang_api.models.health_profile import PersonalProfile

# 没设过目标范围的患者用这两个值：多数成人 T1DM 的常用目标（3.9–10.0 mmol/L）。
# 设过之后一切以患者自己的为准——这是设置，不是常量。
GLUCOSE_TARGET_LOW_DEFAULT = 70
GLUCOSE_TARGET_HIGH_DEFAULT = 180

# 旧安装包（26102504 及更早）只认「低于 70 mg/dL = 3.9 的次数」这一项，
# 读到缺字段就直接报错、整页血糖空掉。这一项只为它们保留，和患者目标范围无关。
LEGACY_LOW_MG_DL = 70


async def glucose_targets(session: AsyncSession, *, user_id: UUID) -> tuple[int, int]:
    """患者自己设的血糖目标范围（mg/dL）；没设过就是默认的 70–180。"""

    profile = await session.get(PersonalProfile, user_id)
    if profile is None:
        return GLUCOSE_TARGET_LOW_DEFAULT, GLUCOSE_TARGET_HIGH_DEFAULT
    low = profile.glucose_target_low_mg_dl or GLUCOSE_TARGET_LOW_DEFAULT
    high = profile.glucose_target_high_mg_dl or GLUCOSE_TARGET_HIGH_DEFAULT
    return int(low), int(high)


async def set_glucose_targets(
    session: AsyncSession, *, user_id: UUID, low_mg_dl: int, high_mg_dl: int
) -> tuple[int, int]:
    """改目标范围。数据库里还有 low < high 的 CHECK，这里先挡一次好给中文错。"""

    if low_mg_dl <= 0 or high_mg_dl <= low_mg_dl:
        raise ValueError("目标范围的下限要小于上限，而且都得大于 0。")
    profile = await session.get(PersonalProfile, user_id)
    if profile is None:
        profile = PersonalProfile(user_id=user_id)
        session.add(profile)
    profile.glucose_target_low_mg_dl = low_mg_dl
    profile.glucose_target_high_mg_dl = high_mg_dl
    await session.flush()
    return low_mg_dl, high_mg_dl


async def record_glucose_reading(
    session: AsyncSession,
    *,
    user_id: UUID,
    value_mg_dl: int,
    measured_at: datetime,
    context: str = "other",
    source: str = "manual",
    source_device: str = "",
    measured_precision: str = "exact",
    note: str | None = None,
) -> GlucoseReading:
    reading = GlucoseReading(
        user_id=user_id,
        value_mg_dl=value_mg_dl,
        measured_at=measured_at,
        context=context,
        source=source,
        # 化验单和传感器没有"哪台血糖仪"，调用方就算传了也不存，免得出现
        # 「静脉血 · 罗氏」这种自相矛盾的记录。
        source_device=source_device if source == "fingerstick" else "",
        measured_precision=measured_precision,
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
    # 三条按患者自己的目标范围切出来的计数；范围本身也一起回给界面，
    # 免得界面再去猜「3.9 是哪来的」。
    target_low_mg_dl: int
    target_high_mg_dl: int
    below_target_count: int
    in_target_count: int
    above_target_count: int
    # 兼容旧安装包（26102504 及更早）：「低于 3.9 的次数」当年是按固定 70 mg/dL 算的，
    # 这些包读不到 below_70_count 会直接报错、整个血糖页空掉。
    # 所以这一项保留成「低于 LEGACY_LOW_MG_DL 的条数」，和新目标范围无关。
    # 等线上连旧包都没有了再删。
    below_70_count: int


async def summarize_glucose(
    session: AsyncSession,
    *,
    user_id: UUID,
    days: int = 7,
) -> GlucoseSummary:
    """窗口内的读数统计，按患者自己的目标范围切出偏低 / 目标内 / 偏高。"""

    since = datetime.now(UTC) - timedelta(days=days)
    low, high = await glucose_targets(session, user_id=user_id)
    row = (
        await session.execute(
            select(
                func.count(GlucoseReading.id),
                func.avg(GlucoseReading.value_mg_dl),
                func.min(GlucoseReading.value_mg_dl),
                func.max(GlucoseReading.value_mg_dl),
                func.count(GlucoseReading.id).filter(
                    GlucoseReading.value_mg_dl < low
                ),
                func.count(GlucoseReading.id).filter(
                    GlucoseReading.value_mg_dl >= low,
                    GlucoseReading.value_mg_dl <= high,
                ),
                func.count(GlucoseReading.id).filter(
                    GlucoseReading.value_mg_dl > high
                ),
                func.count(GlucoseReading.id).filter(
                    GlucoseReading.value_mg_dl < LEGACY_LOW_MG_DL
                ),
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
        "target_low_mg_dl": low,
        "target_high_mg_dl": high,
        "below_target_count": int(row[4] or 0),
        "in_target_count": int(row[5] or 0),
        "above_target_count": int(row[6] or 0),
        "below_70_count": int(row[7] or 0),
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

GLUCOSE_CONTEXT_LABELS: dict[str, str] = {
    "fasting": "空腹",
    "pre_meal": "餐前",
    "post_meal": "餐后",
    "bedtime": "睡前",
    "night": "夜间",
    "other": "随时",
}

# 指尖血的机器品牌。留着是给「同一台机器和化验单对不上」这类问题用的，
# 不是给模型算平均值的。
GLUCOSE_DEVICE_LABELS: dict[str, str] = {
    "roche": "罗氏",
    "abbott": "雅培",
    "sannuo": "三诺",
    "other": "其它血糖仪",
}


def to_mmol(value_mg_dl: int) -> str:
    """国内患者说的是 mmol/L；库里存 mg/dL，只在这里换算一次。"""

    return f"{value_mg_dl / 18:.1f}"


# 患者只说得出「早上 / 晚上 / 半夜」时，条目的时间就是该时段的代表时刻；
# 展示上必须写成「早上（大概）」，不能显示成一个精确钟点。
GLUCOSE_DAYPART_LABELS: dict[int, str] = {
    0: "半夜",
    2: "半夜",
    7: "早上",
    12: "中午",
    15: "下午",
    20: "晚上",
}


def measured_time_label(measured_at: datetime, precision: str) -> str:
    """把一条读数的时间写成给人看的样子。"""

    if precision == "daypart":
        daypart = GLUCOSE_DAYPART_LABELS.get(measured_at.hour, "大概时段")
        return f"{measured_at:%m-%d} {daypart}（大概）"
    return f"{measured_at:%m-%d %H:%M}"


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
    low_count = summary["below_target_count"]
    high_count = summary["above_target_count"]
    if low_count > 0:
        parts.append(f"低于 {to_mmol(summary['target_low_mg_dl'])} 的 {low_count} 次")
    if high_count > 0:
        parts.append(f"高于 {to_mmol(summary['target_high_mg_dl'])} 的 {high_count} 次")
    if parts:
        lines.append("，".join(parts) + " mmol/L。")

    low_target = summary["target_low_mg_dl"]
    high_target = summary["target_high_mg_dl"]
    if (low_target, high_target) != (
        GLUCOSE_TARGET_LOW_DEFAULT,
        GLUCOSE_TARGET_HIGH_DEFAULT,
    ):
        lines.append(
            f"他自己设的目标范围是 {to_mmol(low_target)}–{to_mmol(high_target)} mmol/L，"
            "说高低就按这个范围说，不要拿 3.9–10.0 套他。"
        )

    if recent:
        lines.append("最近几次（时间 | 数值 | 情境）：")
        for reading in recent:
            label = GLUCOSE_CONTEXT_LABELS.get(reading.context, reading.context)
            lines.append(
                f"- {measured_time_label(reading.measured_at, reading.measured_precision)} | "
                f"{to_mmol(reading.value_mg_dl)} | {label}"
            )

    lines.append(
        "这些是患者自己记的，可能漏记，不是连续监测；不要拿一两次的波动说他"
        "「失控」，也不要替他判读高低。"
    )
    return "\n".join(lines)


LBGI_LEVEL_LABELS = {
    "high": "偏低血糖风险高",
    "moderate": "偏低血糖风险中",
    "low": "偏低血糖风险低",
    "lowest": "偏低血糖风险最低",
}


def _band_total(*values: float | None) -> float:
    return round(sum(value for value in values if value is not None), 2)


def _num(value: object) -> str:
    """Numeric 取出来是 Decimal（99.60），跟报告一样写成 99.6。"""

    return f"{float(str(value)):g}"


async def record_glucose_report(
    session: AsyncSession,
    *,
    user_id: UUID,
    period_start: date,
    period_end: date,
    device: str = "",
    device_serial: str = "",
    report_generated_at: date | None = None,
    mean_mmol: float | None = None,
    sd_mmol: float | None = None,
    cv_percent: float | None = None,
    ehba1c_percent: float | None = None,
    tir_percent: float | None = None,
    tar_percent: float | None = None,
    tbr_percent: float | None = None,
    normal_percent: float | None = None,
    low_percent: float | None = None,
    very_low_percent: float | None = None,
    high_percent: float | None = None,
    very_high_percent: float | None = None,
    lbgi_level: str = "",
    note: str | None = None,
    source_attachment_id: UUID | None = None,
) -> GlucoseReport:
    """存一条报告速览。

    只存患者自己那份报告上写着的指标；没写的保持空，不用平均值的默认值去补齐——
    一条「TIR 50%」的假数据比空着更糟。

    报告如果给了五档区间，三档的 TAR/TBR 就在本地由「高+很高」「低+很低」加出来，
    不再让模型自己算（模型算错一次，记录就永远错着）。
    """

    if normal_percent is not None and tir_percent is None:
        tir_percent = normal_percent
    if tar_percent is None and (high_percent is not None or very_high_percent is not None):
        tar_percent = _band_total(high_percent, very_high_percent)
    if tbr_percent is None and (low_percent is not None or very_low_percent is not None):
        tbr_percent = _band_total(low_percent, very_low_percent)

    report = GlucoseReport(
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        device=device,
        device_serial=device_serial,
        report_generated_at=report_generated_at,
        mean_mmol=mean_mmol,
        sd_mmol=sd_mmol,
        cv_percent=cv_percent,
        ehba1c_percent=ehba1c_percent,
        tir_percent=tir_percent,
        tar_percent=tar_percent,
        tbr_percent=tbr_percent,
        normal_percent=normal_percent,
        low_percent=low_percent,
        very_low_percent=very_low_percent,
        high_percent=high_percent,
        very_high_percent=very_high_percent,
        lbgi_level=lbgi_level,
        note=note,
        source_attachment_id=source_attachment_id,
    )
    session.add(report)
    await session.flush()
    return report


async def list_glucose_reports(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 20,
) -> list[GlucoseReport]:
    """按监测结束时间倒序返回患者存过的报告速览。"""

    result = await session.scalars(
        select(GlucoseReport)
        .where(GlucoseReport.user_id == user_id)
        .order_by(GlucoseReport.period_end.desc(), GlucoseReport.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def delete_glucose_report(
    session: AsyncSession,
    *,
    user_id: UUID,
    report_id: UUID,
) -> bool:
    """删掉一条报告速览。

    报告速览是我们替患者存的一份摘要，不是原始报告文件；患者说删就真删，
    免得界面上留一条他否认的记录。
    """

    deleted = await session.scalar(
        sql_delete(GlucoseReport)
        .where(GlucoseReport.id == report_id, GlucoseReport.user_id == user_id)
        .returning(GlucoseReport.id)
    )
    return deleted is not None


def render_glucose_reports(reports: list[GlucoseReport]) -> str:
    """把报告速览拼成一段给模型看的上下文。"""

    if not reports:
        return ""

    lines = ["[患者存过的血糖报告速览]"]
    for report in reports[:3]:
        parts = [f"{report.period_start:%Y-%m-%d} 至 {report.period_end:%Y-%m-%d}"]
        if report.device:
            serial = f"（序列号 {report.device_serial}）" if report.device_serial else ""
            parts.append(f"设备 {report.device}{serial}")
        if report.mean_mmol is not None:
            parts.append(f"平均 {_num(report.mean_mmol)} mmol/L")
        if report.sd_mmol is not None:
            parts.append(f"标准差 {_num(report.sd_mmol)}")
        if report.cv_percent is not None:
            parts.append(f"变异系数 {_num(report.cv_percent)}%")
        if report.ehba1c_percent is not None:
            parts.append(f"预估糖化 {_num(report.ehba1c_percent)}%")
        if report.tir_percent is not None:
            parts.append(f"TIR {_num(report.tir_percent)}%")
        if report.tar_percent is not None:
            parts.append(f"高于目标 {_num(report.tar_percent)}%")
        if report.tbr_percent is not None:
            parts.append(f"低于目标 {_num(report.tbr_percent)}%")
        bands = [
            f"{label} {_num(value)}%"
            for label, value in (
                ("正常", report.normal_percent),
                ("低", report.low_percent),
                ("很低", report.very_low_percent),
                ("高", report.high_percent),
                ("很高", report.very_high_percent),
            )
            if value is not None
        ]
        if bands:
            parts.append("分档 " + " / ".join(bands))
        if report.lbgi_level:
            label = LBGI_LEVEL_LABELS.get(report.lbgi_level, report.lbgi_level)
            parts.append(f"报告写的低血糖风险：{label}")
        lines.append("- " + " | ".join(parts))
    lines.append(
        "这些是报告上原本的指标，不是我们算的；解释时按报告自己的目标范围说，"
        "不要跟 3.9–10.0 混着讲。"
    )
    return "\n".join(lines)
