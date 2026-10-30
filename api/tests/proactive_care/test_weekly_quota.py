"""每周主动联系额度的闸门：数近七天真的发出去几条，到上限就往后推。"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.proactive_care import PROACTIVE_CARE_AGENT_NAME
from antang_api.database import session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
)
from antang_api.proactive_care.quota import (
    DEFAULT_WEEKLY_LIMIT,
    WEEK,
    weekly_quota,
)
from antang_api.psychosocial.support import PsychosocialProfile


async def _seed_sent_contact(
    session: AsyncSession,
    *,
    user_id: UUID,
    sent_at: datetime,
) -> None:
    """造一条「真的发出去过」的主动消息：消息 + 任务 + 连起来的运行记录。"""

    message = Message(
        user_id=user_id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="上周的问候。",
        created_at=sent_at,
        completed_at=sent_at,
    )
    task = ProactiveCareTask(
        user_id=user_id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.COMPLETED,
        due_at=sent_at,
        expires_at=sent_at + timedelta(hours=12),
        finished_at=sent_at,
    )
    session.add_all([message, task])
    await session.flush()
    session.add(
        AgentRun(
            user_id=user_id,
            trigger_message_id=None,
            trigger_care_task_id=task.id,
            result_message_id=message.id,
            parent_run_id=None,
            agent_name=PROACTIVE_CARE_AGENT_NAME,
            model="test-model",
            status=AgentRunStatus.COMPLETED,
            finished_at=sent_at,
        )
    )


async def test_without_an_assessment_the_usual_middle_dose_applies(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for hours_ago in (72, 48, 24):
            await _seed_sent_contact(
                session,
                user_id=committed_user_id,
                sent_at=now - timedelta(hours=hours_ago),
            )
        await session.commit()

    async with session_factory() as session:
        quota = await weekly_quota(
            session,
            user_id=committed_user_id,
            now=now,
            profile=None,
        )

    assert quota.limit == DEFAULT_WEEKLY_LIMIT
    assert quota.used == 3
    assert quota.exhausted is True
    # 最早那条（三天前）掉出七天窗口时才腾出名额。
    assert quota.resumes_at is not None
    assert quota.resumes_at == now - timedelta(hours=72) + WEEK


async def test_the_assessment_quadrant_sets_how_many_contacts_a_week(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    # 怕低血糖、又感觉不到低血糖：剂量最高的一格。
    impaired = PsychosocialProfile(
        fear_behavior_percent=90,
        fear_worry_percent=90,
        gold_score=6,
    )
    assert impaired.weekly_contact_limit == 4
    async with session_factory() as session:
        for days_ago in (5, 4, 3):
            await _seed_sent_contact(
                session,
                user_id=committed_user_id,
                sent_at=now - timedelta(days=days_ago),
            )
        await session.commit()

    async with session_factory() as session:
        room = await weekly_quota(
            session,
            user_id=committed_user_id,
            now=now,
            profile=impaired,
        )

    assert room.limit == 4
    assert room.used == 3
    assert room.exhausted is False
    assert room.resumes_at is None

    async with session_factory() as session:
        await _seed_sent_contact(
            session,
            user_id=committed_user_id,
            sent_at=now - timedelta(hours=6),
        )
        await session.commit()

    async with session_factory() as session:
        full = await weekly_quota(
            session,
            user_id=committed_user_id,
            now=now,
            profile=impaired,
        )

    assert full.exhausted is True
    assert full.resumes_at == now - timedelta(days=5) + WEEK


async def test_a_contact_older_than_a_week_does_not_take_a_slot(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        await _seed_sent_contact(
            session,
            user_id=committed_user_id,
            sent_at=now - timedelta(days=8),
        )
        await _seed_sent_contact(
            session,
            user_id=committed_user_id,
            sent_at=now - timedelta(days=2),
        )
        await session.commit()

    async with session_factory() as session:
        quota = await weekly_quota(
            session,
            user_id=committed_user_id,
            now=now,
            profile=None,
        )

    assert quota.used == 1
    assert quota.exhausted is False
