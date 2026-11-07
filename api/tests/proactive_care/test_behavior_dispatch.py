"""行为痕迹接进主动关怀：排队、去重、现算核对。

测六件事：命中就排一个 task；同一个话题等着的任务不会排第二个；三天内提过的跳过；
只扫最近一天有读数且开关打开的人；排队后痕迹消失就作废不发；额度用完直接作废、
不像日常问候那样重排。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from antang_api.database import session_factory
from antang_api.models import (
    BehaviorSignalRecord,
    GlucoseReading,
    RiskAnnotation,
    User,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
)
from antang_api.proactive_care.behavior_dispatch import (
    BEHAVIOR_SIGNAL_RULE_ID,
    behavior_event_key,
    queue_behavior_signals,
    sweep_behavior_signals,
)
from antang_api.proactive_care.behavior_signals import BehaviorSignal
from antang_api.proactive_care.processor import ProactiveCareProcessor
from antang_api.proactive_care.quota import WeeklyQuota
from antang_api.proactive_care.worker import ProactiveCareWorker

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
async def _clean_up_test_users():
    """这个文件会往共享库里真写用户与任务，跑完自己收干净。

    不清理的话，别的用例的 Worker 会把这里排下的任务领走，然后在别人的断言里
    失败——这不是它自己的问题，但看起来一模一样。
    """

    from sqlalchemy import delete

    yield
    async with session_factory() as session:
        await session.execute(
            delete(User).where(User.username.like("dispatch\\_%", escape="\\"))
        )
        await session.commit()


async def _create_user(prefix: str) -> UUID:
    from antang_api.models import User

    async with session_factory() as session:
        username = f"{prefix}_{uuid4().hex[:8]}"
        user = User(
            username=username,
            username_normalized=username,
            password_hash="test-only-password-hash",
        )
        session.add(user)
        await session.flush()
        settings = ProactiveCareSettings(
            user_id=user.id,
            health_events_enabled=True,
            quiet_hours_start=time(0, 0),
            quiet_hours_end=time(0, 0),
        )
        session.add(settings)
        await session.commit()
        return user.id


async def _add_readings(
    user_id: UUID, *, offsets_minutes: tuple[int, ...], now: datetime = NOW
) -> None:
    async with session_factory() as session:
        for minutes in offsets_minutes:
            session.add(
                GlucoseReading(
                    user_id=user_id,
                    value_mg_dl=110,
                    measured_at=now - timedelta(minutes=minutes),
                    context="other",
                    source="fingerstick",
                )
            )
        await session.commit()


# 六次落在最近的 110 分钟里，最后一条是 10 分钟前。
_SIX_IN_A_ROW = (10, 30, 50, 70, 90, 110)


async def test_a_hit_becomes_a_queued_task() -> None:
    user_id = await _create_user("dispatch_hit")
    await _add_readings(user_id, offsets_minutes=_SIX_IN_A_ROW)

    async with session_factory() as session:
        created = await queue_behavior_signals(
            session, user_id=user_id, now=NOW, timezone_name="Asia/Shanghai"
        )
        await session.commit()

    assert len(created) == 1
    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, created[0])
        assert task is not None
        assert task.kind == ProactiveCareTaskKind.BEHAVIOR_SIGNAL
        assert task.health_event_key == behavior_event_key("repeated_check")
        assert task.rule_id == BEHAVIOR_SIGNAL_RULE_ID
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        evidence = task.health_evidence
        assert isinstance(evidence, dict)
        assert evidence["kind"] == "repeated_check"
        assert evidence["occurred"] == 6


async def test_one_waiting_task_per_topic_is_enough() -> None:
    user_id = await _create_user("dispatch_twice")
    await _add_readings(user_id, offsets_minutes=_SIX_IN_A_ROW)

    async with session_factory() as session:
        first = await queue_behavior_signals(
            session, user_id=user_id, now=NOW, timezone_name="Asia/Shanghai"
        )
        await session.commit()
    async with session_factory() as session:
        second = await queue_behavior_signals(
            session, user_id=user_id, now=NOW, timezone_name="Asia/Shanghai"
        )
        await session.commit()

    assert len(first) == 1
    assert second == []


async def test_a_topic_mentioned_yesterday_is_held_back() -> None:
    user_id = await _create_user("dispatch_cooldown")
    await _add_readings(user_id, offsets_minutes=_SIX_IN_A_ROW)
    async with session_factory() as session:
        session.add(
            ProactiveCareTask(
                user_id=user_id,
                kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
                status=ProactiveCareTaskStatus.COMPLETED,
                due_at=NOW - timedelta(days=1, hours=1),
                expires_at=NOW - timedelta(hours=23),
                health_event_key=behavior_event_key("repeated_check"),
                health_evidence={"kind": "repeated_check"},
                finished_at=NOW - timedelta(days=1),
            )
        )
        await session.commit()

    async with session_factory() as session:
        created = await queue_behavior_signals(
            session, user_id=user_id, now=NOW, timezone_name="Asia/Shanghai"
        )
        await session.commit()

    assert created == []


async def test_calibration_records_what_would_have_been_sent() -> None:
    user_id = await _create_user("dispatch_calibration")
    await _add_readings(user_id, offsets_minutes=_SIX_IN_A_ROW)
    async with session_factory() as session:
        settings = await session.get(ProactiveCareSettings, user_id)
        assert settings is not None
        settings.behavior_calibration_until = NOW + timedelta(days=7)
        await session.commit()

    async with session_factory() as session:
        created = await queue_behavior_signals(
            session, user_id=user_id, now=NOW, timezone_name="Asia/Shanghai"
        )
        await session.commit()

    assert created == []
    async with session_factory() as session:
        records = list(
            (
                await session.scalars(
                    select(BehaviorSignalRecord).where(
                        BehaviorSignalRecord.user_id == user_id
                    )
                )
            ).all()
        )
    assert len(records) == 1
    assert records[0].kind == "repeated_check"
    assert records[0].dispatched is False
    assert records[0].suppressed_reason == "calibration"

    # 校准结束后，同一条痕迹就该真的排任务了。
    async with session_factory() as session:
        settings = await session.get(ProactiveCareSettings, user_id)
        assert settings is not None
        settings.behavior_calibration_until = None
        await session.commit()
    async with session_factory() as session:
        created = await queue_behavior_signals(
            session,
            user_id=user_id,
            now=NOW,
            timezone_name="Asia/Shanghai",
        )
        await session.commit()
    assert len(created) == 1


async def test_a_silent_low_rides_along_with_the_signal() -> None:
    user_id = await _create_user("dispatch_silent_link")
    await _add_readings(user_id, offsets_minutes=_SIX_IN_A_ROW)
    async with session_factory() as session:
        session.add(
            RiskAnnotation(
                user_id=user_id,
                measured_at=NOW - timedelta(days=2),
                value_mg_dl=58,
                physiological_risk="moderate",
                felt_risk="none",
                label="silent",
            )
        )
        await session.commit()

    async with session_factory() as session:
        created = await queue_behavior_signals(
            session, user_id=user_id, now=NOW, timezone_name="Asia/Shanghai"
        )
        await session.commit()

    assert len(created) == 1
    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, created[0])
        assert task is not None
        assert task.health_evidence is not None
        assert task.health_evidence["silent_lows_30d"] == 1


async def test_the_sweep_only_looks_at_recent_data_and_the_switch() -> None:
    busy = await _create_user("dispatch_busy")
    await _add_readings(busy, offsets_minutes=_SIX_IN_A_ROW)
    quiet = await _create_user("dispatch_quiet")
    await _add_readings(
        quiet, offsets_minutes=(60 * 30, 60 * 31, 60 * 32, 60 * 33, 60 * 34, 60 * 35)
    )
    switched_off = await _create_user("dispatch_off")
    await _add_readings(switched_off, offsets_minutes=_SIX_IN_A_ROW)
    async with session_factory() as session:
        settings = await session.get(ProactiveCareSettings, switched_off)
        assert settings is not None
        settings.health_events_enabled = False
        await session.commit()

    async with session_factory() as session:
        created = await sweep_behavior_signals(session, now=NOW)
        await session.commit()

    async with session_factory() as session:
        tasks = list(
            (
                await session.scalars(
                    select(ProactiveCareTask).where(
                        ProactiveCareTask.kind == ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
                        ProactiveCareTask.user_id.in_([busy, quiet, switched_off]),
                    )
                )
            ).all()
        )
    assert created
    assert [task.user_id for task in tasks] == [busy]


class _RecordingGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, *_args: object, **_kwargs: object) -> Any:
        self.calls += 1
        raise AssertionError("痕迹已经不成立时不该开始起草")


class _UnusedAuditor:
    async def ainvoke(self, *_args: object, **_kwargs: object) -> Any:
        raise AssertionError("不该进入审核")


def _processor(graph: object) -> ProactiveCareProcessor:
    return ProactiveCareProcessor(
        agent=cast(Any, graph),
        auditor=cast(Any, _UnusedAuditor()),
        companion_memory=cast(Any, object()),
        token_model=cast(Any, object()),
        model_name="test-model",
    )


async def _waiting_behavior_tasks() -> list[ProactiveCareTask]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(ProactiveCareTask).where(
                        ProactiveCareTask.kind == ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
                        ProactiveCareTask.status.in_(
                            [
                                ProactiveCareTaskStatus.SCHEDULED,
                                ProactiveCareTaskStatus.RUNNING,
                            ]
                        ),
                    )
                )
            ).all()
        )


async def _clear_waiting_behavior_tasks() -> None:
    """别的用例留下的等待任务会被 Worker 先领走，这里清掉，保证只测自己那条。"""

    from sqlalchemy import delete

    async with session_factory() as session:
        await session.execute(
            delete(ProactiveCareTask).where(
                ProactiveCareTask.kind == ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
                ProactiveCareTask.status.in_(
                    [
                        ProactiveCareTaskStatus.SCHEDULED,
                        ProactiveCareTaskStatus.RUNNING,
                    ]
                ),
            )
        )
        await session.commit()


def _worker(processor: ProactiveCareProcessor) -> ProactiveCareWorker:
    # 这些用例只关心「领到的那一条」，所以关掉扫描，免得顺手给别人排任务。
    worker = ProactiveCareWorker(
        process_task=processor.process,
        behavior_sweep_interval=timedelta(days=1),
    )
    return worker


async def test_a_signal_that_no_longer_holds_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _clear_waiting_behavior_tasks()
    user_id = await _create_user("dispatch_gone")
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=user_id,
            kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(hours=6),
            health_event_key=behavior_event_key("repeated_check"),
            health_evidence={"kind": "repeated_check"},
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def empty(*_args: object, **_kwargs: object) -> list[BehaviorSignal]:
        return []

    monkeypatch.setattr(
        "antang_api.proactive_care.processor.behavior_signals", empty
    )
    graph = _RecordingGraph()

    assert (
        await _worker(_processor(graph)).run_once(now=NOW)
        is True
    )

    async with session_factory() as session:
        finished = await session.get(ProactiveCareTask, task_id)
        assert finished is not None
        assert finished.status == ProactiveCareTaskStatus.SKIPPED
        assert finished.outcome_reason == "behavior_signal_gone"
    assert graph.calls == 0


async def test_the_weekly_quota_is_not_rearranged_for_a_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _clear_waiting_behavior_tasks()
    user_id = await _create_user("dispatch_quota")
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=user_id,
            kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(hours=6),
            health_event_key=behavior_event_key("repeated_check"),
            health_evidence={"kind": "repeated_check"},
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def still_there(*_args: object, **_kwargs: object) -> list[BehaviorSignal]:
        return [
            BehaviorSignal(
                kind="repeated_check",
                label="同一段三小时里测得比平时密",
                occurred=6,
                window_days=1,
                evidence=(),
            )
        ]

    async def exhausted(*_args: object, **_kwargs: object) -> WeeklyQuota:
        return WeeklyQuota(limit=1, used=1, resumes_at=NOW + timedelta(days=2))

    monkeypatch.setattr(
        "antang_api.proactive_care.processor.behavior_signals", still_there
    )
    monkeypatch.setattr("antang_api.proactive_care.processor.weekly_quota", exhausted)

    await _worker(_processor(_RecordingGraph())).run_once(now=NOW)

    async with session_factory() as session:
        finished = await session.get(ProactiveCareTask, task_id)
        assert finished is not None
        assert finished.status == ProactiveCareTaskStatus.EXPIRED
        assert finished.outcome_reason == "weekly_contact_limit_expired"
