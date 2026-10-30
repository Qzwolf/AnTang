import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from antang_api.agents.proactive_care import (
    PROACTIVE_CARE_AGENT_NAME,
    PROACTIVE_CARE_AUDITOR_NAME,
    ProactiveCareAudit,
    ProactiveCareDecision,
)
from antang_api.chat import prepare_chat_run
from antang_api.database import session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    CarePlan,
    CarePlanStatus,
    FearBehaviorLog,
    HypoglycemiaBeliefEvent,
    LoginSession,
    Message,
    MessageRole,
    MessageStatus,
    PersonalProfile,
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
    WearableImport,
    WearableObservation,
    WearableRecordType,
)
from antang_api.proactive_care.processor import (
    HEART_RATE_SAFETY_TEXT,
    ProactiveCareProcessor,
)
from antang_api.proactive_care.worker import ProactiveCareWorker
from antang_api.settings import settings as app_settings


class FakeGraph:
    def __init__(
        self,
        response: ProactiveCareDecision | ProactiveCareAudit,
        *,
        before_return: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.response = response
        self.before_return = before_return
        self.calls: list[tuple[dict[str, Any], dict[str, Any], object]] = []

    async def ainvoke(
        self,
        input: dict[str, Any],
        *,
        config: dict[str, Any],
        context: object,
    ) -> dict[str, Any]:
        self.calls.append((input, config, context))
        if self.before_return is not None:
            await self.before_return()
        return {
            "structured_response": self.response,
            "messages": [
                AIMessage(
                    content="",
                    usage_metadata={
                        "input_tokens": 11,
                        "output_tokens": 3,
                        "total_tokens": 14,
                    },
                )
            ],
        }


class FakeMemory:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    async def recall_for_proactive(
        self,
        session: object,
        user_id: UUID,
        *,
        query: str,
        query_timestamp: datetime,
    ) -> str:
        del session, query_timestamp
        self.calls.append((user_id, query))
        return "- 用户以前说，简短问候更容易回复。"


class FailingMemory(FakeMemory):
    async def recall_for_proactive(
        self,
        session: object,
        user_id: UUID,
        *,
        query: str,
        query_timestamp: datetime,
    ) -> str:
        del session, user_id, query, query_timestamp
        raise RuntimeError("Hindsight unavailable")


class FakeTokenModel:
    def get_num_tokens_from_messages(
        self,
        messages: list[object],
        tools: list[object] | None = None,
    ) -> int:
        del messages, tools
        raise NotImplementedError("configured model does not expose a tokenizer")


async def test_first_heart_rate_candidate_creates_private_health_message(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        settings = await session.get(ProactiveCareSettings, committed_user_id)
        profile = await session.get(PersonalProfile, committed_user_id)
        assert settings is not None
        assert profile is not None
        settings.health_events_enabled = True
        settings.timezone = "Asia/Shanghai"
        settings.quiet_hours_start = datetime.min.time()
        settings.quiet_hours_end = datetime.min.time()
        profile.age_years = 30
        login_session = LoginSession(
            user_id=committed_user_id,
            refresh_token_hash=uuid4().hex + uuid4().hex,
            expires_at=now + timedelta(days=30),
        )
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
        session.add_all([login_session, imported])
        await session.flush()
        installation = PushInstallation(
            id=uuid4(),
            login_session_id=login_session.id,
            expo_push_token="ExponentPushToken[health-processor]",
            registration_revision=2,
            permission=PushPermissionState.GRANTED,
            platform=PushPlatform.ANDROID,
            app_version="1.0.0",
            last_seen_at=now,
        )
        start = now - timedelta(minutes=30)
        observation = WearableObservation(
            user_id=committed_user_id,
            external_record_id=f"processor-heart-rate-{uuid4().hex}",
            record_type=WearableRecordType.HEART_RATE,
            start_time=start,
            end_time=now - timedelta(minutes=1),
            start_zone_offset_seconds=28800,
            end_zone_offset_seconds=28800,
            data={
                "samples": [
                    {
                        "time": (start + timedelta(minutes=index)).isoformat(),
                        "beats_per_minute": 110,
                    }
                    for index in range(30)
                ]
            },
            data_hash=uuid4().hex + uuid4().hex,
            source_package="com.huami.watch.hmwatchmanager",
            recording_method=2,
            device=None,
            source_last_modified_at=now,
            last_import_id=imported.id,
        )
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.HEALTH_EVENT,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(hours=24),
            wearable_import_id=imported.id,
            source_version_hash=imported.request_hash,
        )
        session.add_all([installation, observation, task])
        await session.commit()
        task_id = task.id
        installation_id = installation.id

    local_zone = ZoneInfo("Asia/Shanghai")
    local_start = start.astimezone(local_zone)
    local_end = (now - timedelta(minutes=1)).astimezone(local_zone)
    measurement_text = (
        f"手表在{local_start:%Y年%m月%d日 %H:%M}至"
        f"{local_end:%Y年%m月%d日 %H:%M}（Asia/Shanghai）"
        "记录到一段持续偏快的心率，该窗口中位数为110次/分，"
        "最低110次/分，最高110次/分。"
    )
    message_text = (
        f"{measurement_text}\n\n{HEART_RATE_SAFETY_TEXT}\n\n"
        "你现在感觉怎么样，有没有不舒服？"
    )
    draft = FakeGraph(
        ProactiveCareDecision(
            action="send",
            message="你现在感觉怎么样，有没有不舒服？",
            reason="首个心率候选需要关怀",
        )
    )
    audit = FakeGraph(ProactiveCareAudit(decision="approve", reason="事实完整且安全"))
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft),
        auditor=cast(Any, audit),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        completed_task = await session.get(ProactiveCareTask, task_id)
        assert completed_task is not None
        assert completed_task.status == ProactiveCareTaskStatus.COMPLETED
        assert completed_task.outcome_reason == "message_created"
        assert completed_task.rule_id == "heart_rate_window"
        assert completed_task.rule_version == "heart-rate-shadow-v0"
        assert completed_task.health_evidence is not None
        assert completed_task.health_evidence["decision"] == "candidate"
        assert completed_task.health_event_key is not None
        message = await session.scalar(
            select(Message).where(
                Message.user_id == committed_user_id,
                Message.role == MessageRole.ASSISTANT,
                Message.content == message_text,
            )
        )
        assert message is not None
        runs = list(
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.trigger_care_task_id == task_id)
                .order_by(AgentRun.id)
            )
        )
        assert [run.agent_name for run in runs] == [
            PROACTIVE_CARE_AGENT_NAME,
            PROACTIVE_CARE_AUDITOR_NAME,
        ]
        assert all(run.status == AgentRunStatus.COMPLETED for run in runs)
        delivery = await session.scalar(
            select(PushDelivery).where(PushDelivery.message_id == message.id)
        )
        assert delivery is not None
        assert delivery.installation_id == installation_id
        assert delivery.show_message_preview is False

    payload = json.loads(draft.calls[0][0]["messages"][0].content)
    assert payload["must_send"] is True
    assert payload["trigger"]["direction"] == "high"
    assert payload["trigger"]["required_verbatim"] == [
        measurement_text,
        HEART_RATE_SAFETY_TEXT,
    ]
    assert len(audit.calls) == 1
    audit_payload = json.loads(audit.calls[0][0]["messages"][0].content)
    assert audit_payload["draft"] == message_text


