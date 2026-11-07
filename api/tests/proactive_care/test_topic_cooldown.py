"""话题冷却：同一话题刚主动提过就往后推。"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from antang_api.database import session_factory
from antang_api.models import (
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    WearableImport,
    WearableRecordType,
)
from antang_api.proactive_care.heart_rate_shadow import HeartRateShadowResult
from antang_api.proactive_care.processor import ProactiveCareProcessor
from antang_api.proactive_care.topics import (
    TOPIC_COOLDOWN,
    topic_cooldown_until,
    topic_prefix,
)
from antang_api.proactive_care.worker import ProactiveCareWorker


def test_a_topic_keeps_the_rule_version_and_direction() -> None:
    assert (
        topic_prefix("heart_rate_v1:low:2026-11-02T03:00:00+00:00")
        == "heart_rate_v1:low:"
    )
    # 高与低是两个话题。
    assert topic_prefix("heart_rate_v1:high:2026-11-02T03:00:00+00:00") != topic_prefix(
        "heart_rate_v1:low:2026-11-02T03:00:00+00:00"
    )


@pytest.mark.parametrize("value", [None, "", "heart_rate_v1", "heart_rate_v1:", ":low:2026"])
def test_a_key_that_cannot_be_read_has_no_topic(value: str | None) -> None:
    assert topic_prefix(value) is None


async def _seed_completed_event(
    user_id: UUID,
    *,
    event_key: str,
    finished_at: datetime,
    status: ProactiveCareTaskStatus = ProactiveCareTaskStatus.COMPLETED,
) -> UUID:
    """每个事件任务都配一条自己的导入记录：一条导入只允许挂一个任务。"""

    async with session_factory() as session:
        imported = WearableImport(
            user_id=user_id,
            client_sync_id=uuid4(),
            request_hash=uuid4().hex + uuid4().hex,
            record_type=WearableRecordType.HEART_RATE,
            health_context_complete=True,
            records_created=1,
            records_updated=0,
            records_unchanged=0,
            records_deleted=0,
            created_at=finished_at,
        )
        session.add(imported)
        await session.flush()
        task = ProactiveCareTask(
            user_id=user_id,
            kind=ProactiveCareTaskKind.HEALTH_EVENT,
            status=status,
            due_at=finished_at - timedelta(hours=1),
            expires_at=finished_at + timedelta(days=1),
            wearable_import_id=imported.id,
            health_event_key=event_key,
            finished_at=finished_at,
        )
        session.add(task)
        await session.commit()
        return task.id


async def test_a_topic_sent_yesterday_waits_out_the_cooldown(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    finished_at = now - timedelta(days=1)
    await _seed_completed_event(
        committed_user_id,
        event_key="heart_rate_v1:low:2026-11-01T03:00:00+00:00",
        finished_at=finished_at,
    )

    async with session_factory() as session:
        resumes_at = await topic_cooldown_until(
            session,
            user_id=committed_user_id,
            prefix=topic_prefix("heart_rate_v1:low:2026-11-02T03:00:00+00:00"),
            now=now,
        )

    assert resumes_at == finished_at + TOPIC_COOLDOWN


async def test_the_other_direction_and_old_news_do_not_hold_a_topic_back(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    await _seed_completed_event(
        committed_user_id,
        event_key="heart_rate_v1:high:2026-11-01T03:00:00+00:00",
        finished_at=now - timedelta(days=1),
    )
    await _seed_completed_event(
        committed_user_id,
        event_key="heart_rate_v1:low:2026-10-01T03:00:00+00:00",
        finished_at=now - timedelta(days=30),
    )
    # 被跳过的那次不算「提过」。
    await _seed_completed_event(
        committed_user_id,
        event_key="heart_rate_v1:low:2026-11-01T09:00:00+00:00",
        finished_at=now - timedelta(hours=2),
        status=ProactiveCareTaskStatus.SKIPPED,
    )

    async with session_factory() as session:
        resumes_at = await topic_cooldown_until(
            session,
            user_id=committed_user_id,
            prefix=topic_prefix("heart_rate_v1:low:2026-11-02T03:00:00+00:00"),
            now=now,
        )

    assert resumes_at is None


class _RecordingGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, *_args: object, **_kwargs: object) -> Any:
        self.calls += 1
        raise AssertionError("冷却期内不该开始起草")


class _UnusedAuditor:
    async def ainvoke(self, *_args: object, **_kwargs: object) -> Any:
        raise AssertionError("冷却期内不该进入审核")


async def test_the_processor_moves_the_alert_instead_of_drafting_it(
    committed_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    finished_at = now - timedelta(days=1)
    await _seed_completed_event(
        committed_user_id,
        event_key="heart_rate_v1:low:2026-11-01T03:00:00+00:00",
        finished_at=finished_at,
    )

    async with session_factory() as session:
        settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert settings is not None
        settings.health_events_enabled = True
        settings.timezone = "Asia/Shanghai"
        settings.quiet_hours_start = time(0, 0)
        settings.quiet_hours_end = time(0, 0)
        imported = WearableImport(
            user_id=committed_user_id,
            client_sync_id=uuid4(),
            request_hash=uuid4().hex + uuid4().hex,
            record_type=WearableRecordType.HEART_RATE,
            health_context_complete=True,
            records_created=1,
            records_updated=0,
            records_unchanged=0,
            records_deleted=0,
            created_at=now,
        )
        session.add(imported)
        await session.flush()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.HEALTH_EVENT,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(seconds=1),
            # 冷却期结束还在有效期内，所以是「往后推」而不是「过期作废」。
            expires_at=now + timedelta(days=7),
            wearable_import_id=imported.id,
            source_version_hash=uuid4().hex + uuid4().hex,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def fake_shadow(*_args: object, **_kwargs: object) -> HeartRateShadowResult:
        return HeartRateShadowResult(
            decision="candidate",
            reason="candidate",
            received_at=now,
            age_years=30,
            context_complete=True,
            timezone="Asia/Shanghai",
            source_package="com.huami.watch.hmwatchmanager",
            recording_method=2,
            minute_aggregation="median",
            direction="low",
            event_key="heart_rate_v1:low:2026-11-02T03:00:00+00:00",
        )

    monkeypatch.setattr(
        "antang_api.proactive_care.processor.evaluate_heart_rate_shadow",
        fake_shadow,
    )

    graph = _RecordingGraph()
    processor = ProactiveCareProcessor(
        agent=cast(Any, graph),
        auditor=cast(Any, _UnusedAuditor()),
        companion_memory=cast(Any, object()),
        token_model=cast(Any, object()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        moved = await session.get(ProactiveCareTask, task_id)
        assert moved is not None
        assert moved.status == ProactiveCareTaskStatus.SCHEDULED
        assert moved.outcome_reason == "topic_cooldown"
        assert moved.due_at == finished_at + TOPIC_COOLDOWN
    assert graph.calls == 0
