"""日常问候落在患者自己定的整点：可预测，比随机更省心。"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from antang_api.database import session_factory
from antang_api.models import (
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    RoutineCareCadence,
)
from antang_api.proactive_care.service import schedule_routine_check_in

SHANGHAI = ZoneInfo("Asia/Shanghai")


async def _daily_at(user_id: UUID, hour: int | None) -> None:
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.DAILY
        care_settings.timezone = "Asia/Shanghai"
        care_settings.check_in_hour = hour
        await session.commit()


async def _scheduled(session: Any, user_id: UUID) -> ProactiveCareTask:
    task = await session.scalar(
        select(ProactiveCareTask).where(
            ProactiveCareTask.user_id == user_id,
            ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
        )
    )
    assert task is not None
    return task


async def test_a_chosen_hour_wins_over_the_rolling_clock(
    committed_user_id: UUID,
) -> None:
    await _daily_at(committed_user_id, 9)
    after = datetime.now(timezone.utc)

    async with session_factory() as session:
        await schedule_routine_check_in(
            session, user_id=committed_user_id, after=after, reset_existing=False
        )
        await session.commit()
        task = await _scheduled(session, committed_user_id)

    local = task.due_at.astimezone(SHANGHAI)
    assert (local.hour, local.minute) == (9, 0)
    assert task.due_at > after
    # 最多比「一天之后」晚一天：只会滑到下一个整点。
    assert task.due_at <= after + timedelta(days=2)
    # 过期时间跟着新时间走，不会变成「到了却已经过期」。
    assert task.expires_at == task.due_at + timedelta(hours=12)


async def test_an_hour_that_already_passed_today_rolls_to_tomorrow(
    committed_user_id: UUID,
) -> None:
    await _daily_at(committed_user_id, 9)
    # 本地时间 10-31 15:00 触发一次问候：滚动时间是一天后 11-01 15:00，
    # 那之后的第一个 09:00 是 11-02。固定时刻不会让间隔变短。
    after = datetime(2026, 10, 31, 15, 0, tzinfo=SHANGHAI)

    async with session_factory() as session:
        await schedule_routine_check_in(
            session, user_id=committed_user_id, after=after, reset_existing=False
        )
        await session.commit()
        task = await _scheduled(session, committed_user_id)

    assert task.due_at.astimezone(SHANGHAI) == datetime(
        2026, 11, 2, 9, 0, tzinfo=SHANGHAI
    )


async def test_without_a_chosen_hour_the_clock_keeps_rolling(
    committed_user_id: UUID,
) -> None:
    await _daily_at(committed_user_id, None)
    after = datetime(2026, 10, 31, 15, 0, tzinfo=SHANGHAI)

    async with session_factory() as session:
        await schedule_routine_check_in(
            session, user_id=committed_user_id, after=after, reset_existing=False
        )
        await session.commit()
        task = await _scheduled(session, committed_user_id)

    # 没选整点就还是「上次之后 + 一天」，一分不差。
    assert task.due_at == after + timedelta(days=1)


async def test_changing_the_hour_moves_the_waiting_check_in(
    committed_user_id: UUID,
) -> None:
    after = datetime(2026, 10, 31, 15, 0, tzinfo=SHANGHAI)
    await _daily_at(committed_user_id, None)
    async with session_factory() as session:
        await schedule_routine_check_in(
            session, user_id=committed_user_id, after=after, reset_existing=False
        )
        await session.commit()

    # 患者在设置页把「几点」改成 7 点：已经排好的下一次要跟着挪过去。
    await _daily_at(committed_user_id, 7)
    async with session_factory() as session:
        await schedule_routine_check_in(
            session,
            user_id=committed_user_id,
            after=after,
            reset_existing=False,
            realign_existing=True,
        )
        await session.commit()
        task = await _scheduled(session, committed_user_id)

    assert task.due_at.astimezone(SHANGHAI) == datetime(
        2026, 11, 2, 7, 0, tzinfo=SHANGHAI
    )
