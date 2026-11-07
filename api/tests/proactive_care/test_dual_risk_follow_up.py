"""双风险回访：只有「低了没感觉」那一档才排，排了就去问，问不到就作废。

测五件事：只有 silent 才需要回访；排出来的任务三天后到期、话题键带标注 id；
同一个标注不排第二个；开关关着或者补记的是很久以前的事就不排；运行到那一步时
标注已经被删掉，任务作废且不开始起草。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from antang_api.database import session_factory
from antang_api.models import (
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    RiskAnnotation,
    User,
)
from antang_api.proactive_care.processor import ProactiveCareProcessor
from antang_api.proactive_care.reasons import message_reason
from antang_api.proactive_care.worker import ProactiveCareWorker
from antang_api.psychosocial.dual_risk import (
    FOLLOW_UP_AFTER,
    FOLLOW_UP_RULE_ID,
    FOLLOW_UP_TTL,
    follow_up_topic,
    needs_follow_up,
    queue_follow_up,
)

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
async def _clean_up_test_users():
    yield
    async with session_factory() as session:
        await session.execute(
            delete(User).where(User.username.like("dualrisk\\_%", escape="\\"))
        )
        await session.commit()


async def _create_user(prefix: str, *, health_events: bool = True) -> UUID:
    async with session_factory() as session:
        username = f"{prefix}_{uuid4().hex[:8]}"
        user = User(
            username=username,
            username_normalized=username,
            password_hash="test-only-password-hash",
        )
        session.add(user)
        await session.flush()
        session.add(
            ProactiveCareSettings(
                user_id=user.id,
                health_events_enabled=health_events,
                quiet_hours_start=time(0, 0),
                quiet_hours_end=time(0, 0),
            )
        )
        await session.commit()
        return user.id


async def _add_annotation(
    user_id: UUID,
    *,
    label: str = "silent",
    measured_at: datetime = NOW - timedelta(hours=2),
    value_mg_dl: int | None = 58,
) -> UUID:
    async with session_factory() as session:
        annotation = RiskAnnotation(
            user_id=user_id,
            measured_at=measured_at,
            value_mg_dl=value_mg_dl,
            physiological_risk="moderate",
            felt_risk="none" if label == "silent" else "some",
            label=label,
        )
        session.add(annotation)
        await session.commit()
        return annotation.id


def test_only_the_silent_case_is_worth_coming_back_to() -> None:
    class _Row:
        def __init__(self, label: str) -> None:
            self.label = label

    assert needs_follow_up(cast(Any, _Row("silent"))) is True
    for other in ("both", "felt_only", "neither"):
        assert needs_follow_up(cast(Any, _Row(other))) is False


async def test_a_silent_low_gets_one_question_three_days_later() -> None:
    user_id = await _create_user("dualrisk_queue")
    measured_at = NOW - timedelta(hours=2)
    annotation_id = await _add_annotation(user_id, measured_at=measured_at)

    async with session_factory() as session:
        annotation = await session.get(RiskAnnotation, annotation_id)
        assert annotation is not None
        task = await queue_follow_up(
            session, user_id=user_id, annotation=annotation, now=NOW
        )
        await session.commit()

    assert task is not None
    assert task.kind == ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP
    assert task.status == ProactiveCareTaskStatus.SCHEDULED
    assert task.due_at == measured_at + FOLLOW_UP_AFTER
    assert task.expires_at == task.due_at + FOLLOW_UP_TTL
    assert task.rule_id == FOLLOW_UP_RULE_ID
    assert task.health_event_key == f"dual-risk:{annotation_id}"
    assert task.health_event_key == follow_up_topic(annotation)
    assert task.wearable_import_id is None
    assert task.care_plan_id is None
    evidence = task.health_evidence
    assert evidence is not None
    assert evidence["value_mg_dl"] == 58
    assert evidence["annotation_id"] == str(annotation_id)


async def test_the_same_night_is_not_queued_twice() -> None:
    user_id = await _create_user("dualrisk_once")
    annotation_id = await _add_annotation(user_id)

    async with session_factory() as session:
        annotation = await session.get(RiskAnnotation, annotation_id)
        assert annotation is not None
        first = await queue_follow_up(
            session, user_id=user_id, annotation=annotation, now=NOW
        )
        second = await queue_follow_up(
            session, user_id=user_id, annotation=annotation, now=NOW
        )
        await session.commit()

    assert first is not None
    assert second is None
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(ProactiveCareTask).where(
                        ProactiveCareTask.user_id == user_id,
                        ProactiveCareTask.kind
                        == ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP,
                    )
                )
            ).all()
        )
    assert len(rows) == 1


async def test_an_old_backfill_or_a_switched_off_switch_queues_nothing() -> None:
    old_user = await _create_user("dualrisk_old")
    old_id = await _add_annotation(old_user, measured_at=NOW - timedelta(days=10))
    off_user = await _create_user("dualrisk_off", health_events=False)
    off_id = await _add_annotation(off_user)
    felt_user = await _create_user("dualrisk_felt")
    felt_id = await _add_annotation(felt_user, label="both")

    async with session_factory() as session:
        for user_id, annotation_id in (
            (old_user, old_id),
            (off_user, off_id),
            (felt_user, felt_id),
        ):
            annotation = await session.get(RiskAnnotation, annotation_id)
            assert annotation is not None
            assert (
                await queue_follow_up(
                    session, user_id=user_id, annotation=annotation, now=NOW
                )
                is None
            )
        await session.commit()


def test_the_reason_says_we_are_coming_back_to_that_night() -> None:
    task = ProactiveCareTask(
        kind=ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP,
        health_evidence={"measured_at": (NOW - timedelta(days=3)).isoformat()},
    )
    reason = message_reason(task, now=NOW, timezone_name="Asia/Shanghai")
    assert "没什么感觉" in reason
    assert "后来" in reason
    # 原因句不复述数值，也不报次数。
    assert "3." not in reason
    assert "次" not in reason


class _RecordingGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, *_args: object, **_kwargs: object) -> Any:
        self.calls += 1
        raise AssertionError("标注已经不在了，不该开始起草")


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


async def _clear_waiting_tasks() -> None:
    """别的用例留下的等待任务会被 Worker 先领走，这里清掉，保证只测自己那条。"""

    async with session_factory() as session:
        await session.execute(
            delete(ProactiveCareTask).where(
                ProactiveCareTask.kind == ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP,
                ProactiveCareTask.status.in_(
                    [
                        ProactiveCareTaskStatus.SCHEDULED,
                        ProactiveCareTaskStatus.RUNNING,
                    ]
                ),
            )
        )
        await session.commit()


async def test_a_deleted_annotation_cancels_the_question() -> None:
    await _clear_waiting_tasks()
    user_id = await _create_user("dualrisk_deleted")
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=user_id,
            kind=ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=NOW - timedelta(seconds=1),
            expires_at=NOW + timedelta(hours=6),
            health_event_key=f"dual-risk:{uuid4()}",
            health_evidence={"annotation_id": str(uuid4())},
            rule_id=FOLLOW_UP_RULE_ID,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    graph = _RecordingGraph()
    worker = ProactiveCareWorker(
        process_task=_processor(graph).process,
        behavior_sweep_interval=timedelta(days=1),
    )
    assert await worker.run_once(now=NOW) is True

    async with session_factory() as session:
        finished = await session.get(ProactiveCareTask, task_id)
        assert finished is not None
        assert finished.status == ProactiveCareTaskStatus.SKIPPED
        assert finished.outcome_reason == "dual_risk_gone"
    assert graph.calls == 0