async def test_normal_heart_rate_result_stops_before_the_agent(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        settings = await session.get(ProactiveCareSettings, committed_user_id)
        profile = await session.get(PersonalProfile, committed_user_id)
        assert settings is not None
        assert profile is not None
        settings.health_events_enabled = True
        settings.quiet_hours_start = datetime.min.time()
        settings.quiet_hours_end = datetime.min.time()
        profile.age_years = 30
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
        start = now - timedelta(minutes=30)
        session.add_all(
            [
                WearableObservation(
                    user_id=committed_user_id,
                    external_record_id=f"processor-normal-heart-{uuid4().hex}",
                    record_type=WearableRecordType.HEART_RATE,
                    start_time=start,
                    end_time=now - timedelta(minutes=1),
                    start_zone_offset_seconds=28800,
                    end_zone_offset_seconds=28800,
                    data={
                        "samples": [
                            {
                                "time": (start + timedelta(minutes=index)).isoformat(),
                                "beats_per_minute": 80,
                            }
                            for index in range(30)
                        ]
                    },
                    data_hash=uuid4().hex + uuid4().hex,
                    source_package="com.huami.watch.hmwatchmanager",
                    recording_method=2,
                    device=None,
                    source_last_modified_at=now,
                    last_import_id=imported.id,
                ),
                ProactiveCareTask(
                    user_id=committed_user_id,
                    kind=ProactiveCareTaskKind.HEALTH_EVENT,
                    status=ProactiveCareTaskStatus.SCHEDULED,
                    due_at=now - timedelta(seconds=1),
                    expires_at=now + timedelta(hours=24),
                    wearable_import_id=imported.id,
                    source_version_hash=imported.request_hash,
                ),
            ]
        )
        await session.flush()
        task_id = await session.scalar(
            select(ProactiveCareTask.id).where(
                ProactiveCareTask.wearable_import_id == imported.id
            )
        )
        assert task_id is not None
        await session.commit()

    draft = FakeGraph(
        ProactiveCareDecision(action="skip", message=None, reason="不应调用")
    )
    audit = FakeGraph(ProactiveCareAudit(decision="approve", reason="不应调用"))
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft),
        auditor=cast(Any, audit),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )
    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SKIPPED
        assert task.outcome_reason == "shadow_threshold_not_sustained"
        assert task.health_evidence is not None
        assert task.health_evidence["decision"] == "no_match"
        assert (
            await session.scalar(
                select(AgentRun.id).where(AgentRun.trigger_care_task_id == task_id)
            )
            is None
        )
    assert draft.calls == []
    assert audit.calls == []


