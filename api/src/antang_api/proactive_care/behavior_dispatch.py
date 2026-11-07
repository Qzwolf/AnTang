"""把行为痕迹变成主动关怀任务：算出来、挡住重复的，再排队等 Worker 处理。

这一层只负责「该不该排一个任务」，不负责说什么。说什么由主动关怀 Agent 起草、
Auditor 审核，发多少由每日一条与每周额度管着。

校准期（`behavior_calibration_until`）内只记不发：每次命中都落一条
`behavior_signal_records`，但不排任务。阈值是我按公开研究定的，没在真人身上
验证过——先用一周真实数据看看会触发几次，再决定要不要真的发。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    BehaviorSignalRecord,
    GlucoseReading,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    RiskAnnotation,
)
from antang_api.proactive_care.behavior_signals import (
    BehaviorSignal,
    behavior_signals,
)
from antang_api.proactive_care.topics import topic_cooldown_until

BEHAVIOR_SIGNAL_RULE_ID = "behavior_signal"
BEHAVIOR_SIGNAL_RULE_VERSION = "behavior-signal-v1"
# 行为痕迹不是急事：24 小时内都没排上就作废，晚到的问候比没有更糟。
BEHAVIOR_TASK_TTL = timedelta(hours=24)
# 同一类痕迹的话题前缀。冲突检测与话题冷却共用它，所以形状要和心率那条一致。
TOPIC_PREFIX = "behavior"
# 把「低了却没感觉」和「测得比平时密」连起来看的窗口：同一个人的这两种记录
# 落在同一段时间里，更像是「他在找掌控感」，而不是「他今天测得多」。
SILENT_LINK_WINDOW = timedelta(days=30)


def behavior_event_key(signal_kind: str) -> str:
    return f"{TOPIC_PREFIX}:{signal_kind}"


async def _queued_for_topic(
    session: AsyncSession, *, user_id: UUID, event_key: str
) -> bool:
    existing = await session.scalar(
        select(ProactiveCareTask.id)
        .where(
            ProactiveCareTask.user_id == user_id,
            ProactiveCareTask.kind == ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
            ProactiveCareTask.health_event_key == event_key,
            ProactiveCareTask.status.in_(
                [
                    ProactiveCareTaskStatus.SCHEDULED,
                    ProactiveCareTaskStatus.RUNNING,
                ]
            ),
        )
        .limit(1)
    )
    return existing is not None


async def _silent_low_count(
    session: AsyncSession, *, user_id: UUID, now: datetime
) -> int:
    """近 30 天「血糖偏低、他当时没感觉」的条数。次数只给模型判断用。"""

    count = await session.scalar(
        select(func_count())
        .select_from(RiskAnnotation)
        .where(
            RiskAnnotation.user_id == user_id,
            RiskAnnotation.label == "silent",
            RiskAnnotation.measured_at >= now - SILENT_LINK_WINDOW,
        )
    )
    return int(count or 0)


def func_count():  # noqa: ANN201 - 只是把 sqlalchemy.func 收在一处
    from sqlalchemy import func

    return func.count()


async def queue_behavior_signals(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> list[UUID]:
    """给现在命中的每一条痕迹排一个任务；已经在队里或刚提过的跳过。

    每一条命中都会落一条 `behavior_signal_records`，`dispatched` 说明有没有真的
    排成任务、`suppressed_reason` 说明为什么没排。校准期只记不发。
    """

    moment = now if now is not None else datetime.now(timezone.utc)
    settings = await session.get(ProactiveCareSettings, user_id)
    calibrating = (
        settings is not None
        and settings.behavior_calibration_until is not None
        and settings.behavior_calibration_until > moment
    )
    silent_lows = await _silent_low_count(session, user_id=user_id, now=moment)
    created: list[UUID] = []
    for signal in await behavior_signals(
        session, user_id=user_id, now=moment, timezone_name=timezone_name
    ):
        event_key = behavior_event_key(signal.kind)
        evidence = dict(signal.as_dict())
        # 有过「低了却没感觉」的人，反复测量更可能是想找回掌控感：这句话跟着
        # 证据一起交给模型，不写进患者看到的消息里。
        evidence["silent_lows_30d"] = silent_lows
        suppressed: str | None = None
        if calibrating:
            suppressed = "calibration"
        elif await _queued_for_topic(session, user_id=user_id, event_key=event_key):
            suppressed = "already_queued"
        else:
            resumes_at = await topic_cooldown_until(
                session, user_id=user_id, prefix=event_key, now=moment
            )
            due_at = resumes_at if resumes_at is not None else moment
            if due_at > moment + BEHAVIOR_TASK_TTL:
                # 冷却还没结束就已经过了有效期：这次不提，等下次算出来再说。
                suppressed = "expired_window"
            else:
                task = ProactiveCareTask(
                    user_id=user_id,
                    kind=ProactiveCareTaskKind.BEHAVIOR_SIGNAL,
                    status=ProactiveCareTaskStatus.SCHEDULED,
                    due_at=due_at,
                    expires_at=due_at + BEHAVIOR_TASK_TTL,
                    health_event_key=event_key,
                    health_evidence=evidence,
                    rule_id=BEHAVIOR_SIGNAL_RULE_ID,
                    rule_version=BEHAVIOR_SIGNAL_RULE_VERSION,
                )
                session.add(task)
                await session.flush()
                created.append(task.id)
            if suppressed is None and resumes_at is not None:
                suppressed = "cooling_down"
        session.add(
            BehaviorSignalRecord(
                user_id=user_id,
                kind=signal.kind,
                detected_at=moment,
                dispatched=suppressed is None,
                suppressed_reason=suppressed,
                evidence=evidence,
            )
        )
        await session.flush()
    return created


async def sweep_behavior_signals(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> list[UUID]:
    """扫一遍最近有血糖记录、且开着健康事件的用户，给命中的排任务。

    只扫「最近一天里有读数」的人：没有新数据的人不会凭空多出痕迹，扫他等于浪费
    一次查询。校准期内这些人也会被扫到，只是只记不发。
    """

    moment = now if now is not None else datetime.now(timezone.utc)
    since = moment - timedelta(hours=24)
    user_ids = (
        await session.scalars(
            select(distinct(GlucoseReading.user_id))
            .join(
                ProactiveCareSettings,
                ProactiveCareSettings.user_id == GlucoseReading.user_id,
            )
            .where(
                GlucoseReading.measured_at >= since,
                GlucoseReading.measured_at <= moment,
                GlucoseReading.revoked_at.is_(None),
                ProactiveCareSettings.health_events_enabled.is_(True),
            )
            .limit(limit)
        )
    ).all()
    created: list[UUID] = []
    for user_id in user_ids:
        settings = await session.get(ProactiveCareSettings, user_id)
        if settings is None:
            continue
        created.extend(
            await queue_behavior_signals(
                session,
                user_id=user_id,
                now=moment,
                timezone_name=settings.timezone,
            )
        )
    return created


def signal_from_task_evidence(
    evidence: dict[str, object] | None,
) -> BehaviorSignal | None:
    """从任务里存的证据还原一条痕迹，用来核对「这一刻还成不成立」。"""

    if not evidence:
        return None
    kind = evidence.get("kind")
    label = evidence.get("label")
    occurred = evidence.get("occurred")
    window_days = evidence.get("window_days")
    if (
        not isinstance(kind, str)
        or not isinstance(label, str)
        or not isinstance(occurred, int)
        or not isinstance(window_days, int)
    ):
        return None
    return BehaviorSignal(
        kind=kind,
        label=label,
        occurred=occurred,
        window_days=window_days,
        evidence=(),
    )


__all__ = [
    "BEHAVIOR_SIGNAL_RULE_ID",
    "BEHAVIOR_SIGNAL_RULE_VERSION",
    "BEHAVIOR_TASK_TTL",
    "SILENT_LINK_WINDOW",
    "TOPIC_PREFIX",
    "behavior_event_key",
    "queue_behavior_signals",
    "signal_from_task_evidence",
    "sweep_behavior_signals",
]
