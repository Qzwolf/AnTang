from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models.psychosocial import PsychosocialAssessment
from antang_api.psychosocial.support import PsychosocialProfile


async def read_latest_assessment_row(
    session: AsyncSession,
    user_id: UUID,
) -> PsychosocialAssessment | None:
    """取最近一次评估的整行；接口要返回评估时间与量表名。"""

    return await session.scalar(
        select(PsychosocialAssessment)
        .where(PsychosocialAssessment.user_id == user_id)
        .order_by(
            PsychosocialAssessment.assessed_at.desc(),
            PsychosocialAssessment.created_at.desc(),
        )
        .limit(1)
    )


async def read_latest_assessment(
    session: AsyncSession,
    user_id: UUID,
) -> PsychosocialProfile | None:
    """取最近一次评估；没有评估返回 None，调用方据此不加沟通方式那一段。"""

    row = await read_latest_assessment_row(session, user_id)
    if row is None:
        return None
    return PsychosocialProfile(
        fear_behavior_percent=row.fear_behavior_percent,
        fear_worry_percent=row.fear_worry_percent,
        gold_score=row.gold_score,
    )


async def record_assessment(
    session: AsyncSession,
    *,
    user_id: UUID,
    fear_behavior_percent: int,
    fear_worry_percent: int,
    gold_score: int | None,
    instrument: str,
    assessed_at: datetime,
    who5_percent: int | None = None,
) -> PsychosocialAssessment:
    """写入一次评估。历史保留：前后测是判断这套设计是否有效的前提。"""

    row = PsychosocialAssessment(
        user_id=user_id,
        fear_behavior_percent=fear_behavior_percent,
        fear_worry_percent=fear_worry_percent,
        gold_score=gold_score,
        who5_percent=who5_percent,
        instrument=instrument,
        assessed_at=assessed_at,
    )
    session.add(row)
    await session.flush()
    return row
