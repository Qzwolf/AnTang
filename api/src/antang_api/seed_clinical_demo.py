"""Move deterministic simulation records to the existing clinical example account."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import engine, session_factory
from antang_api.import_ohio import apply_subject, insert_rows
from antang_api.models import GlucoseReading, PatientAccessGrant, User, UserRole
from antang_api.models.study import StudyEvent, StudySubject
from antang_api.settings import settings

USERNAME = "antang_demo_patient"
EXTRA_USERNAME = "antang_synthetic_demo_v1"
NAME = "示例患者"
RECORD_KEY = "antang_demo_patient"  # Keep record IDs stable when the demo account name changes.
DATASET = "模拟演示"
NOTE = "虚构演示记录，非真实患者数据或用药建议。"
BEGIN = datetime(2026, 11, 18, 16, tzinfo=UTC)
END = BEGIN + timedelta(days=1)
VALUES = (
    105, 101, 99, 98, 100, 102, 103, 102, 101, 103, 105, 109,
    112, 115, 118, 122, 140, 164, 172, 154, 139, 127, 118, 116,
    129, 151, 178, 181, 161, 144, 129, 121, 115, 109, 105, 107,
    113, 118, 133, 158, 169, 162, 149, 134, 124, 119, 113, 109,
)
# Hours are relative to the start of the demonstration day.
EVENTS = (
    ("basal", 0, 24, 0.8, "U/h"),
    ("bolus", 7.5, None, 2, "U"),
    ("meal", 8, None, 42, "g"),
    ("bolus", 12, None, 3, "U"),
    ("meal", 12.5, None, 55, "g"),
    ("temp_basal", 15, 16, 0.5, "U/h"),
    ("bolus", 18, None, 2, "U"),
    ("meal", 18.5, None, 47, "g"),
)


async def populate(session: AsyncSession, *, local_preview: bool = False) -> tuple[int, int]:
    if local_preview and not settings.local_basic_mode:
        raise ValueError("Local preview grants are unavailable in production")
    provenance = {"notice": NOTE, "synthetic": True, "version": 1}
    user = await session.scalar(select(User).where(User.username_normalized == USERNAME).with_for_update())
    if user is None:
        if not local_preview:
            raise ValueError("Existing clinical example account was not found")
        user_id = await apply_subject(session, USERNAME, NAME, DATASET, None, BEGIN, END, provenance)
    else:
        if user.full_name != NAME or user.role != UserRole.PATIENT or not user.is_active:
            raise ValueError("Existing clinical example account did not match")
        user_id = user.id
    subject = await session.get(StudySubject, user_id)
    if subject is not None and (subject.dataset != DATASET or subject.provenance != provenance):
        raise ValueError("Existing example account has different research data")

    extra = await session.scalar(select(User).where(User.username_normalized == EXTRA_USERNAME).with_for_update())
    if extra is not None:
        extra_subject = await session.get(StudySubject, extra.id)
        if (extra.full_name != "模拟患者（演示数据）" or extra.role != UserRole.PATIENT
                or extra_subject is None or extra_subject.dataset != DATASET
                or extra_subject.provenance != provenance):
            raise ValueError("Separate simulation account did not match")
        for model in (GlucoseReading, StudyEvent):
            await session.execute(update(model).where(model.user_id == extra.id).values(user_id=user_id))
        await session.execute(delete(User).where(User.id == extra.id))

    if subject is None:
        session.add(StudySubject(user_id=user_id, dataset=DATASET, insulin_type=None,
                                 period_start=BEGIN, period_end=END, provenance=provenance))
    if local_preview:
        clinician = await session.scalar(select(User).where(User.username_normalized == "preview_clinician",
                                                            User.role == UserRole.CLINICIAN))
        if clinician is None:
            raise ValueError("Local preview clinician was not found")
        grant = await session.scalar(select(PatientAccessGrant.id).where(
            PatientAccessGrant.patient_id == user_id,
            PatientAccessGrant.clinician_id == clinician.id,
            PatientAccessGrant.revoked_at.is_(None),
        ))
        if grant is None:
            session.add(PatientAccessGrant(patient_id=user_id, clinician_id=clinician.id))

    glucose = [
        {"id": uuid5(NAMESPACE_URL, f"{RECORD_KEY}:glucose:{index}"),
         "user_id": user_id, "measured_at": BEGIN + timedelta(minutes=30 * index),
         "value_mg_dl": value, "source": "manual", "context": "other", "note": NOTE}
        for index, value in enumerate(VALUES)
    ]
    events = [
        {"id": uuid5(NAMESPACE_URL, f"{RECORD_KEY}:event:{index}"),
         "user_id": user_id, "kind": kind, "value": value, "unit": unit,
         "measured_at": BEGIN + timedelta(hours=start),
         "ended_at": BEGIN + timedelta(hours=end) if end is not None else None,
         "detail": "模拟演示", "source_ref": f"synthetic:{index}", "raw_record": {"synthetic": True}}
        for index, (kind, start, end, value, unit) in enumerate(EVENTS)
    ]
    added_glucose = await insert_rows(session, GlucoseReading, glucose)
    added_events = await insert_rows(session, StudyEvent, events)
    return added_glucose, added_events


async def seed(*, local_preview: bool = False) -> None:
    try:
        async with session_factory.begin() as session:
            added_glucose, added_events = await populate(session, local_preview=local_preview)
        print(f"示例患者已就绪，本次补建 {added_glucose} 条血糖和 {added_events} 条事件。")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-preview", action="store_true")
    asyncio.run(seed(local_preview=parser.parse_args().local_preview))
