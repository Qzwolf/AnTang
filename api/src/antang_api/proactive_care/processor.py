import json
from datetime import datetime, time, timedelta, timezone
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from langchain.agents.middleware import InputAgentState
from langchain.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from antang_api.agents.proactive_care import (
    AGENT_SYSTEM_PROMPT,
    AUDITOR_SYSTEM_PROMPT,
    PROACTIVE_CARE_AGENT_NAME,
    PROACTIVE_CARE_AUDITOR_NAME,
    ProactiveCareAgentGraph,
    ProactiveCareAudit,
    ProactiveCareAuditorGraph,
    ProactiveCareDecision,
    extract_proactive_result,
)
from antang_api.agents.runtime import AgentContext
from antang_api.behavioral import read_fear_summary, render_behavior_context
from antang_api.companion_memory import CompanionMemory
from antang_api.context import count_input_tokens
from antang_api.conversations import care_conversation, touch_conversation
from antang_api.database import (
    lock_user_conversation,
    session_factory as default_session_factory,
)
from antang_api.health_profile.service import load_health_profile_snapshot
from antang_api.psychosocial import (
    read_latest_assessment,
    render_guidance,
)
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    CarePlan,
    CarePlanStatus,
    ConversationSummary,
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
    RoutineCareCadence,
)
from antang_api.proactive_care.heart_rate_shadow import (
    HeartRateShadowResult,
    evaluate_heart_rate_shadow,
)
from antang_api.proactive_care.service import (
    has_scheduled_routine_replacement,
    schedule_routine_check_in,
)

MAX_PROACTIVE_INPUT_TOKENS = 20_000
ORDINARY_CONTACT_INTERVAL = timedelta(hours=24)
USER_ACTIVITY_DELAY = timedelta(minutes=30)
HEART_RATE_SAFETY_TEXT = (
    "这段手表数据可能存在延迟或误差，只能作为关怀线索，不能用来诊断心律问题或低血糖。"
    "如果你现在有胸痛、呼吸困难、接近晕厥或意识不清，请立即联系当地急救。"
)


def _quiet_hours_end(
    now: datetime,
    *,
    timezone_name: str,
    start: time,
    end: time,
) -> datetime | None:
    """若当前处于免打扰时段，返回本次时段的结束时间。"""

    if start == end:
        return None

    local_now = now.astimezone(ZoneInfo(timezone_name))
    local_time = local_now.timetz().replace(tzinfo=None)
    if start < end:
        if not start <= local_time < end:
            return None
        end_date = local_now.date()
    else:
        if start <= local_time:
            end_date = local_now.date() + timedelta(days=1)
        elif local_time < end:
            end_date = local_now.date()
        else:
            return None

    local_end = datetime.combine(end_date, end, tzinfo=local_now.tzinfo)
    return local_end.astimezone(timezone.utc)


def _end_claim(
    task: ProactiveCareTask,
    *,
    status: ProactiveCareTaskStatus,
    reason: str,
    now: datetime,
    due_at: datetime | None = None,
) -> None:
    task.status = status
    task.outcome_reason = reason
    task.lease_token = None
    task.lease_expires_at = None
    if status == ProactiveCareTaskStatus.SCHEDULED:
        if due_at is None:
            raise ValueError("重新调度任务必须提供 due_at")
        task.due_at = due_at
        task.finished_at = None
    else:
        task.finished_at = now


