"""Add one clearly marked, deterministic synthetic patient for the clinical demo."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from antang_api.database import engine, session_factory
from antang_api.import_ohio import apply_subject, insert_rows
from antang_api.models import GlucoseReading
from antang_api.models.study import StudyEvent

USERNAME = "antang_demo_patient"
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


async def seed() -> None:
    try:
        async with session_factory.begin() as session:
            user_id = await apply_subject(
                session, USERNAME, "模拟患者（演示数据）", DATASET, None, BEGIN, END,
                {"notice": NOTE, "synthetic": True, "version": 1},
            )
            glucose = [
                {"id": uuid5(NAMESPACE_URL, f"{USERNAME}:glucose:{index}"),
                 "user_id": user_id, "measured_at": BEGIN + timedelta(minutes=30 * index),
                 "value_mg_dl": value, "source": "manual", "context": "other", "note": NOTE}
                for index, value in enumerate(VALUES)
            ]
            events = [
                {"id": uuid5(NAMESPACE_URL, f"{USERNAME}:event:{index}"),
                 "user_id": user_id, "kind": kind, "value": value, "unit": unit,
                 "measured_at": BEGIN + timedelta(hours=start),
                 "ended_at": BEGIN + timedelta(hours=end) if end is not None else None,
                 "detail": "模拟演示", "source_ref": f"synthetic:{index}", "raw_record": {"synthetic": True}}
                for index, (kind, start, end, value, unit) in enumerate(EVENTS)
            ]
            added_glucose = await insert_rows(session, GlucoseReading, glucose)
            added_events = await insert_rows(session, StudyEvent, events)
        print(f"模拟患者已就绪，本次新增 {added_glucose} 条血糖和 {added_events} 条事件。")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
