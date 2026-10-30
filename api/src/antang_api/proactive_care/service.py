from datetime import datetime, timedelta, timezone
from typing import Literal, TypedDict, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import lock_user_conversation
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    CarePlan,
    CarePlanStatus,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    RoutineCareCadence,
    WearableImport,
    WearableObservation,
    WearableRecordTombstone,
    WearableRecordType,
)
from antang_api.proactive_care.heart_rate_shadow import (
    HEART_RATE_RULE_ID,
    HEART_RATE_RULE_VERSION,
    evaluate_heart_rate_shadow,
)

_ROUTINE_INTERVALS = {
    RoutineCareCadence.DAILY: timedelta(days=1),
    RoutineCareCadence.EVERY_3_DAYS: timedelta(days=3),
    RoutineCareCadence.WEEKLY: timedelta(days=7),
}

# 打扰预算的另一半：连续几次问候没人搭理，就自己降下来，不靠用户来发现。
# 阈值取 2/3：一次没回可能只是忙，两次就是「这个时间点不合适」。
_QUIET_DOWNGRADE_AFTER = 2
_QUIET_PAUSE_AFTER = 3


async def _unanswered_routine_streak(
    session: AsyncSession,
    user_id: UUID,
) -> int:
    """数最近连续几次日常问候没有被回应（从最近一次往前数，遇到回应就停）。"""

    responses = await session.scalars(
        select(ProactiveCareTask.response_message_id)
        .where(
            ProactiveCareTask.user_id == user_id,
            ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            ProactiveCareTask.status == ProactiveCareTaskStatus.COMPLETED,
        )
        .order_by(
            ProactiveCareTask.due_at.desc(),
            ProactiveCareTask.id.desc(),
        )
        .limit(_QUIET_PAUSE_AFTER + 2)
    )
    streak = 0
    for response_message_id in responses:
        if response_message_id is not None:
            break
        streak += 1
    return streak


async def _apply_quiet_policy(
    session: AsyncSession,
    *,
    care_settings: ProactiveCareSettings,
    user_id: UUID,
    now: datetime,
) -> None:
    """连续没回应就降频，再没回应就暂停；用户只要回一次就清零。"""

    streak = await _unanswered_routine_streak(session, user_id)
    care_settings.unanswered_contacts = streak
    if streak >= _QUIET_PAUSE_AFTER:
        if care_settings.routine_cadence != RoutineCareCadence.DISABLED:
            care_settings.routine_cadence = RoutineCareCadence.DISABLED
            care_settings.auto_paused_at = now
        return
    if (
        streak >= _QUIET_DOWNGRADE_AFTER
        and care_settings.routine_cadence == RoutineCareCadence.DAILY
    ):
        care_settings.routine_cadence = RoutineCareCadence.EVERY_3_DAYS
        care_settings.auto_downgraded_at = now


async def has_scheduled_routine_replacement(
    session: AsyncSession,
    task: ProactiveCareTask,
) -> bool:
    """串行确认运行中的日常任务是否已被下一次候选取代。"""

    if task.kind != ProactiveCareTaskKind.ROUTINE_CHECK_IN:
        return False
    await lock_user_conversation(session, task.user_id)
    replacement_id = await session.scalar(
        select(ProactiveCareTask.id)
        .where(
            ProactiveCareTask.user_id == task.user_id,
            ProactiveCareTask.id != task.id,
            ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
        )
        .with_for_update()
        .limit(1)
    )
    return replacement_id is not None


async def schedule_routine_check_in(
    session: AsyncSession,
    *,
    user_id: UUID,
    after: datetime,
    reset_existing: bool,
) -> UUID | None:
    """确保已开启日常关怀的用户始终有下一次候选任务。"""

    care_settings = await session.get(ProactiveCareSettings, user_id)
    if care_settings is None:
        raise RuntimeError("用户缺少主动关怀设置")
    if reset_existing:
        # 用户刚回来说话：不管之前积了几次没回应，都从零开始。
        care_settings.unanswered_contacts = 0
    else:
        await _apply_quiet_policy(
            session,
            care_settings=care_settings,
            user_id=user_id,
            now=after,
        )
    scheduled = await session.scalar(
        select(ProactiveCareTask)
        .where(
            ProactiveCareTask.user_id == user_id,
            ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
        )
        .with_for_update()
    )
    interval = _ROUTINE_INTERVALS.get(care_settings.routine_cadence)
    if interval is None:
        if scheduled is not None:
            scheduled.status = ProactiveCareTaskStatus.CANCELLED
            scheduled.outcome_reason = "routine_disabled"
            scheduled.finished_at = after
        return None

    due_at = after + interval
    if scheduled is not None:
        if reset_existing:
            scheduled.due_at = due_at
            scheduled.expires_at = due_at + timedelta(hours=12)
            scheduled.outcome_reason = None
        return scheduled.id

    scheduled = ProactiveCareTask(
        user_id=user_id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.SCHEDULED,
        due_at=due_at,
        expires_at=due_at + timedelta(hours=12),
    )
    session.add(scheduled)
    await session.flush()
    return scheduled.id