async def test_plan_follow_up_uses_the_current_plan_and_creates_ai_message(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.plan_follow_up_enabled = True
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        authorization = Message(
            user_id=committed_user_id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="好，明天问我散步完成没有。",
            completed_at=now - timedelta(days=1),
        )
        recent_routine_message = Message(
            user_id=committed_user_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="一小时前的日常问候。",
            completed_at=now - timedelta(hours=1),
        )
        recent_routine_task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.COMPLETED,
            due_at=now - timedelta(hours=2),
            expires_at=now + timedelta(hours=1),
            finished_at=now - timedelta(hours=1),
        )
        session.add_all([authorization, recent_routine_message, recent_routine_task])
        await session.flush()
        session.add(
            AgentRun(
                user_id=committed_user_id,
                trigger_message_id=None,
                trigger_care_task_id=recent_routine_task.id,
                result_message_id=recent_routine_message.id,
                parent_run_id=None,
                agent_name=PROACTIVE_CARE_AGENT_NAME,
                model="test-model",
                status=AgentRunStatus.COMPLETED,
                finished_at=now - timedelta(hours=1),
            )
        )
        plan = CarePlan(
            user_id=committed_user_id,
            summary="完成一次二十分钟散步",
            status=CarePlanStatus.ACTIVE,
            follow_up_at=now - timedelta(minutes=1),
            revision=1,
            created_by_message_id=authorization.id,
            last_changed_by_message_id=authorization.id,
        )
        session.add(plan)
        await session.flush()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.PLAN_FOLLOW_UP,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=plan.follow_up_at,
            expires_at=now + timedelta(hours=24),
            care_plan_id=plan.id,
            care_plan_revision=plan.revision,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    draft = FakeGraph(
        ProactiveCareDecision(
            action="send",
            message="昨天说的散步计划，后来做得怎么样？",
            reason="用户授权的计划到期",
        )
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="自然且准确")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )
    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.COMPLETED
        message = await session.scalar(
            select(Message)
            .where(
                Message.user_id == committed_user_id,
                Message.role == MessageRole.ASSISTANT,
            )
            .order_by(Message.id.desc())
        )
        assert message is not None
        assert message.content == "昨天说的散步计划，后来做得怎么样？"
    payload = json.loads(draft.calls[0][0]["messages"][0].content)
    assert payload["must_send"] is True
    assert payload["trigger"]["kind"] == "plan_follow_up"
    assert payload["trigger"]["plan_summary"] == "完成一次二十分钟散步"