class ProactiveCareProcessor:
    """准备上下文、运行两个 Agent，并在用户未插话时提交唯一消息。"""

    def __init__(
        self,
        *,
        agent: ProactiveCareAgentGraph,
        auditor: ProactiveCareAuditorGraph,
        companion_memory: CompanionMemory,
        token_model: BaseChatModel,
        model_name: str,
        sessions: async_sessionmaker[AsyncSession] = default_session_factory,
    ) -> None:
        if not model_name.strip():
            raise ValueError("主动关怀 model_name 不能为空")
        self._agent = agent
        self._auditor = auditor
        self._companion_memory = companion_memory
        self._token_model = token_model
        self._model_name = model_name
        self._sessions = sessions

    async def process(self, task_id: UUID, lease_token: UUID) -> None:
        """处理一次已经由 Worker 领取的任务。"""

        now = datetime.now(timezone.utc)
        async with self._sessions() as session:
            task = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == task_id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                    ProactiveCareTask.lease_expires_at > now,
                    ProactiveCareTask.expires_at > now,
                )
                .with_for_update()
            )
            if task is None:
                return

            care_settings = await session.get(ProactiveCareSettings, task.user_id)
            if care_settings is None:
                raise RuntimeError("用户缺少主动关怀设置")

            plan: CarePlan | None = None
            heart_rate_result: HeartRateShadowResult | None = None
            if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                if care_settings.routine_cadence == RoutineCareCadence.DISABLED:
                    _end_claim(
                        task,
                        status=ProactiveCareTaskStatus.CANCELLED,
                        reason="routine_disabled",
                        now=now,
                    )
                    await schedule_routine_check_in(
                        session,
                        user_id=task.user_id,
                        after=now,
                        reset_existing=False,
                    )
                    await session.commit()
                    return
            elif task.kind == ProactiveCareTaskKind.PLAN_FOLLOW_UP:
                plan = await session.get(CarePlan, task.care_plan_id)
                if (
                    not care_settings.plan_follow_up_enabled
                    or plan is None
                    or plan.user_id != task.user_id
                    or plan.status != CarePlanStatus.ACTIVE
                    or plan.revision != task.care_plan_revision
                ):
                    _end_claim(
                        task,
                        status=ProactiveCareTaskStatus.CANCELLED,
                        reason="plan_not_active",
                        now=now,
                    )
                    await session.commit()
                    return
            else:
                if not care_settings.health_events_enabled:
                    _end_claim(
                        task,
                        status=ProactiveCareTaskStatus.CANCELLED,
                        reason="health_events_disabled",
                        now=now,
                    )
                    await session.commit()
                    return
                heart_rate_result = await evaluate_heart_rate_shadow(
                    session,
                    task=task,
                    timezone_name=care_settings.timezone,
                )
                if (
                    heart_rate_result.decision != "candidate"
                    or heart_rate_result.reason == "duplicate_candidate"
                ):
                    _end_claim(
                        task,
                        status=ProactiveCareTaskStatus.SKIPPED,
                        reason=f"shadow_{heart_rate_result.reason}",
                        now=now,
                    )
                    await session.commit()
                    return

            quiet_end = _quiet_hours_end(
                now,
                timezone_name=care_settings.timezone,
                start=care_settings.quiet_hours_start,
                end=care_settings.quiet_hours_end,
            )
            if quiet_end is not None:
                if quiet_end >= task.expires_at:
                    _end_claim(
                        task,
                        status=ProactiveCareTaskStatus.EXPIRED,
                        reason="quiet_hours_expired",
                        now=now,
                    )
                    if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                        await schedule_routine_check_in(
                            session,
                            user_id=task.user_id,
                            after=now,
                            reset_existing=False,
                        )
                else:
                    task.attempt_count -= 1
                    if await has_scheduled_routine_replacement(session, task):
                        _end_claim(
                            task,
                            status=ProactiveCareTaskStatus.CANCELLED,
                            reason="routine_replaced",
                            now=now,
                        )
                    else:
                        _end_claim(
                            task,
                            status=ProactiveCareTaskStatus.SCHEDULED,
                            reason="quiet_hours",
                            now=now,
                            due_at=quiet_end,
                        )
                await session.commit()
                return

            if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                last_proactive_message = aliased(Message)
                last_proactive_contact_at = await session.scalar(
                    select(last_proactive_message.created_at)
                    .join(
                        AgentRun,
                        AgentRun.result_message_id == last_proactive_message.id,
                    )
                    .join(
                        ProactiveCareTask,
                        AgentRun.trigger_care_task_id == ProactiveCareTask.id,
                    )
                    .where(
                        AgentRun.user_id == task.user_id,
                        AgentRun.parent_run_id.is_(None),
                        AgentRun.agent_name == PROACTIVE_CARE_AGENT_NAME,
                        AgentRun.status == AgentRunStatus.COMPLETED,
                        ProactiveCareTask.user_id == task.user_id,
                        last_proactive_message.user_id == task.user_id,
                        last_proactive_message.status == MessageStatus.COMPLETED,
                    )
                    .order_by(last_proactive_message.id.desc())
                    .limit(1)
                )
                if last_proactive_contact_at is not None:
                    next_contact_at = (
                        last_proactive_contact_at + ORDINARY_CONTACT_INTERVAL
                    )
                    if next_contact_at > now:
                        if next_contact_at >= task.expires_at:
                            _end_claim(
                                task,
                                status=ProactiveCareTaskStatus.EXPIRED,
                                reason="ordinary_contact_limit_expired",
                                now=now,
                            )
                            await schedule_routine_check_in(
                                session,
                                user_id=task.user_id,
                                after=now,
                                reset_existing=False,
                            )
                        else:
                            task.attempt_count -= 1
                            if await has_scheduled_routine_replacement(session, task):
                                _end_claim(
                                    task,
                                    status=ProactiveCareTaskStatus.CANCELLED,
                                    reason="routine_replaced",
                                    now=now,
                                )
                            else:
                                _end_claim(
                                    task,
                                    status=ProactiveCareTaskStatus.SCHEDULED,
                                    reason="ordinary_contact_limit",
                                    now=now,
                                    due_at=next_contact_at,
                                )
                        await session.commit()
                        return

            claimed_through_message_id = task.claimed_through_message_id
            message_query = select(Message).where(
                Message.user_id == task.user_id,
                Message.status == MessageStatus.COMPLETED,
            )
            if claimed_through_message_id is None:
                recent_messages: list[Message] = []
                summary = None
            else:
                recent_messages = list(
                    reversed(
                        list(
                            await session.scalars(
                                message_query.where(
                                    Message.id <= claimed_through_message_id
                                )
                                .order_by(Message.id.desc())
                                .limit(10)
                            )
                        )
                    )
                )
                summary = await session.scalar(
                    select(ConversationSummary)
                    .where(
                        ConversationSummary.user_id == task.user_id,
                        ConversationSummary.through_message_id
                        <= claimed_through_message_id,
                    )
                    .order_by(ConversationSummary.id.desc())
                    .limit(1)
                )

            contact_message = aliased(Message, name="last_care_message")
            reply_message = aliased(Message, name="last_care_reply")
            last_contact = (
                await session.execute(
                    select(
                        ProactiveCareTask.kind,
                        contact_message.content,
                        contact_message.created_at,
                        reply_message.content,
                        reply_message.created_at,
                    )
                    .join(
                        AgentRun,
                        AgentRun.trigger_care_task_id == ProactiveCareTask.id,
                    )
                    .join(
                        contact_message,
                        AgentRun.result_message_id == contact_message.id,
                    )
                    .outerjoin(
                        reply_message,
                        ProactiveCareTask.response_message_id == reply_message.id,
                    )
                    .where(
                        ProactiveCareTask.user_id == task.user_id,
                        AgentRun.user_id == task.user_id,
                        AgentRun.parent_run_id.is_(None),
                        AgentRun.agent_name == PROACTIVE_CARE_AGENT_NAME,
                        AgentRun.status == AgentRunStatus.COMPLETED,
                        contact_message.user_id == task.user_id,
                        contact_message.status == MessageStatus.COMPLETED,
                    )
                    .order_by(contact_message.id.desc())
                    .limit(1)
                )
            ).one_or_none()

            profile = (
                await load_health_profile_snapshot(session, task.user_id)
            ).model_dump(
                mode="json",
                include={"personal_profile", "health_facts"},
            )
            trigger: dict[str, Any]
            memory_query: str
            required_message_parts: tuple[str, ...] = ()
            expected_health_evidence: dict[str, object] | None = None
            expected_health_event_key: str | None = None
            if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                trigger = {
                    "kind": task.kind.value,
                    "cadence": care_settings.routine_cadence.value,
                    "due_at": task.due_at.isoformat(),
                }
                memory_query = "日常问候"
            elif task.kind == ProactiveCareTaskKind.PLAN_FOLLOW_UP:
                if plan is None:
                    raise RuntimeError("计划回访任务缺少有效计划")
                trigger = {
                    "kind": task.kind.value,
                    "plan_summary": plan.summary,
                    "follow_up_at": plan.follow_up_at.isoformat(),
                    "plan_revision": plan.revision,
                }
                memory_query = plan.summary
            else:
                if (
                    heart_rate_result is None
                    or heart_rate_result.direction is None
                    or heart_rate_result.sample_start is None
                    or heart_rate_result.sample_end is None
                    or heart_rate_result.minimum_bpm is None
                    or heart_rate_result.median_bpm is None
                    or heart_rate_result.maximum_bpm is None
                    or heart_rate_result.abnormal_fraction is None
                    or heart_rate_result.event_key is None
                ):
                    raise RuntimeError("心率候选缺少生成关怀所需的证据")
                local_zone = ZoneInfo(care_settings.timezone)
                sample_start = heart_rate_result.sample_start.astimezone(local_zone)
                sample_end = heart_rate_result.sample_end.astimezone(local_zone)
                direction_text = (
                    "持续偏快" if heart_rate_result.direction == "high" else "持续偏慢"
                )
                measurement_text = (
                    f"手表在{sample_start:%Y年%m月%d日 %H:%M}至"
                    f"{sample_end:%Y年%m月%d日 %H:%M}"
                    f"（{care_settings.timezone}）记录到一段{direction_text}的心率，"
                    f"该窗口中位数为{heart_rate_result.median_bpm:g}次/分，"
                    f"最低{heart_rate_result.minimum_bpm:g}次/分，"
                    f"最高{heart_rate_result.maximum_bpm:g}次/分。"
                )
                required_message_parts = (
                    measurement_text,
                    HEART_RATE_SAFETY_TEXT,
                )
                expected_health_evidence = heart_rate_result.evidence()
                expected_health_event_key = heart_rate_result.event_key
                trigger = {
                    "kind": task.kind.value,
                    "rule_id": task.rule_id,
                    "rule_version": task.rule_version,
                    "event_key": heart_rate_result.event_key,
                    "direction": heart_rate_result.direction,
                    "sample_start": heart_rate_result.sample_start.isoformat(),
                    "sample_end": heart_rate_result.sample_end.isoformat(),
                    "timezone": care_settings.timezone,
                    "sample_count": heart_rate_result.sample_count,
                    "covered_minutes": heart_rate_result.covered_minutes,
                    "abnormal_minutes": heart_rate_result.abnormal_minutes,
                    "abnormal_fraction": heart_rate_result.abnormal_fraction,
                    "minimum_bpm": heart_rate_result.minimum_bpm,
                    "median_bpm": heart_rate_result.median_bpm,
                    "maximum_bpm": heart_rate_result.maximum_bpm,
                    "required_verbatim": list(required_message_parts),
                }
                memory_query = f"{direction_text}心率后的关怀"

            context_payload: dict[str, Any] = {
                "must_send": True,
                "trigger": trigger,
                "health_profile": profile,
                "earlier_summary": summary.content if summary is not None else None,
                "recent_messages": [
                    {
                        "role": message.role.value,
                        "content": message.content,
                        "created_at": message.created_at.isoformat(),
                    }
                    for message in recent_messages
                ],
                "last_proactive_contact": (
                    {
                        "kind": last_contact[0].value,
                        "message": last_contact[1],
                        "sent_at": last_contact[2].isoformat(),
                        "reply": last_contact[3],
                        "replied_at": (
                            last_contact[4].isoformat()
                            if last_contact[4] is not None
                            else None
                        ),
                    }
                    if last_contact is not None
                    else None
                ),
                "companion_memory": "",
                # 主动关怀也要按同一个人的恐惧程度与察觉能力调整说法。
                "psychosocial_guidance": render_guidance(
                    await read_latest_assessment(session, task.user_id),
                ),
                # 否则主动消息不知道「这个人最近每晚查三次血糖」，关怀会踩空。
                "behavior_context": render_behavior_context(
                    await read_fear_summary(session, user_id=task.user_id),
                ),
            }

            root_run = AgentRun(
                user_id=task.user_id,
                trigger_message_id=None,
                trigger_care_task_id=task.id,
                result_message_id=None,
                parent_run_id=None,
                agent_name=PROACTIVE_CARE_AGENT_NAME,
                model=self._model_name,
                status=AgentRunStatus.RUNNING,
            )
            session.add(root_run)
            await session.flush()
            user_id = task.user_id
            task_kind = task.kind
            root_run_id = root_run.id
            await session.commit()

        async with self._sessions() as session:
            context_payload[
                "companion_memory"
            ] = await self._companion_memory.recall_for_proactive(
                session,
                user_id,
                query=memory_query,
                query_timestamp=now,
            )

        model_text = json.dumps(context_payload, ensure_ascii=False)
        model_message = HumanMessage(content=model_text)
        input_tokens = await count_input_tokens(
            self._token_model,
            AGENT_SYSTEM_PROMPT,
            [model_message],
        )
        if input_tokens > MAX_PROACTIVE_INPUT_TOKENS:
            raise RuntimeError("主动关怀 Agent 输入超过 20000 tokens")

        agent_config: RunnableConfig = {
            "configurable": {"thread_id": str(root_run_id)},
            "metadata": {
                "run_id": str(root_run_id),
                "user_id": str(user_id),
                "stream_visibility": "internal",
            },
        }
        agent_state = cast(
            dict[str, Any],
            await self._agent.ainvoke(
                cast(InputAgentState, {"messages": [model_message]}),
                config=agent_config,
                context=AgentContext(
                    user_id=user_id,
                    run_id=root_run_id,
                    input_message_count=1,
                ),
            ),
        )
        decision, root_input_tokens, root_output_tokens = extract_proactive_result(
            agent_state,
            ProactiveCareDecision,
        )

        decision_error: str | None = None
        draft_message = decision.message
        health_question: str | None = None
        if decision.action == "skip":
            decision_error = "必须发送的主动关怀任务不能返回 skip"
        elif decision.message is None:
            decision_error = "主动关怀 send 决定缺少消息"
        elif task_kind == ProactiveCareTaskKind.HEALTH_EVENT:
            health_question = decision.message.strip()
            if len(health_question) > 120 or "\n" in health_question or "\r" in health_question:
                decision_error = "心率关怀 Agent 只能生成一句简短问题"
            else:
                draft_message = "\n\n".join((*required_message_parts, health_question))

        check_at = datetime.now(timezone.utc)
        auditor_run_id: UUID | None = None
        async with self._sessions() as session:
            live_task = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == task_id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                    ProactiveCareTask.lease_expires_at > check_at,
                )
                .with_for_update()
            )
            live_root = await session.get(AgentRun, root_run_id, with_for_update=True)
            if (
                live_task is None
                or live_root is None
                or live_root.status != AgentRunStatus.RUNNING
            ):
                await session.rollback()
                return
            if live_task.expires_at <= check_at:
                await session.rollback()
                await self._finish(
                    task_id=task_id,
                    user_id=user_id,
                    lease_token=lease_token,
                    claimed_through_message_id=claimed_through_message_id,
                    task_kind=task_kind,
                    root_run_id=root_run_id,
                    root_tokens=(root_input_tokens, root_output_tokens),
                    final_status=ProactiveCareTaskStatus.SKIPPED,
                    outcome_reason="task_expired",
                )
                return
            live_root.input_tokens = root_input_tokens
            live_root.output_tokens = root_output_tokens
            if decision_error is not None:
                await session.commit()
            else:
                auditor_run = AgentRun(
                    user_id=user_id,
                    trigger_message_id=None,
                    trigger_care_task_id=task_id,
                    result_message_id=None,
                    parent_run_id=root_run_id,
                    agent_name=PROACTIVE_CARE_AUDITOR_NAME,
                    model=self._model_name,
                    status=AgentRunStatus.RUNNING,
                )
                session.add(auditor_run)
                await session.flush()
                auditor_run_id = auditor_run.id
                await session.commit()

        if decision_error is not None:
            raise RuntimeError(decision_error)
        if draft_message is None:
            raise RuntimeError("主动关怀 send 决定缺少消息")
        if auditor_run_id is None:
            raise RuntimeError("主动关怀 Auditor AgentRun 未创建")

        audit_text = json.dumps(
            {"context": context_payload, "draft": draft_message},
            ensure_ascii=False,
        )
        audit_message = HumanMessage(content=audit_text)
        audit_input_tokens = await count_input_tokens(
            self._token_model,
            AUDITOR_SYSTEM_PROMPT,
            [audit_message],
        )
        if audit_input_tokens > MAX_PROACTIVE_INPUT_TOKENS:
            raise RuntimeError("主动关怀 Auditor 输入超过 20000 tokens")

        auditor_config: RunnableConfig = {
            "configurable": {"thread_id": str(auditor_run_id)},
            "metadata": {
                "run_id": str(auditor_run_id),
                "user_id": str(user_id),
                "stream_visibility": "internal",
            },
        }
        audit_state = cast(
            dict[str, Any],
            await self._auditor.ainvoke(
                cast(InputAgentState, {"messages": [audit_message]}),
                config=auditor_config,
                context=AgentContext(
                    user_id=user_id,
                    run_id=auditor_run_id,
                    input_message_count=1,
                ),
            ),
        )
        audit, auditor_input_tokens, auditor_output_tokens = extract_proactive_result(
            audit_state, ProactiveCareAudit
        )
        approved = audit.decision == "approve"
        await self._finish(
            task_id=task_id,
            user_id=user_id,
            lease_token=lease_token,
            claimed_through_message_id=claimed_through_message_id,
            task_kind=task_kind,
            root_run_id=root_run_id,
            root_tokens=(root_input_tokens, root_output_tokens),
            auditor_run_id=auditor_run_id,
            auditor_tokens=(auditor_input_tokens, auditor_output_tokens),
            final_status=(
                ProactiveCareTaskStatus.COMPLETED
                if approved
                else ProactiveCareTaskStatus.SKIPPED
            ),
            outcome_reason="message_created" if approved else "auditor_reject",
            message=draft_message if approved else None,
            required_message_parts=required_message_parts,
            health_question=health_question,
            expected_health_evidence=expected_health_evidence,
            expected_health_event_key=expected_health_event_key,
        )

    async def _finish(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        lease_token: UUID,
        claimed_through_message_id: UUID | None,
        task_kind: ProactiveCareTaskKind,
        root_run_id: UUID,
        root_tokens: tuple[int, int],
        final_status: ProactiveCareTaskStatus,
        outcome_reason: str,
        auditor_run_id: UUID | None = None,
        auditor_tokens: tuple[int, int] | None = None,
        message: str | None = None,
        required_message_parts: tuple[str, ...] = (),
        health_question: str | None = None,
        expected_health_evidence: dict[str, object] | None = None,
        expected_health_event_key: str | None = None,
    ) -> None:
        async with self._sessions() as session:
            await lock_user_conversation(session, user_id)
            finished_at = datetime.now(timezone.utc)
            task = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == task_id,
                    ProactiveCareTask.user_id == user_id,
                    ProactiveCareTask.kind == task_kind,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                    ProactiveCareTask.lease_expires_at > finished_at,
                )
                .with_for_update()
            )
            if task is None:
                await session.rollback()
                return

            root_run = await session.get(AgentRun, root_run_id, with_for_update=True)
            if (
                root_run is None
                or root_run.user_id != user_id
                or root_run.trigger_care_task_id != task_id
                or root_run.parent_run_id is not None
                or root_run.agent_name != PROACTIVE_CARE_AGENT_NAME
                or root_run.status != AgentRunStatus.RUNNING
            ):
                raise RuntimeError("主动关怀根 AgentRun 状态与当前任务不一致")

            auditor_run: AgentRun | None = None
            if auditor_run_id is not None:
                auditor_run = await session.get(
                    AgentRun,
                    auditor_run_id,
                    with_for_update=True,
                )
                if (
                    auditor_run is None
                    or auditor_run.user_id != user_id
                    or auditor_run.trigger_care_task_id != task_id
                    or auditor_run.parent_run_id != root_run_id
                    or auditor_run.agent_name != PROACTIVE_CARE_AUDITOR_NAME
                    or auditor_run.status != AgentRunStatus.RUNNING
                    or auditor_tokens is None
                ):
                    raise RuntimeError("主动关怀 Auditor 状态与当前任务不一致")
            elif auditor_tokens is not None:
                raise RuntimeError("Auditor token 缺少对应 AgentRun")

            root_run.input_tokens = root_tokens[0]
            root_run.output_tokens = root_tokens[1]
            if auditor_run is not None and auditor_tokens is not None:
                auditor_run.input_tokens = auditor_tokens[0]
                auditor_run.output_tokens = auditor_tokens[1]

            if task.expires_at <= finished_at:
                for run in (root_run, auditor_run):
                    if run is None:
                        continue
                    run.status = AgentRunStatus.FAILED
                    run.error_type = "CareTaskExpired"
                    run.error_message = "主动关怀任务在消息生成期间过期"
                    run.finished_at = finished_at
                _end_claim(
                    task,
                    status=ProactiveCareTaskStatus.EXPIRED,
                    reason="task_expired",
                    now=finished_at,
                )
                if task_kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                    await schedule_routine_check_in(
                        session,
                        user_id=user_id,
                        after=finished_at,
                        reset_existing=False,
                    )
                await session.commit()
                return

            live_settings = await session.get(
                ProactiveCareSettings,
                user_id,
                with_for_update=True,
            )
            if live_settings is None:
                raise RuntimeError("用户缺少主动关怀设置")
            authorization_reason: str | None = None
            if (
                task_kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN
                and live_settings.routine_cadence == RoutineCareCadence.DISABLED
            ):
                authorization_reason = "routine_disabled"
            elif task_kind == ProactiveCareTaskKind.PLAN_FOLLOW_UP:
                live_plan = await session.get(CarePlan, task.care_plan_id)
                if (
                    not live_settings.plan_follow_up_enabled
                    or live_plan is None
                    or live_plan.user_id != user_id
                    or live_plan.status != CarePlanStatus.ACTIVE
                    or live_plan.revision != task.care_plan_revision
                ):
                    authorization_reason = "plan_not_active"
            elif (
                task_kind == ProactiveCareTaskKind.HEALTH_EVENT
                and not live_settings.health_events_enabled
            ):
                authorization_reason = "health_events_disabled"

            if authorization_reason is not None:
                for run in (root_run, auditor_run):
                    if run is None:
                        continue
                    run.status = AgentRunStatus.CANCELLED
                    run.error_type = "CareAuthorizationChanged"
                    run.error_message = "用户在消息提交前关闭或改变了主动关怀授权"
                    run.finished_at = finished_at
                _end_claim(
                    task,
                    status=ProactiveCareTaskStatus.CANCELLED,
                    reason=authorization_reason,
                    now=finished_at,
                )
                if task_kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                    await schedule_routine_check_in(
                        session,
                        user_id=user_id,
                        after=finished_at,
                        reset_existing=False,
                    )
                await session.commit()
                return

            quiet_end = _quiet_hours_end(
                finished_at,
                timezone_name=live_settings.timezone,
                start=live_settings.quiet_hours_start,
                end=live_settings.quiet_hours_end,
            )
            if quiet_end is not None:
                for run in (root_run, auditor_run):
                    if run is None:
                        continue
                    run.status = AgentRunStatus.CANCELLED
                    run.error_type = "QuietHoursChanged"
                    run.error_message = "消息生成期间进入了用户设置的免打扰时段"
                    run.finished_at = finished_at

                if quiet_end >= task.expires_at:
                    _end_claim(
                        task,
                        status=ProactiveCareTaskStatus.EXPIRED,
                        reason="quiet_hours_expired",
                        now=finished_at,
                    )
                    if task_kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                        await schedule_routine_check_in(
                            session,
                            user_id=user_id,
                            after=finished_at,
                            reset_existing=False,
                        )
                else:
                    task.attempt_count -= 1
                    if await has_scheduled_routine_replacement(session, task):
                        _end_claim(
                            task,
                            status=ProactiveCareTaskStatus.CANCELLED,
                            reason="routine_replaced",
                            now=finished_at,
                        )
                    else:
                        _end_claim(
                            task,
                            status=ProactiveCareTaskStatus.SCHEDULED,
                            reason="quiet_hours",
                            now=finished_at,
                            due_at=quiet_end,
                        )
                await session.commit()
                return

            latest_message_id = await session.scalar(
                select(Message.id)
                .where(Message.user_id == user_id)
                .order_by(Message.id.desc())
                .limit(1)
            )
            if latest_message_id != claimed_through_message_id:
                for run in (root_run, auditor_run):
                    if run is None:
                        continue
                    run.status = AgentRunStatus.CANCELLED
                    run.error_type = "UserActivity"
                    run.error_message = "用户在主动关怀生成期间继续了对话"
                    run.finished_at = finished_at

                if task_kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                    _end_claim(
                        task,
                        status=ProactiveCareTaskStatus.CANCELLED,
                        reason="user_activity",
                        now=finished_at,
                    )
                    await schedule_routine_check_in(
                        session,
                        user_id=user_id,
                        after=finished_at,
                        reset_existing=False,
                    )
                else:
                    retry_at = finished_at + USER_ACTIVITY_DELAY
                    if retry_at >= task.expires_at:
                        _end_claim(
                            task,
                            status=ProactiveCareTaskStatus.EXPIRED,
                            reason="user_activity_expired",
                            now=finished_at,
                        )
                    else:
                        task.attempt_count -= 1
                        _end_claim(
                            task,
                            status=ProactiveCareTaskStatus.SCHEDULED,
                            reason="user_activity",
                            now=finished_at,
                            due_at=retry_at,
                        )
                await session.commit()
                return

            if (message is None) != (final_status == ProactiveCareTaskStatus.SKIPPED):
                raise RuntimeError("主动关怀任务终态与消息结果不一致")

            if message is not None and task_kind == ProactiveCareTaskKind.HEALTH_EVENT:
                if (
                    expected_health_evidence is None
                    or expected_health_event_key is None
                    or task.health_evidence != expected_health_evidence
                    or task.health_event_key != expected_health_event_key
                ):
                    raise RuntimeError("心率关怀证据在消息提交前发生变化")
                if health_question is None:
                    raise RuntimeError("心率关怀缺少 Agent 生成的关怀问题")
                expected_message = "\n\n".join(
                    (*required_message_parts, health_question)
                )
                if message != expected_message:
                    raise RuntimeError("心率关怀消息与程序组装结果不一致")

            result_message: Message | None = None
            if message is not None:
                # 主动关怀单独落进一段「关怀消息」会话，侧边栏里和患者自己开的对话分开。
                care = await care_conversation(session, user_id)
                result_message = Message(
                    client_message_id=None,
                    user_id=user_id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content=message,
                    sources=[],
                    completed_at=finished_at,
                    conversation_id=care.id,
                )
                await touch_conversation(session, care, at=finished_at)
                session.add(result_message)
                await session.flush()

                installation_rows = list(
                    await session.execute(
                        select(
                            PushInstallation.id,
                            PushInstallation.registration_revision,
                        )
                        .join(
                            LoginSession,
                            PushInstallation.login_session_id == LoginSession.id,
                        )
                        .where(
                            LoginSession.user_id == user_id,
                            LoginSession.revoked_at.is_(None),
                            LoginSession.expires_at > finished_at,
                            PushInstallation.disabled_at.is_(None),
                            PushInstallation.permission == PushPermissionState.GRANTED,
                            PushInstallation.expo_push_token.is_not(None),
                        )
                    )
                )
                show_message_preview = True
                if task_kind == ProactiveCareTaskKind.HEALTH_EVENT:
                    care_settings = await session.get(
                        ProactiveCareSettings,
                        user_id,
                    )
                    if care_settings is None:
                        raise RuntimeError("用户缺少主动关怀设置")
                    show_message_preview = (
                        care_settings.health_notification_preview_enabled
                    )
                session.add_all(
                    [
                        PushDelivery(
                            message_id=result_message.id,
                            installation_id=installation_id,
                            installation_revision=registration_revision,
                            show_message_preview=show_message_preview,
                            status=PushDeliveryStatus.PENDING,
                            next_attempt_at=finished_at,
                            expires_at=task.expires_at,
                        )
                        for installation_id, registration_revision in installation_rows
                    ]
                )

            root_run.status = AgentRunStatus.COMPLETED
            root_run.result_message_id = (
                result_message.id if result_message is not None else None
            )
            root_run.finished_at = finished_at

            if auditor_run is not None:
                if auditor_tokens is None:
                    raise RuntimeError("Auditor AgentRun 缺少 token 用量")
                auditor_run.status = AgentRunStatus.COMPLETED
                auditor_run.finished_at = finished_at

            _end_claim(
                task,
                status=final_status,
                reason=outcome_reason,
                now=finished_at,
            )
            if task_kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                await schedule_routine_check_in(
                    session,
                    user_id=user_id,
                    after=finished_at,
                    reset_existing=False,
                )
            await session.commit()
