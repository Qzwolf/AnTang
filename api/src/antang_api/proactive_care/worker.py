import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import case, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from antang_api.database import lock_user_conversation, session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    Message,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
)
from antang_api.proactive_care.behavior_dispatch import sweep_behavior_signals
from antang_api.proactive_care.service import (
    has_scheduled_routine_replacement,
    schedule_routine_check_in,
)
from antang_api.settings import settings

TaskProcessor = Callable[[UUID, UUID], Awaitable[None]]


class ProactiveCareWorker:
    """从 PostgreSQL 领取主动关怀任务，并把租约交给具体处理流程。"""

    def __init__(
        self,
        *,
        process_task: TaskProcessor,
        sessions: async_sessionmaker[AsyncSession] = session_factory,
        behavior_sweep_interval: timedelta = timedelta(minutes=10),
    ) -> None:
        self._process_task = process_task
        self._sessions = sessions
        self._behavior_sweep_interval = behavior_sweep_interval
        self._last_behavior_sweep: datetime | None = None

    async def _sweep_behavior_signals(self, now: datetime) -> None:
        """多久扫一次行为痕迹：它们不是急事，十分钟一次够用。

        放在领取任务之前，所以刚扫出来的任务在同一轮就能被领走；扫不动不该
        影响正常任务，出错只记日志。
        """

        if (
            self._last_behavior_sweep is not None
            and now - self._last_behavior_sweep < self._behavior_sweep_interval
        ):
            return
        self._last_behavior_sweep = now
        try:
            async with self._sessions() as session:
                created = await sweep_behavior_signals(session, now=now)
                await session.commit()
                if created:
                    logger.info("行为痕迹排入 {} 个主动关怀任务", len(created))
        except Exception:
            logger.exception("扫描行为痕迹失败，下一轮再试")

    async def run_once(self, *, now: datetime | None = None) -> bool:
        """最多处理一项到期任务；完全没有可做的工作时返回 False。"""

        claimed_at = now or datetime.now(timezone.utc)
        await self._sweep_behavior_signals(claimed_at)
        other_task = aliased(ProactiveCareTask)
        active_core_run = aliased(AgentRun)
        async with self._sessions() as session:
            task = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    or_(
                        (ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED)
                        & (ProactiveCareTask.due_at <= claimed_at),
                        (ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING)
                        & (ProactiveCareTask.lease_expires_at <= claimed_at),
                    ),
                    ~exists(
                        select(1).where(
                            other_task.user_id == ProactiveCareTask.user_id,
                            other_task.id != ProactiveCareTask.id,
                            other_task.status == ProactiveCareTaskStatus.RUNNING,
                        )
                    ),
                    ~exists(
                        select(1).where(
                            active_core_run.user_id == ProactiveCareTask.user_id,
                            active_core_run.parent_run_id.is_(None),
                            active_core_run.agent_name == "core_agent",
                            active_core_run.status.in_(
                                [
                                    AgentRunStatus.RUNNING,
                                    AgentRunStatus.WAITING_FOR_USER,
                                ]
                            ),
                        )
                    ),
                )
                .order_by(
                    case(
                        (
                            ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                            0,
                        ),
                        else_=1,
                    ),
                    case(
                        (
                            ProactiveCareTask.kind
                            == ProactiveCareTaskKind.HEALTH_EVENT,
                            0,
                        ),
                        (
                            ProactiveCareTask.kind
                            == ProactiveCareTaskKind.PLAN_FOLLOW_UP,
                            1,
                        ),
                        else_=2,
                    ),
                    ProactiveCareTask.due_at,
                    ProactiveCareTask.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return False

            if not await lock_user_conversation(session, task.user_id, wait=False):
                await session.rollback()
                return False

            active_core_run_id = await session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.user_id == task.user_id,
                    AgentRun.parent_run_id.is_(None),
                    AgentRun.agent_name == "core_agent",
                    AgentRun.status.in_(
                        [AgentRunStatus.RUNNING, AgentRunStatus.WAITING_FOR_USER]
                    ),
                )
                .limit(1)
            )
            if active_core_run_id is not None:
                await session.rollback()
                return False

            active_task_id = await session.scalar(
                select(ProactiveCareTask.id)
                .where(
                    ProactiveCareTask.user_id == task.user_id,
                    ProactiveCareTask.id != task.id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                )
                .limit(1)
            )
            if active_task_id is not None:
                await session.rollback()
                return False

            interrupted_error = (
                "CareLeaseExpired"
                if task.status == ProactiveCareTaskStatus.RUNNING
                else "CareAttemptAbandoned"
            )
            interrupted_message = (
                "主动关怀任务租约已过期"
                if task.status == ProactiveCareTaskStatus.RUNNING
                else "主动关怀任务留下了未结束的旧运行"
            )
            care_run_ids = select(AgentRun.id).where(
                AgentRun.trigger_care_task_id == task.id
            )
            await session.execute(
                update(AgentToolCall)
                .where(
                    AgentToolCall.agent_run_id.in_(care_run_ids),
                    AgentToolCall.status == AgentToolCallStatus.RUNNING,
                )
                .values(
                    status=AgentToolCallStatus.FAILED,
                    error_type=interrupted_error,
                    error_message=interrupted_message,
                    finished_at=claimed_at,
                )
            )
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.trigger_care_task_id == task.id,
                    AgentRun.status.in_(
                        [
                            AgentRunStatus.RUNNING,
                            AgentRunStatus.WAITING_FOR_USER,
                        ]
                    ),
                )
                .values(
                    status=AgentRunStatus.FAILED,
                    error_type=interrupted_error,
                    error_message=interrupted_message,
                    finished_at=claimed_at,
                )
            )

            if task.expires_at <= claimed_at:
                task.status = ProactiveCareTaskStatus.EXPIRED
                task.lease_token = None
                task.lease_expires_at = None
                task.outcome_reason = "task_expired"
                task.finished_at = claimed_at
                if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                    await schedule_routine_check_in(
                        session,
                        user_id=task.user_id,
                        after=claimed_at,
                        reset_existing=False,
                    )
                await session.commit()
                return True

            if task.attempt_count >= settings.proactive_care_max_attempts:
                task.status = ProactiveCareTaskStatus.FAILED
                task.lease_token = None
                task.lease_expires_at = None
                task.outcome_reason = "attempts_exhausted"
                task.finished_at = claimed_at
                if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN:
                    await schedule_routine_check_in(
                        session,
                        user_id=task.user_id,
                        after=claimed_at,
                        reset_existing=False,
                    )
                await session.commit()
                return True

            lease_token = uuid4()
            task.claimed_through_message_id = await session.scalar(
                select(Message.id)
                .where(Message.user_id == task.user_id)
                .order_by(Message.id.desc())
                .limit(1)
            )
            task.status = ProactiveCareTaskStatus.RUNNING
            task.lease_token = lease_token
            task.lease_expires_at = claimed_at + timedelta(
                minutes=settings.proactive_care_lease_minutes
            )
            task.attempt_count += 1
            task.started_at = task.started_at or claimed_at
            task.outcome_reason = None
            task_id = task.id
            await session.commit()

        try:
            async with asyncio.timeout(settings.proactive_care_lease_minutes * 60):
                await self._process_task(task_id, lease_token)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._record_failure(task_id, lease_token, error)
            logger.bind(
                task_id=str(task_id),
                error_type=type(error).__name__,
            ).error("proactive_care_task_failed")
            return True

        needs_failure = False
        async with self._sessions() as session:
            task = await session.get(ProactiveCareTask, task_id)
            needs_failure = (
                task is not None
                and task.status == ProactiveCareTaskStatus.RUNNING
                and task.lease_token == lease_token
            )
        if needs_failure:
            await self._record_failure(
                task_id,
                lease_token,
                RuntimeError("任务处理函数返回时没有保存终态"),
            )
        return True

    async def run_forever(self) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(settings.proactive_care_worker_poll_seconds)

    async def _record_failure(
        self,
        task_id: UUID,
        lease_token: UUID,
        error: Exception,
    ) -> None:
        failed_at = datetime.now(timezone.utc)
        async with self._sessions() as session:
            user_id = await session.scalar(
                select(ProactiveCareTask.user_id).where(ProactiveCareTask.id == task_id)
            )
            if user_id is None:
                return
            await lock_user_conversation(session, user_id)
            task = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == task_id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                )
                .with_for_update()
            )
            if task is None:
                return

            care_run_ids = select(AgentRun.id).where(
                AgentRun.trigger_care_task_id == task.id
            )
            await session.execute(
                update(AgentToolCall)
                .where(
                    AgentToolCall.agent_run_id.in_(care_run_ids),
                    AgentToolCall.status == AgentToolCallStatus.RUNNING,
                )
                .values(
                    status=AgentToolCallStatus.FAILED,
                    error_type="CareTaskProcessorError",
                    error_message="主动关怀任务处理失败",
                    finished_at=failed_at,
                )
            )
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.trigger_care_task_id == task.id,
                    AgentRun.status.in_(
                        [
                            AgentRunStatus.RUNNING,
                            AgentRunStatus.WAITING_FOR_USER,
                        ]
                    ),
                )
                .values(
                    status=AgentRunStatus.FAILED,
                    error_type="CareTaskProcessorError",
                    error_message="主动关怀任务处理失败",
                    finished_at=failed_at,
                )
            )

            retry_at = failed_at + timedelta(
                seconds=settings.proactive_care_retry_seconds
            )
            replacement_exists = (
                task.attempt_count < settings.proactive_care_max_attempts
                and retry_at < task.expires_at
                and await has_scheduled_routine_replacement(session, task)
            )

            task.lease_token = None
            task.lease_expires_at = None
            task.outcome_reason = f"processor_error:{type(error).__name__}"
            if task.attempt_count >= settings.proactive_care_max_attempts:
                task.status = ProactiveCareTaskStatus.FAILED
                task.finished_at = failed_at
            else:
                if retry_at >= task.expires_at:
                    task.status = ProactiveCareTaskStatus.EXPIRED
                    task.finished_at = failed_at
                elif replacement_exists:
                    task.status = ProactiveCareTaskStatus.CANCELLED
                    task.outcome_reason = "routine_replaced"
                    task.finished_at = failed_at
                else:
                    task.status = ProactiveCareTaskStatus.SCHEDULED
                    task.due_at = retry_at
            if task.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN and task.status in {
                ProactiveCareTaskStatus.FAILED,
                ProactiveCareTaskStatus.EXPIRED,
            }:
                await schedule_routine_check_in(
                    session,
                    user_id=task.user_id,
                    after=failed_at,
                    reset_existing=False,
                )
            await session.commit()