async def test_worker_generates_audits_and_commits_one_message(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        login_session = LoginSession(
            user_id=committed_user_id,
            refresh_token_hash=uuid4().hex + uuid4().hex,
            expires_at=now + timedelta(days=30),
        )
        recent_message = Message(
            user_id=committed_user_id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="这两天挺平稳的。",
        )
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=12),
        )
        session.add_all([login_session, recent_message, task])
        await session.flush()
        installation = PushInstallation(
            id=uuid4(),
            login_session_id=login_session.id,
            expo_push_token="ExponentPushToken[processor-outbox]",
            registration_revision=3,
            permission=PushPermissionState.GRANTED,
            platform=PushPlatform.ANDROID,
            app_version="1.0.0",
            last_seen_at=now,
        )
        session.add(installation)
        await session.commit()
        task_id = task.id
        recent_message_id = recent_message.id
        installation_id = installation.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(
            action="send",
            message="这两天过得怎么样？有空跟我说一句就好。",
            reason="到了已授权的日常问候时间",
        )
    )
    audit_graph = FakeGraph(
        ProactiveCareAudit(decision="approve", reason="内容克制且没有虚构信息")
    )
    memory = FakeMemory()
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(Any, audit_graph),
        companion_memory=cast(Any, memory),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )
    worker = ProactiveCareWorker(process_task=processor.process)

    assert await worker.run_once(now=now) is True

    async with session_factory() as session:
        completed_task = await session.get(ProactiveCareTask, task_id)
        assert completed_task is not None
        assert completed_task.status == ProactiveCareTaskStatus.COMPLETED
        assert completed_task.claimed_through_message_id == recent_message_id
        assert completed_task.lease_token is None

        messages = list(
            await session.scalars(
                select(Message)
                .where(Message.user_id == committed_user_id)
                .order_by(Message.id)
            )
        )
        assert [
            (message.role, message.status, message.content) for message in messages
        ] == [
            (MessageRole.USER, MessageStatus.COMPLETED, "这两天挺平稳的。"),
            (
                MessageRole.ASSISTANT,
                MessageStatus.COMPLETED,
                "这两天过得怎么样？有空跟我说一句就好。",
            ),
        ]

        runs = list(
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.trigger_care_task_id == task_id)
                .order_by(AgentRun.id)
            )
        )
        assert [run.agent_name for run in runs] == [
            PROACTIVE_CARE_AGENT_NAME,
            PROACTIVE_CARE_AUDITOR_NAME,
        ]
        assert all(run.status == AgentRunStatus.COMPLETED for run in runs)
        assert runs[0].result_message_id == messages[-1].id
        assert runs[0].input_tokens == 11
        assert runs[0].output_tokens == 3
        assert runs[1].parent_run_id == runs[0].id
        assert runs[1].result_message_id is None

        delivery = await session.scalar(
            select(PushDelivery).where(PushDelivery.message_id == messages[-1].id)
        )
        assert delivery is not None
        assert delivery.installation_id == installation_id
        assert delivery.installation_revision == 3
        assert delivery.status == PushDeliveryStatus.PENDING
        assert delivery.show_message_preview is True
        assert delivery.next_attempt_at is not None
        assert delivery.expires_at == completed_task.expires_at

        next_routine = await session.scalar(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == committed_user_id,
                ProactiveCareTask.id != task_id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
        )
        assert next_routine is not None
        assert next_routine.due_at > now + timedelta(days=2)
        assert next_routine.expires_at == next_routine.due_at + timedelta(hours=12)

    assert memory.calls == [(committed_user_id, "日常问候")]
    agent_payload = json.loads(draft_graph.calls[0][0]["messages"][0].content)
    assert agent_payload["must_send"] is True
    assert set(agent_payload["health_profile"]) == {
        "personal_profile",
        "health_facts",
    }
    assert len(draft_graph.calls) == 1
    draft_input = draft_graph.calls[0][0]
    assert isinstance(draft_input["messages"][0], HumanMessage)
    payload = json.loads(cast(str, draft_input["messages"][0].content))
    assert payload["recent_messages"][-1]["role"] == "user"
    assert payload["recent_messages"][-1]["content"] == "这两天挺平稳的。"
    assert payload["recent_messages"][-1]["created_at"]
    assert payload["companion_memory"].startswith("- 用户以前说")


async def test_quiet_hours_reschedule_does_not_consume_failure_budget(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.timezone = "UTC"
        care_settings.quiet_hours_start = (now - timedelta(hours=1)).time()
        care_settings.quiet_hours_end = (now + timedelta(hours=1)).time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=2),
            attempt_count=1,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(action="skip", message=None, reason="不应调用")
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="不应调用")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        rescheduled = await session.get(ProactiveCareTask, task_id)
        assert rescheduled is not None
        assert rescheduled.status == ProactiveCareTaskStatus.SCHEDULED
        assert rescheduled.outcome_reason == "quiet_hours"
        assert rescheduled.attempt_count == 1
        assert (
            await session.scalar(
                select(AgentRun.id).where(AgentRun.trigger_care_task_id == task_id)
            )
            is None
        )
    assert draft_graph.calls == []


async def test_entering_quiet_hours_during_audit_reschedules_without_message(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.timezone = "UTC"
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=2),
            attempt_count=2,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def enter_quiet_hours() -> None:
        changed_at = datetime.now(timezone.utc)
        async with session_factory() as session:
            care_settings = await session.get(
                ProactiveCareSettings,
                committed_user_id,
            )
            assert care_settings is not None
            care_settings.timezone = "UTC"
            care_settings.quiet_hours_start = (changed_at - timedelta(hours=1)).time()
            care_settings.quiet_hours_end = (changed_at + timedelta(hours=1)).time()
            await session.commit()

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="今天感觉怎么样？",
                    reason="日常问候到期",
                )
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(
                ProactiveCareAudit(decision="approve", reason="内容安全"),
                before_return=enter_quiet_hours,
            ),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        assert task.outcome_reason == "quiet_hours"
        assert task.attempt_count == 2
        assert task.due_at > now
        assert (
            await session.scalar(
                select(Message.id).where(Message.user_id == committed_user_id)
            )
            is None
        )
        runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.trigger_care_task_id == task_id)
            )
        )
        assert len(runs) == 2
        assert all(run.status == AgentRunStatus.CANCELLED for run in runs)
        assert all(run.error_type == "QuietHoursChanged" for run in runs)


