"""Public history must retain provenance and be safe to import repeatedly."""
from sqlalchemy import delete, select

from antang_api.database import session_factory
from antang_api.health_profile.service import load_health_profile_snapshot
from antang_api.import_public_diabetes import import_records, parse_subject
from antang_api.models import GlucoseReading, User
from antang_api.schemas.glucose import GlucoseReadingResponse


def test_parser_preserves_history_and_reports_invalid_glucose():
    rows, rejected = parse_subject("01", "04-21-1991 9:09 58 100\n04-21-1991 9:09 33 009\n04-21-1991 25:00 48 120\n04-21-1991 10:00 48 0")
    assert len(rows) == 1
    assert rows[0]["measured_at"].year == 1991
    assert rows[0]["source"] == "public_dataset"
    assert "data-01:1" in rows[0]["note"]
    assert [r["line"] for r in rejected] == [3, 4]


async def test_public_history_round_trips_and_repeat_is_idempotent():
    rows, rejected = parse_subject("01", "04-21-1991 9:09 58 100")
    data = {"01": (rows, rejected)}
    try:
        first = await import_records(data)
        second = await import_records(data)
        assert first["01"]["added"] == 1
        assert second["01"]["added"] == 0
        async with session_factory() as session:
            saved = await session.scalar(select(GlucoseReading).where(GlucoseReading.id == rows[0]["id"]))
            assert saved is not None
            await load_health_profile_snapshot(session, saved.user_id)
            response = GlucoseReadingResponse.model_validate(saved)
            assert response.source == "public_dataset"
            assert response.value_mg_dl == 100
            assert response.measured_at.year == 1991
    finally:
        async with session_factory() as session, session.begin():
            user_id = await session.scalar(select(User.id).where(User.username_normalized == "uci_diabetes_01"))
            await session.execute(delete(GlucoseReading).where(GlucoseReading.user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
