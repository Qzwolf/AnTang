"""打扰预算：推送限流、连续没回应就降频/暂停、锁屏文案不带正文。"""

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select

from antang_api.database import session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    LoginSession,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    PushPermissionState,
    PushPlatform,
    RoutineCareCadence,
)
from antang_api.agents.proactive_care import PROACTIVE_CARE_AGENT_NAME
from antang_api.proactive_care.push import (
    PushDeliveryWorker,
    _care_notification_body,
)
from antang_api.proactive_care.service import schedule_routine_check_in


class FakeExpoClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send(self, message: dict[str, object]) -> tuple[str, str | None]:
        self.sent.append(message)
        return "ok", "ticket-budget"

    async def get_receipt(self, ticket_id: str) -> tuple[str, str | None]:
        return "ok", None


async def _add_unanswered_check_in(
    session: Any,
    user_id: UUID,
    *,
    now: datetime,
    minutes_ago: int,
) -> None:
    task = ProactiveCareTask(
        user_id=user_id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.COMPLETED,
        due_at=now - timedelta(minutes=minutes_ago),
        expires_at=now - timedelta(minutes=minutes_ago) + timedelta(hours=12),
        response_message_id=None,
        finished_at=now - timedelta(minutes=minutes_ago),
    )
    session.add(task)


async def _set_daily(user_id: UUID) -> None:
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.DAILY
        await session.commit()


async def test_two_unanswered_check_ins_downgrade_daily_to_every_three_days(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    await _set_daily(committed_user_id)
    async with session_factory() as session:
        await _add_unanswered_check_in(
            session, committed_user_id, now=now, minutes_ago=1500
        )
        await _add_unanswered_check_in(
            session, committed_user_id, now=now, minutes_ago=10
        )
        await session.commit()

        await schedule_routine_check_in(
            session,
            user_id=committed_user_id,
            after=now,
            reset_existing=False,
        )
        await session.commit()

        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        assert care_settings.routine_cadence == RoutineCareCadence.EVERY_3_DAYS
        assert care_settings.auto_downgraded_at is not None
        assert care_settings.auto_paused_at is None
        assert care_settings.unanswered_contacts == 2
        scheduled = await session.scalar(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == committed_user_id,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
        )
        assert scheduled is not None
        assert scheduled.due_at == now + timedelta(days=3)


async def test_a_third_silent_week_pauses_the_care_instead_of_pinging_again(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    await _set_daily(committed_user_id)
    async with session_factory() as session:
        for minutes_ago in (3000, 1500, 10):
            await _add_unanswered_check_in(
                session, committed_user_id, now=now, minutes_ago=minutes_ago
            )
        await session.commit()

        scheduled_id = await schedule_routine_check_in(
            session,
            user_id=committed_user_id,
            after=now,
            reset_existing=False,
        )
        await session.commit()

        assert scheduled_id is None
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        assert care_settings.routine_cadence == RoutineCareCadence.DISABLED
        assert care_settings.auto_paused_at is not None
        assert care_settings.unanswered_contacts == 3


async def test_talking_again_clears_the_silent_streak(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    await _set_daily(committed_user_id)
    async with session_factory() as session:
        await _add_unanswered_check_in(
            session, committed_user_id, now=now, minutes_ago=1500
        )
        await _add_unanswered_check_in(
            session, committed_user_id, now=now, minutes_ago=10
        )
        await session.commit()

        await schedule_routine_check_in(
            session,
            user_id=committed_user_id,
            after=now,
            reset_existing=True,
        )
        await session.commit()

        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        assert care_settings.unanswered_contacts == 0
        assert care_settings.routine_cadence == RoutineCareCadence.DAILY
        assert care_settings.auto_downgraded_at is None


async def _make_pending_delivery(
    session: Any,
    user_id: UUID,
    *,
    now: datetime,
) -> UUID:
    login_session = LoginSession(
        user_id=user_id,
        refresh_token_hash=uuid4().hex + uuid4().hex,
        expires_at=now + timedelta(days=30),
    )
    message = Message(
        user_id=user_id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="今天感觉怎么样？",
        completed_at=now,
    )
    session.add_all([login_session, message])
    await session.flush()
    installation = PushInstallation(
        id=uuid4(),
        login_session_id=login_session.id,
        expo_push_token=f"ExponentPushToken[{uuid4().hex}]",
        registration_revision=1,
        permission=PushPermissionState.GRANTED,
        platform=PushPlatform.ANDROID,
        app_version="1.0.0",
        last_seen_at=now,
    )
    session.add(installation)
    await session.flush()
    task = ProactiveCareTask(
        user_id=user_id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.COMPLETED,
        due_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        finished_at=now,
    )
    session.add(task)
    await session.flush()
    run = AgentRun(
        user_id=user_id,
        trigger_message_id=None,
        trigger_care_task_id=task.id,
        result_message_id=message.id,
        parent_run_id=None,
        agent_name=PROACTIVE_CARE_AGENT_NAME,
        model="test-model",
        status=AgentRunStatus.COMPLETED,
        finished_at=now,
    )
    delivery = PushDelivery(
        message_id=message.id,
        installation_id=installation.id,
        installation_revision=1,
        show_message_preview=True,
        status=PushDeliveryStatus.PENDING,
        next_attempt_at=now,
        expires_at=now + timedelta(hours=1),
    )
    session.add_all([run, delivery])
    await session.flush()
    return delivery.id


async def test_a_second_lock_screen_notification_within_a_day_is_held_back(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        first = await _make_pending_delivery(session, committed_user_id, now=now)
        await session.commit()
        client = FakeExpoClient()
        worker = PushDeliveryWorker(client=cast(Any, client))
        assert await worker.run_once(now=now) is True

        second = await _make_pending_delivery(
            session, committed_user_id, now=now + timedelta(hours=2)
        )
        await session.commit()
        assert await worker.run_once(now=now + timedelta(hours=2)) is True

    assert len(client.sent) == 1
    async with session_factory() as session:
        sent = await session.get(PushDelivery, first)
        held = await session.get(PushDelivery, second)
        assert sent is not None and held is not None
        assert sent.status == PushDeliveryStatus.TICKET_ACCEPTED
        assert held.status == PushDeliveryStatus.FAILED
        assert held.provider_error_code == "interruption_budget"


async def test_the_lock_screen_copy_uses_the_local_greeting() -> None:
    # 称呼来自账号本身，代码里没有任何默认名字；空账号就只说问候。
    morning = _care_notification_body(
        name="Lily", local_now=datetime(2026, 10, 25, 7, 30)
    )
    assert morning == "Lily，早上好。糖糖想跟你聊两句，可以吗？"
    noon = _care_notification_body(name="Lily", local_now=datetime(2026, 10, 25, 12, 0))
    assert noon.startswith("Lily，中午好。")
    evening = _care_notification_body(
        name="Lily", local_now=datetime(2026, 10, 25, 21, 30)
    )
    assert evening.startswith("Lily，晚上好。")
    anonymous = _care_notification_body(
        name="  ", local_now=datetime(2026, 10, 25, 7, 30)
    )
    assert anonymous == "早上好。糖糖想跟你聊两句，可以吗？"