async def test_entering_quiet_hours_during_audit_expires_short_lived_task(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.timezone = "UTC"
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=10),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def enter_long_quiet_hours() -> None:
        changed_at = datetime.now(timezone.utc)
        async with session_factory() as session:
            care_settings = await session.get(
                ProactiveCareSettings,
                committed_user_id,
            )
            assert care_settings is not None
            care_settings.timezone = "UTC"
            care_settings.quiet_hours_start = (changed_at - timedelta(hours=1)).time()
            care_settings.quiet_hours_end = (changed_at + timedelta(hours=1)).time()
            await session.commit()

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="今天感觉怎么样？",
                    reason="日常问候到期",
                )
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(
                ProactiveCareAudit(decision="approve", reason="内容安全"),
                before_return=enter_long_quiet_hours,
            ),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.EXPIRED
        assert task.outcome_reason == "quiet_hours_expired"
        assert (
            await session.scalar(
                select(Message.id).where(Message.user_id == committed_user_id)
            )
            is None
        )
        replacement = await session.scalar(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == committed_user_id,
                ProactiveCareTask.id != task_id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
        )
        assert replacement is not None


async def test_quiet_hours_cancel_running_routine_when_replacement_exists(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    lease_token = uuid4()
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.timezone = "UTC"
        care_settings.quiet_hours_start = (now - timedelta(hours=1)).time()
        care_settings.quiet_hours_end = (now + timedelta(hours=1)).time()
        running = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.RUNNING,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=2),
            attempt_count=1,
            lease_token=lease_token,
            lease_expires_at=now + timedelta(minutes=10),
        )
        replacement = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now + timedelta(days=3),
            expires_at=now + timedelta(days=3, hours=12),
        )
        session.add_all([running, replacement])
        await session.commit()
        running_id = running.id
        replacement_id = replacement.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(action="skip", message=None, reason="不应调用")
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="不应调用")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    await processor.process(running_id, lease_token)

    async with session_factory() as session:
        old_task = await session.get(ProactiveCareTask, running_id)
        replacement = await session.get(ProactiveCareTask, replacement_id)
        assert old_task is not None
        assert old_task.status == ProactiveCareTaskStatus.CANCELLED
        assert old_task.outcome_reason == "routine_replaced"
        assert old_task.attempt_count == 0
        assert replacement is not None
        assert replacement.status == ProactiveCareTaskStatus.SCHEDULED
    assert draft_graph.calls == []


async def test_contact_limit_reschedule_does_not_consume_failure_budget(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()

        previous_message = Message(
            user_id=committed_user_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="之前的一次健康关怀。",
            completed_at=now,
        )
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
            created_at=now - timedelta(hours=2),
        )
        session.add(imported)
        await session.flush()
        previous_task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.HEALTH_EVENT,
            status=ProactiveCareTaskStatus.COMPLETED,
            due_at=now - timedelta(hours=2),
            expires_at=now + timedelta(hours=1),
            wearable_import_id=imported.id,
            source_version_hash=imported.request_hash,
            finished_at=now,
        )
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=2),
            attempt_count=1,
        )
        session.add_all([previous_message, previous_task, task])
        await session.flush()
        session.add(
            AgentRun(
                user_id=committed_user_id,
                trigger_message_id=None,
                trigger_care_task_id=previous_task.id,
                result_message_id=previous_message.id,
                parent_run_id=None,
                agent_name=PROACTIVE_CARE_AGENT_NAME,
                model="test-model",
                status=AgentRunStatus.COMPLETED,
                finished_at=now,
            )
        )
        await session.commit()
        task_id = task.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(action="skip", message=None, reason="不应调用")
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="不应调用")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        rescheduled = await session.get(ProactiveCareTask, task_id)
        assert rescheduled is not None
        assert rescheduled.status == ProactiveCareTaskStatus.SCHEDULED
        assert rescheduled.outcome_reason == "ordinary_contact_limit"
        assert rescheduled.attempt_count == 1
        assert (
            await session.scalar(
                select(AgentRun.id).where(AgentRun.trigger_care_task_id == task_id)
            )
            is None
        )
    assert draft_graph.calls == []


