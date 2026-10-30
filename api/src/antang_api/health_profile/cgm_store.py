"""CGM 导入的存取：先记账，再写样本，重复导入不会重复入库。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.health_profile.cgm_import import ParsedSample
from antang_api.models.glucose import CgmSample, GlucoseImport


async def store_import(
    session: AsyncSession,
    *,
    user_id: UUID,
    source_system: str,
    file_name: str,
    samples: list[ParsedSample],
    skipped_count: int = 0,
) -> GlucoseImport:
    """写入一次导入：账本一行 + 样本若干（唯一键冲突就跳过）。"""

    record = GlucoseImport(
        user_id=user_id,
        source_system=source_system,
        file_name=file_name[:200],
        parsed_count=len(samples),
        skipped_count=skipped_count,
    )
    session.add(record)
    await session.flush()

    inserted = 0
    if samples:
        values = [
            {
                "user_id": user_id,
                "import_id": record.id,
                "source_system": source_system,
                "device_id": sample.device_id or "",
                "recorded_at": sample.recorded_at,
                "value_mg_dl": sample.value_mg_dl,
                "trend": sample.trend,
                "quality": "ok",
            }
            for sample in samples
        ]
        statement = (
            insert(CgmSample)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[
                    CgmSample.user_id,
                    CgmSample.source_system,
                    CgmSample.device_id,
                    CgmSample.recorded_at,
                ]
            )
            .returning(CgmSample.id)
        )
        inserted = len((await session.scalars(statement)).all())

    record.inserted_count = inserted
    record.skipped_count = skipped_count + (len(samples) - inserted)
    await session.flush()
    return record


async def read_samples(
    session: AsyncSession,
    *,
    user_id: UUID,
    since,
    limit: int = 2000,
) -> list[CgmSample]:
    return list(
        (
            await session.scalars(
                select(CgmSample)
                .where(CgmSample.user_id == user_id, CgmSample.recorded_at >= since)
                .order_by(CgmSample.recorded_at.asc())
                .limit(limit)
            )
        ).all()
    )
