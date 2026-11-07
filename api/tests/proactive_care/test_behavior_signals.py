"""行为信号：只数次数与时间，不读文字。

测三件事：形状（同一天分散测 vs 集中在三小时里）、边界（刚好 6 次算不算）、
夜里按患者本地时区判而不是按 UTC。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import session_factory
from antang_api.models import FearBehaviorLog, GlucoseReading, ProactiveCareSettings, User
from antang_api.proactive_care.behavior_signals import (
    EXERCISE_AVOIDANCE_COUNT,
    NIGHT_RECHECK_COUNT,
    REPEATED_CHECK_COUNT,
    REPEATED_CHECK_WINDOW,
    behavior_signals,
    exercise_avoidance,
    night_rechecking,
    repeated_check,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def _clean_up_signal_users():
    """本文件造的 signal_ 用户跑完删掉，免得别的用例的扫描把它们算进去。"""

    from sqlalchemy import delete

    yield
    async with session_factory() as session:
        await session.execute(
            delete(User).where(User.username.like("signal\\_%", escape="\\"))
        )
        await session.commit()


def _minutes_ago(minutes: int) -> datetime:
    return NOW - timedelta(minutes=minutes)


async def _create_user(session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:10]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    session.add(user)
    await session.flush()
    session.add(ProactiveCareSettings(user_id=user.id))
    await session.flush()
    return user


async def _add_reading(
    session: AsyncSession, user_id: UUID, measured_at: datetime
) -> GlucoseReading:
    reading = GlucoseReading(
        user_id=user_id,
        value_mg_dl=110,
        measured_at=measured_at,
        context="other",
        source="fingerstick",
    )
    session.add(reading)
    await session.flush()
    return reading


def test_six_times_inside_three_hours_is_a_signal() -> None:
    times = [_minutes_ago(minutes) for minutes in (10, 40, 70, 100, 130, 160)]

    signal = repeated_check(times, now=NOW)

    assert signal is not None
    assert signal.kind == "repeated_check"
    assert signal.occurred == REPEATED_CHECK_COUNT
    assert signal.window_days == 1


def test_the_six_can_be_spread_over_the_day_without_a_signal() -> None:
    # 早中晚各两次，一天六次，形状不是「越测越慌」。
    times = [
        NOW.replace(hour=1),
        NOW.replace(hour=2),
        NOW.replace(hour=9),
        NOW.replace(hour=10),
        NOW.replace(hour=20),
        NOW.replace(hour=21),
    ]

    assert repeated_check(times, now=NOW) is None


def test_five_inside_one_hour_is_still_below_the_line() -> None:
    times = [_minutes_ago(minutes) for minutes in (5, 15, 25, 35, 45)]

    assert repeated_check(times, now=NOW) is None


def test_readings_from_two_days_ago_do_not_count() -> None:
    times = [
        NOW - timedelta(days=2),
        NOW - timedelta(days=2, minutes=20),
        NOW - timedelta(days=2, minutes=40),
        NOW - timedelta(days=2, minutes=60),
        NOW - timedelta(days=2, minutes=80),
        NOW - timedelta(days=2, minutes=100),
    ]

    assert repeated_check(times, now=NOW) is None


def test_one_skipped_run_is_nerves_and_two_is_a_pattern() -> None:
    assert exercise_avoidance([_minutes_ago(60)], now=NOW) is None

    signal = exercise_avoidance(
        [NOW - timedelta(days=3), NOW - timedelta(days=9)], now=NOW
    )

    assert signal is not None
    assert signal.kind == "exercise_avoidance"
    assert signal.occurred == EXERCISE_AVOIDANCE_COUNT


def test_old_skipped_runs_fall_out_of_the_two_week_window() -> None:
    assert (
        exercise_avoidance(
            [NOW - timedelta(days=20), NOW - timedelta(days=30)], now=NOW
        )
        is None
    )


def test_two_nights_with_two_checks_each_is_a_signal() -> None:
    # 北京时间凌晨 02:00 与 03:30：UTC 是前一天 18:00/19:30，按 UTC 判就不会算成夜里。
    zone_7 = timezone(timedelta(hours=8))
    night_one = datetime(2026, 11, 1, 2, 0, tzinfo=zone_7)
    night_two = datetime(2026, 11, 2, 2, 0, tzinfo=zone_7)
    times = [
        night_one,
        night_one + timedelta(minutes=90),
        night_two,
        night_two + timedelta(minutes=90),
    ]

    signal = night_rechecking(times, now=NOW, timezone_name="Asia/Shanghai")

    assert signal is not None
    assert signal.kind == "night_rechecking"
    assert signal.occurred == 2


def test_one_check_per_night_is_not_rechecking() -> None:
    zone_7 = timezone(timedelta(hours=8))
    times = [
        datetime(2026, 11, 1, 2, 0, tzinfo=zone_7),
        datetime(2026, 11, 2, 2, 0, tzinfo=zone_7),
    ]

    assert (
        night_rechecking(times, now=NOW, timezone_name="Asia/Shanghai") is None
    )


def test_a_reading_at_five_in_the_morning_is_the_morning_not_the_night() -> None:
    zone_7 = timezone(timedelta(hours=8))
    times = [
        datetime(2026, 11, 1, 5, 30, tzinfo=zone_7),
        datetime(2026, 11, 1, 5, 45, tzinfo=zone_7),
        datetime(2026, 11, 2, 5, 30, tzinfo=zone_7),
        datetime(2026, 11, 2, 5, 45, tzinfo=zone_7),
    ]

    assert (
        night_rechecking(times, now=NOW, timezone_name="Asia/Shanghai") is None
    )


@pytest.mark.asyncio
async def test_the_signals_are_put_together_from_the_database(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "signal_busy")
    for minutes in (10, 30, 50, 70, 90, 110):
        await _add_reading(db_session, user.id, _minutes_ago(minutes))
    db_session.add(
        FearBehaviorLog(
            user_id=user.id,
            kind="exercise_avoidance",
            occurred_at=NOW - timedelta(days=2),
            count=1,
        )
    )
    db_session.add(
        FearBehaviorLog(
            user_id=user.id,
            kind="exercise_avoidance",
            occurred_at=NOW - timedelta(days=6),
            count=1,
        )
    )
    await db_session.commit()

    found = await behavior_signals(
        db_session, user_id=user.id, now=NOW, timezone_name="Asia/Shanghai"
    )

    kinds = [signal.kind for signal in found]
    assert kinds == ["repeated_check", "exercise_avoidance"]
    assert found[0].evidence[-1] == _minutes_ago(10)


@pytest.mark.asyncio
async def test_revoked_readings_and_deleted_logs_do_not_count(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "signal_gone")
    for minutes in (10, 30, 50, 70, 90):
        reading = await _add_reading(db_session, user.id, _minutes_ago(minutes))
        reading.revoked_at = NOW
    db_session.add(
        FearBehaviorLog(
            user_id=user.id,
            kind="exercise_avoidance",
            occurred_at=NOW - timedelta(days=1),
            count=1,
            deleted_at=NOW,
        )
    )
    await db_session.commit()

    found = await behavior_signals(
        db_session, user_id=user.id, now=NOW, timezone_name="Asia/Shanghai"
    )

    assert found == []


@pytest.mark.asyncio
async def test_another_patients_readings_never_count(db_session: AsyncSession) -> None:
    mine = await _create_user(db_session, "signal_mine")
    theirs = await _create_user(db_session, "signal_theirs")
    for minutes in (10, 30, 50, 70, 90, 110):
        await _add_reading(db_session, theirs.id, _minutes_ago(minutes))
    await db_session.commit()

    found = await behavior_signals(
        db_session, user_id=mine.id, now=NOW, timezone_name="Asia/Shanghai"
    )

    assert found == []


def test_the_window_constant_is_the_one_the_doc_names() -> None:
    assert REPEATED_CHECK_WINDOW == timedelta(hours=3)
    assert REPEATED_CHECK_COUNT == 6
    assert NIGHT_RECHECK_COUNT == 2
    assert EXERCISE_AVOIDANCE_COUNT == 2