class CarePlanResult(TypedDict):
    plan_id: str
    status: str
    summary: str
    follow_up_at: str
    revision: int


async def manage_care_plan(
    session: AsyncSession,
    *,
    user_id: UUID,
    trigger_message_id: UUID,
    action: Literal["create", "update", "complete", "cancel"],
    authorization_quote: str,
    plan_id: UUID | None = None,
    summary: str | None = None,
    follow_up_at: datetime | None = None,
    now: datetime | None = None,
) -> CarePlanResult:
    """按当前用户消息的明确授权创建或改变一项回访计划。"""

    message = await session.scalar(
        select(Message).where(
            Message.id == trigger_message_id,
            Message.user_id == user_id,
            Message.role == MessageRole.USER,
            Message.status == MessageStatus.COMPLETED,
        )
    )
    if message is None:
        raise RuntimeError("找不到当前用户的触发消息")

    replay = await session.scalar(
        select(CarePlan).where(
            CarePlan.user_id == user_id,
            CarePlan.last_changed_by_message_id == trigger_message_id,
        )
    )
    if replay is not None:
        return {
            "plan_id": str(replay.id),
            "status": replay.status.value,
            "summary": replay.summary,
            "follow_up_at": replay.follow_up_at.isoformat(),
            "revision": replay.revision,
        }

    quote = authorization_quote.strip()
    if not quote or quote not in message.content:
        raise ValueError("计划操作必须引用当前用户消息中的原话")

    changed_at = now or datetime.now(timezone.utc)
    if changed_at.tzinfo is None or changed_at.utcoffset() is None:
        raise ValueError("当前时间必须包含时区")

    care_settings = await session.get(ProactiveCareSettings, user_id)
    if care_settings is None:
        raise RuntimeError("用户缺少主动关怀设置")
    if action in {"create", "update"} and not care_settings.plan_follow_up_enabled:
        raise ValueError("用户已关闭计划回访")

    active_change = action in {"create", "update"}
    if active_change:
        cleaned_summary = summary.strip() if summary is not None else ""
        if not cleaned_summary or len(cleaned_summary) > 500:
            raise ValueError("计划摘要必须是 1 到 500 个字符")
        if (
            follow_up_at is None
            or follow_up_at.tzinfo is None
            or follow_up_at.utcoffset() is None
            or follow_up_at <= changed_at
        ):
            raise ValueError("计划回访时间必须是包含时区的未来时间")
    elif summary is not None or follow_up_at is not None:
        raise ValueError("完成或取消计划不接受新摘要和回访时间")
    else:
        cleaned_summary = ""

    if action == "create":
        if plan_id is not None:
            raise ValueError("创建计划时不能提供 plan_id")
        plan = CarePlan(
            user_id=user_id,
            summary=cleaned_summary,
            status=CarePlanStatus.ACTIVE,
            follow_up_at=follow_up_at,
            revision=1,
            created_by_message_id=trigger_message_id,
            last_changed_by_message_id=trigger_message_id,
        )
        session.add(plan)
        await session.flush()
    else:
        if plan_id is None:
            raise ValueError(f"{action} 计划时必须提供 plan_id")
        plan = await session.scalar(
            select(CarePlan)
            .where(CarePlan.id == plan_id, CarePlan.user_id == user_id)
            .with_for_update()
        )
        if plan is None:
            raise RuntimeError("找不到当前用户的计划")
        if plan.status != CarePlanStatus.ACTIVE:
            raise ValueError("计划已经结束，不能再次修改")

        await session.execute(
            update(ProactiveCareTask)
            .where(
                ProactiveCareTask.care_plan_id == plan.id,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
            .values(
                status=ProactiveCareTaskStatus.CANCELLED,
                outcome_reason="plan_changed",
                finished_at=changed_at,
            )
        )
        plan.revision += 1
        plan.last_changed_by_message_id = trigger_message_id
        if action == "update":
            plan.summary = cleaned_summary
            plan.follow_up_at = cast(datetime, follow_up_at)
        else:
            plan.status = (
                CarePlanStatus.COMPLETED
                if action == "complete"
                else CarePlanStatus.CANCELLED
            )
            plan.resolved_at = changed_at

    if active_change:
        scheduled_for = cast(datetime, follow_up_at)
        session.add(
            ProactiveCareTask(
                user_id=user_id,
                kind=ProactiveCareTaskKind.PLAN_FOLLOW_UP,
                status=ProactiveCareTaskStatus.SCHEDULED,
                due_at=scheduled_for,
                expires_at=scheduled_for + timedelta(hours=24),
                care_plan_id=plan.id,
                care_plan_revision=plan.revision,
            )
        )
    await session.flush()
    return {
        "plan_id": str(plan.id),
        "status": plan.status.value,
        "summary": plan.summary,
        "follow_up_at": plan.follow_up_at.isoformat(),
        "revision": plan.revision,
    }


async def record_user_activity(
    session: AsyncSession,
    *,
    user_id: UUID,
    message_id: UUID,
) -> None:
    """记录用户对主动消息的回复，并重新计算日常问候候选时间。"""

    message = await session.get(Message, message_id)
    if (
        message is None
        or message.user_id != user_id
        or message.role != MessageRole.USER
    ):
        raise RuntimeError("record_user_activity 需要当前用户已保存的真实消息")

    previous_message = await session.scalar(
        select(Message)
        .where(Message.user_id == user_id, Message.id < message.id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    if previous_message is not None and previous_message.role == MessageRole.ASSISTANT:
        replied_task = await session.scalar(
            select(ProactiveCareTask)
            .join(
                AgentRun,
                AgentRun.trigger_care_task_id == ProactiveCareTask.id,
            )
            .where(
                ProactiveCareTask.user_id == user_id,
                AgentRun.user_id == user_id,
                AgentRun.result_message_id == previous_message.id,
                AgentRun.status == AgentRunStatus.COMPLETED,
            )
        )
        if replied_task is not None and replied_task.response_message_id is None:
            replied_task.response_message_id = message.id

    await schedule_routine_check_in(
        session,
        user_id=user_id,
        after=message.created_at,
        reset_existing=True,
    )


async def record_health_import(
    session: AsyncSession,
    *,
    user_id: UUID,
    import_id: UUID,
) -> UUID | None:
    """登记新心率检查，并让受后续健康数据影响的旧结果重新计算。"""

    imported = await session.scalar(
        select(WearableImport).where(
            WearableImport.id == import_id,
            WearableImport.user_id == user_id,
        )
    )
    if imported is None:
        raise RuntimeError("找不到当前用户的手环导入记录")
    if imported.record_type not in {
        WearableRecordType.HEART_RATE,
        WearableRecordType.EXERCISE,
        WearableRecordType.SLEEP,
        WearableRecordType.STEPS,
    }:
        return None

    changed_count = (
        imported.records_created + imported.records_updated + imported.records_deleted
    )
    if changed_count == 0:
        return None

    if imported.record_type is WearableRecordType.HEART_RATE:
        await lock_user_conversation(session, user_id)
        existing_task = await session.scalar(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == user_id,
                ProactiveCareTask.wearable_import_id == imported.id,
            )
        )
        if existing_task is not None:
            return existing_task.id

    tombstone_ids = set(
        await session.scalars(
            select(WearableRecordTombstone.external_record_id).where(
                WearableRecordTombstone.user_id == user_id,
                WearableRecordTombstone.import_id == imported.id,
            )
        )
    )
    changed_observations = list(
        await session.scalars(
            select(WearableObservation).where(
                WearableObservation.user_id == user_id,
                (
                    (WearableObservation.last_import_id == imported.id)
                    | WearableObservation.external_record_id.in_(tombstone_ids)
                ),
            )
        )
    )
    changed_record_ids = {
        *(observation.external_record_id for observation in changed_observations),
        *tombstone_ids,
    }
    evaluated_tasks = list(
        await session.scalars(
            select(ProactiveCareTask)
            .where(
                ProactiveCareTask.user_id == user_id,
                ProactiveCareTask.rule_id == HEART_RATE_RULE_ID,
                ProactiveCareTask.rule_version == HEART_RATE_RULE_VERSION,
            )
            .order_by(ProactiveCareTask.id)
            .with_for_update()
        )
    )
    affected_tasks: list[ProactiveCareTask] = []
    for previous_task in evaluated_tasks:
        if previous_task.status not in {
            ProactiveCareTaskStatus.SCHEDULED,
            ProactiveCareTaskStatus.RUNNING,
            ProactiveCareTaskStatus.SKIPPED,
        }:
            continue
        if (
            previous_task.status == ProactiveCareTaskStatus.SKIPPED
            and not (previous_task.outcome_reason or "").startswith("shadow_")
        ):
            continue

        evidence = previous_task.health_evidence
        if not isinstance(evidence, dict):
            raise TypeError("stored heart rate evidence is not an object")

        if imported.record_type is WearableRecordType.HEART_RATE:
            source_records = evidence.get("source_records")
            if not isinstance(source_records, dict):
                raise TypeError("stored heart rate evidence has invalid source records")
            if changed_record_ids.intersection(source_records):
                affected_tasks.append(previous_task)
            continue

        if imported.record_type is WearableRecordType.EXERCISE:
            blocking_reason = "exercise_context"
        elif imported.record_type is WearableRecordType.SLEEP:
            blocking_reason = "sleep_context"
        else:
            blocking_reason = "step_activity"
        if evidence.get("reason") == blocking_reason:
            affected_tasks.append(previous_task)
            continue

        sample_start_raw = evidence.get("sample_start")
        sample_end_raw = evidence.get("sample_end")
        if not isinstance(sample_start_raw, str) or not isinstance(sample_end_raw, str):
            continue
        sample_start = datetime.fromisoformat(sample_start_raw)
        sample_end = datetime.fromisoformat(sample_end_raw)
        margin = (
            timedelta(minutes=30)
            if imported.record_type is WearableRecordType.EXERCISE
            else timedelta()
        )
        if any(
            observation.end_time >= sample_start - margin
            and observation.start_time <= sample_end + margin
            for observation in changed_observations
        ):
            affected_tasks.append(previous_task)

    if affected_tasks:
        care_settings = await session.get(ProactiveCareSettings, user_id)
        if care_settings is None:
            raise RuntimeError("用户缺少主动关怀设置")
        for previous_task in affected_tasks:
            previous_evidence = previous_task.health_evidence
            if not isinstance(previous_evidence, dict):
                raise TypeError("stored heart rate evidence is not an object")
            previous_reason = previous_evidence.get("reason")
            can_restore_skipped_task = (
                previous_task.status == ProactiveCareTaskStatus.SKIPPED
                and isinstance(previous_reason, str)
                and previous_reason != "candidate"
                and previous_task.outcome_reason == f"shadow_{previous_reason}"
            )
            result = await evaluate_heart_rate_shadow(
                session,
                task=previous_task,
                timezone_name=care_settings.timezone,
            )
            previous_task.outcome_reason = f"shadow_{result.reason}"
            remains_candidate = (
                result.decision == "candidate"
                and result.reason != "duplicate_candidate"
            )
            if (
                previous_task.status == ProactiveCareTaskStatus.SCHEDULED
                and not remains_candidate
            ):
                previous_task.status = ProactiveCareTaskStatus.SKIPPED
                previous_task.finished_at = imported.created_at
            elif can_restore_skipped_task and remains_candidate:
                if imported.created_at >= previous_task.expires_at:
                    previous_task.status = ProactiveCareTaskStatus.EXPIRED
                    previous_task.outcome_reason = "task_expired"
                    previous_task.finished_at = imported.created_at
                else:
                    previous_task.status = ProactiveCareTaskStatus.SCHEDULED
                    previous_task.due_at = imported.created_at
                    previous_task.attempt_count = 0
                    previous_task.started_at = None
                    previous_task.finished_at = None
                    previous_task.claimed_through_message_id = None
                    previous_task.outcome_reason = None

    if imported.record_type is not WearableRecordType.HEART_RATE:
        return None

    task = ProactiveCareTask(
        user_id=user_id,
        kind=ProactiveCareTaskKind.HEALTH_EVENT,
        status=ProactiveCareTaskStatus.SCHEDULED,
        due_at=imported.created_at,
        expires_at=imported.created_at + timedelta(hours=24),
        wearable_import_id=imported.id,
        source_version_hash=imported.request_hash,
    )
    session.add(task)
    await session.flush()
    return task.id