async def test_contact_limit_cancel_running_routine_when_replacement_exists(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    lease_token = uuid4()
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()

        previous_message = Message(
            user_id=committed_user_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="之前的一次普通关怀。",
            completed_at=now,
        )
        previous_task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.COMPLETED,
            due_at=now - timedelta(hours=2),
            expires_at=now + timedelta(hours=1),
            finished_at=now,
        )
        running = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.RUNNING,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=2),
            attempt_count=1,
            lease_token=lease_token,
            lease_expires_at=now + timedelta(minutes=10),
        )
        replacement = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now + timedelta(days=3),
            expires_at=now + timedelta(days=3, hours=12),
        )
        session.add_all([previous_message, previous_task, running, replacement])
        await session.flush()
        session.add(
            AgentRun(
                user_id=committed_user_id,
                trigger_message_id=None,
                trigger_care_task_id=previous_task.id,
                result_message_id=previous_message.id,
                parent_run_id=None,
                agent_name=PROACTIVE_CARE_AGENT_NAME,
                model="test-model",
                status=AgentRunStatus.COMPLETED,
                finished_at=now,
            )
        )
        await session.commit()
        running_id = running.id
        replacement_id = replacement.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(action="skip", message=None, reason="不应调用")
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="不应调用")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    await processor.process(running_id, lease_token)

    async with session_factory() as session:
        old_task = await session.get(ProactiveCareTask, running_id)
        replacement = await session.get(ProactiveCareTask, replacement_id)
        assert old_task is not None
        assert old_task.status == ProactiveCareTaskStatus.CANCELLED
        assert old_task.outcome_reason == "routine_replaced"
        assert old_task.attempt_count == 0
        assert replacement is not None
        assert replacement.status == ProactiveCareTaskStatus.SCHEDULED
    assert draft_graph.calls == []


async def test_must_send_agent_skip_is_a_retryable_processor_failure(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=12),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(
            action="skip",
            reason="当前对话表明用户不希望被继续打扰",
        )
    )
    audit_graph = FakeGraph(ProactiveCareAudit(decision="approve", reason="不会被调用"))
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(Any, audit_graph),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        assert task.outcome_reason == "processor_error:RuntimeError"
        assert task.attempt_count == 1
        assert (
            await session.scalar(
                select(Message.id).where(Message.user_id == committed_user_id)
            )
            is None
        )
        runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.trigger_care_task_id == task_id)
            )
        )
        assert len(runs) == 1
        assert runs[0].status == AgentRunStatus.FAILED
        assert runs[0].result_message_id is None
        assert runs[0].input_tokens == 11
        next_routine = await session.scalar(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == committed_user_id,
                ProactiveCareTask.id != task_id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
        )
        assert next_routine is None
    assert audit_graph.calls == []


async def test_auditor_rejects_without_saving_the_draft(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=12),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="我一直在实时盯着你，你是不是又低血糖了？",
                    reason="日常问候",
                )
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(
                ProactiveCareAudit(
                    decision="reject",
                    reason="虚构实时监测和低血糖事实",
                )
            ),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SKIPPED
        assert task.outcome_reason == "auditor_reject"
        assert (
            await session.scalar(
                select(Message.id).where(Message.user_id == committed_user_id)
            )
            is None
        )
        runs = list(
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.trigger_care_task_id == task_id)
                .order_by(AgentRun.id)
            )
        )
        assert len(runs) == 2
        assert all(run.status == AgentRunStatus.COMPLETED for run in runs)
        assert all(run.result_message_id is None for run in runs)


async def test_user_message_during_audit_cancels_the_old_draft(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=12),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def user_continues_chatting() -> None:
        async with session_factory() as session:
            await prepare_chat_run(
                session,
                committed_user_id,
                uuid4(),
                "我刚好想说，今天感觉挺好的。",
            )

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="今天过得怎么样？",
                    reason="日常问候到期",
                )
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(
                ProactiveCareAudit(decision="approve", reason="内容安全"),
                before_return=user_continues_chatting,
            ),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        old_task = await session.get(ProactiveCareTask, task_id)
        assert old_task is not None
        assert old_task.status == ProactiveCareTaskStatus.CANCELLED
        assert old_task.outcome_reason == "user_activity"

        saved_draft = await session.scalar(
            select(Message.id).where(
                Message.user_id == committed_user_id,
                Message.role == MessageRole.ASSISTANT,
                Message.status == MessageStatus.COMPLETED,
                Message.content == "今天过得怎么样？",
            )
        )
        assert saved_draft is None

        runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.trigger_care_task_id == task_id)
            )
        )
        assert len(runs) == 2
        assert all(run.status == AgentRunStatus.CANCELLED for run in runs)
        assert all(run.input_tokens == 11 for run in runs)

        replacement_task = await session.scalar(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == committed_user_id,
                ProactiveCareTask.id != task_id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
        )
        assert replacement_task is not None


async def test_user_message_during_plan_audit_does_not_consume_failure_budget(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.plan_follow_up_enabled = True
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        authorization = Message(
            user_id=committed_user_id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="好，明天问我散步完成没有。",
            completed_at=now - timedelta(days=1),
        )
        session.add(authorization)
        await session.flush()
        plan = CarePlan(
            user_id=committed_user_id,
            summary="完成一次二十分钟散步",
            status=CarePlanStatus.ACTIVE,
            follow_up_at=now - timedelta(minutes=1),
            revision=1,
            created_by_message_id=authorization.id,
            last_changed_by_message_id=authorization.id,
        )
        session.add(plan)
        await session.flush()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.PLAN_FOLLOW_UP,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=plan.follow_up_at,
            expires_at=now + timedelta(hours=24),
            care_plan_id=plan.id,
            care_plan_revision=plan.revision,
            attempt_count=app_settings.proactive_care_max_attempts - 1,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def user_continues_chatting() -> None:
        async with session_factory() as session:
            await prepare_chat_run(
                session,
                committed_user_id,
                uuid4(),
                "我先补充一下今天的情况。",
            )

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="昨天说的散步计划，后来做得怎么样？",
                    reason="用户授权的计划到期",
                )
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(
                ProactiveCareAudit(decision="approve", reason="内容安全"),
                before_return=user_continues_chatting,
            ),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        assert task.outcome_reason == "user_activity"
        assert task.attempt_count == app_settings.proactive_care_max_attempts - 1
        assert task.due_at >= now + timedelta(minutes=30)
        assert (
            await session.scalar(
                select(Message.id).where(
                    Message.user_id == committed_user_id,
                    Message.content == "昨天说的散步计划，后来做得怎么样？",
                )
            )
            is None
        )


async def test_disabling_routine_during_audit_prevents_the_message(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=12),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def disable_routine() -> None:
        async with session_factory() as session:
            care_settings = await session.get(
                ProactiveCareSettings,
                committed_user_id,
            )
            assert care_settings is not None
            care_settings.routine_cadence = RoutineCareCadence.DISABLED
            await session.commit()

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="这条消息不应在关闭后出现。",
                    reason="日常问候",
                )
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(
                ProactiveCareAudit(decision="approve", reason="内容安全"),
                before_return=disable_routine,
            ),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )
    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.CANCELLED
        assert task.outcome_reason == "routine_disabled"
        assert (
            await session.scalar(
                select(Message.id).where(Message.user_id == committed_user_id)
            )
            is None
        )


async def test_memory_failure_retries_without_creating_an_empty_message(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=12),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(
            action="send",
            message="今天感觉怎么样？",
            reason="日常问候到期",
        )
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="内容安全")),
        ),
        companion_memory=cast(Any, FailingMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        assert task.outcome_reason == "processor_error:RuntimeError"
        assert (
            await session.scalar(
                select(Message.id).where(Message.user_id == committed_user_id)
            )
            is None
        )
        runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.trigger_care_task_id == task_id)
            )
        )
        assert len(runs) == 1
        assert runs[0].status == AgentRunStatus.FAILED
        assert runs[0].result_message_id is None
    assert draft_graph.calls == []


async def test_task_that_expires_during_drafting_never_creates_a_message(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def cross_expiry() -> None:
        async with session_factory() as session:
            expiring_task = await session.get(ProactiveCareTask, task_id)
            assert expiring_task is not None
            expiring_task.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="这条过期草稿不能出现。",
                    reason="日常问候到期",
                ),
                before_return=cross_expiry,
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="内容安全")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.EXPIRED
        assert (
            await session.scalar(
                select(Message.id).where(Message.user_id == committed_user_id)
            )
            is None
        )
        runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.trigger_care_task_id == task_id)
            )
        )
        assert len(runs) == 1
        assert runs[0].status == AgentRunStatus.FAILED


async def test_proactive_context_carries_recent_behaviors_and_beliefs(
    committed_user_id: UUID,
) -> None:
    """主动消息必须知道最近的行为与信念。

    否则关怀会踩空：用户整夜在查血糖，主动消息还在问「今天过得怎么样」。
    """

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        session.add_all(
            [
                FearBehaviorLog(
                    user_id=committed_user_id,
                    kind="night_check",
                    count=2,
                    occurred_at=now - timedelta(hours=8),
                    note="半夜醒了就去测，怕低血糖",
                ),
                HypoglycemiaBeliefEvent(
                    user_id=committed_user_id,
                    belief_code="hypo_causes_coma",
                    quote="总怕半夜低血糖昏过去没人知道",
                    occurrence="mentioned",
                    detected_at=now - timedelta(hours=8),
                ),
                ProactiveCareTask(
                    user_id=committed_user_id,
                    kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                    status=ProactiveCareTaskStatus.SCHEDULED,
                    due_at=now - timedelta(minutes=1),
                    expires_at=now + timedelta(hours=12),
                ),
            ]
        )
        await session.commit()

    draft_graph = FakeGraph(
        ProactiveCareDecision(
            action="send",
            message="昨晚睡得断断续续的吧？今天想先聊哪一头？",
            reason="到了已授权的日常问候时间，且最近夜间有反复确认",
        )
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="内容克制")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )

    assert draft_graph.calls, "主动关怀应当调用起草 Agent"
    payload = json.loads(draft_graph.calls[0][0]["messages"][0].content)
    behavior_context = payload["behavior_context"]
    assert "[近期行为与低血糖信念]" in behavior_context
    assert "夜间反复确认血糖 2 次" in behavior_context
    assert "低血糖会让我昏迷" in behavior_context
    # 次数只供起草时决定问什么，不允许出现在发给用户的话里。
    assert "不要在回复里报这些次数" in behavior_context


async def _seed_sent_routine_contacts(
    user_id: UUID,
    *,
    sent_at_each: list[datetime],
) -> None:
    async with session_factory() as session:
        for sent_at in sent_at_each:
            message = Message(
                user_id=user_id,
                role=MessageRole.ASSISTANT,
                status=MessageStatus.COMPLETED,
                content="上周的问候。",
                created_at=sent_at,
                completed_at=sent_at,
            )
            # 每条都有人回：这样「连续没人理」的暂停策略不会插手，测的只是额度。
            reply = Message(
                user_id=user_id,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="嗯，还行。",
                created_at=sent_at + timedelta(minutes=10),
                completed_at=sent_at + timedelta(minutes=10),
            )
            session.add_all([message, reply])
            await session.flush()
            past_task = ProactiveCareTask(
                user_id=user_id,
                kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                status=ProactiveCareTaskStatus.COMPLETED,
                due_at=sent_at,
                expires_at=sent_at + timedelta(hours=12),
                finished_at=sent_at,
                response_message_id=reply.id,
            )
            session.add(past_task)
            await session.flush()
            session.add(
                AgentRun(
                    user_id=user_id,
                    trigger_message_id=None,
                    trigger_care_task_id=past_task.id,
                    result_message_id=message.id,
                    parent_run_id=None,
                    agent_name=PROACTIVE_CARE_AGENT_NAME,
                    model="test-model",
                    status=AgentRunStatus.COMPLETED,
                    finished_at=sent_at,
                )
            )
        await session.commit()


async def test_a_full_week_moves_the_routine_check_in_to_the_next_free_slot(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=4)
    await _seed_sent_routine_contacts(
        committed_user_id,
        sent_at_each=[
            oldest,
            oldest + timedelta(days=1),
            oldest + timedelta(days=2),
        ],
    )
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=5),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    draft_graph = FakeGraph(
        ProactiveCareDecision(
            action="send",
            message="这周已经说过几次话了，这条不该发出去。",
            reason="日常问候到期",
        )
    )
    processor = ProactiveCareProcessor(
        agent=cast(Any, draft_graph),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="内容安全")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )
    assert not draft_graph.calls, "额度用完时不应该惊动起草 Agent"
    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        assert task.outcome_reason == "weekly_contact_limit"
        assert task.due_at == oldest + timedelta(days=7)
        assert (
            await session.scalar(
                select(Message.id).where(
                    Message.user_id == committed_user_id,
                    Message.role == MessageRole.ASSISTANT,
                    Message.created_at > now - timedelta(hours=1),
                )
            )
            is None
        )


async def test_a_full_week_that_outlives_the_task_schedules_a_new_one(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(days=6, hours=18)
    await _seed_sent_routine_contacts(
        committed_user_id,
        sent_at_each=[
            oldest,
            oldest + timedelta(hours=1),
            oldest + timedelta(hours=2),
        ],
    )
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.quiet_hours_start = datetime.min.time()
        care_settings.quiet_hours_end = datetime.min.time()
        # 三条问候之后患者回过话，所以不是「连续没人理」，不该自动暂停。
        session.add(
            Message(
                user_id=committed_user_id,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="嗯，还行。",
                created_at=oldest + timedelta(hours=3),
                completed_at=oldest + timedelta(hours=3),
            )
        )
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=2),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    processor = ProactiveCareProcessor(
        agent=cast(
            Any,
            FakeGraph(
                ProactiveCareDecision(
                    action="send",
                    message="这条不该发出去。",
                    reason="日常问候到期",
                )
            ),
        ),
        auditor=cast(
            Any,
            FakeGraph(ProactiveCareAudit(decision="approve", reason="内容安全")),
        ),
        companion_memory=cast(Any, FakeMemory()),
        token_model=cast(Any, FakeTokenModel()),
        model_name="test-model",
    )

    assert (
        await ProactiveCareWorker(process_task=processor.process).run_once(now=now)
        is True
    )
    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.EXPIRED
        assert task.outcome_reason == "weekly_contact_limit_expired"
        # 额度腾出来之前这一轮就到点了，于是排下一次，而不是永远不再问候。
        next_task = await session.scalar(
            select(ProactiveCareTask)
            .where(
                ProactiveCareTask.user_id == committed_user_id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
            .order_by(ProactiveCareTask.id.desc())
        )
        assert next_task is not None
        assert next_task.id != task_id
        # 按用户自己的日常节奏（每 3 天）重排，到那天额度早就腾出来了。
        assert abs((next_task.due_at - now) - timedelta(days=3)) < timedelta(seconds=5)
