from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    HYPOGLYCEMIA_BELIEFS,
    FearBehaviorLog,
    HypoglycemiaBeliefEvent,
    PsychosocialAssessment,
)
from antang_api.psychosocial import FEAR_HIGH_PERCENT, PsychosocialProfile
from antang_api.psychosocial import read_latest_assessment_row
from antang_api.psychosocial.reframes import render_reframe_hint

# 回避类行为：直接缩小生活范围。过度监测（反复检查、夜间确认）单独统计，
# 因为它表达的是担忧而不是回避，两者在临床上不是一回事。
AVOIDANCE_KINDS: tuple[str, ...] = ("exercise_avoidance", "insulin_hold")
MONITORING_KINDS: tuple[str, ...] = ("extra_check", "night_check")

# 「行为上的回避」换算成周频次后，达到这个值就算隐式风险偏高。
# 这是先验阈值，用于把不一致提示出来，不是诊断标准；样本足够后要重新标定。
AVOIDANCE_WEEKLY_THRESHOLD = 2.0

Discrepancy = Literal["aligned", "implicit_higher", "explicit_higher", "unknown"]

_BELIEF_LABELS = dict(HYPOGLYCEMIA_BELIEFS)


@dataclass(frozen=True)
class BeliefNode:
    """信念图的一个节点在当前窗口里的状态。"""

    belief_code: str
    label: str
    mentions: int
    reframes: int
    denials: int
    latest_quote: str
    latest_detected_at: datetime

    @property
    def resolved(self) -> bool:
        """最近一次观测是「被否认或已松动」，且没有更新的复发。"""
        return self.denials + self.reframes >= self.mentions


@dataclass(frozen=True)
class FearSummary:
    """把显性分数与行为代理放在一起看的周报。

    两个数字不一致时才是信息量最大的地方：量表低而回避行为多，
    说明这个人不愿意（或没能力）在问卷上表达恐惧。
    """

    window_start: datetime
    window_end: datetime
    behavior_counts: dict[str, int]
    behavior_total: int
    monitoring_weekly: float
    avoidance_weekly: float
    beliefs: tuple[BeliefNode, ...]
    assessment: PsychosocialProfile | None
    assessment_row: PsychosocialAssessment | None
    discrepancy: Discrepancy
    discrepancy_note: str


def record_behavior(
    session: AsyncSession,
    *,
    user_id: UUID,
    kind: str,
    count: int = 1,
    occurred_at: datetime | None = None,
    note: str | None = None,
    source_message_id: UUID | None = None,
) -> FearBehaviorLog:
    """写入一次行为记录。kind 由模型判定，这里只落库。"""

    log = FearBehaviorLog(
        user_id=user_id,
        kind=kind,
        count=count,
        occurred_at=occurred_at or datetime.now(UTC),
        note=note,
        source_message_id=source_message_id,
    )
    session.add(log)
    return log


def record_belief(
    session: AsyncSession,
    *,
    user_id: UUID,
    belief_code: str,
    quote: str,
    occurrence: str = "mentioned",
    trigger_context: str | None = None,
    fear_intensity: int | None = None,
    detected_at: datetime | None = None,
    source_message_id: UUID | None = None,
) -> HypoglycemiaBeliefEvent:
    """写入一次信念观测。同一个节点可以反复出现，图谱状态由观测推出。"""

    event = HypoglycemiaBeliefEvent(
        user_id=user_id,
        belief_code=belief_code,
        quote=quote,
        occurrence=occurrence,
        trigger_context=trigger_context,
        fear_intensity=fear_intensity,
        detected_at=detected_at or datetime.now(UTC),
        source_message_id=source_message_id,
    )
    session.add(event)
    return event


async def read_behavior_logs(
    session: AsyncSession,
    *,
    user_id: UUID,
    days: int = 28,
    now: datetime | None = None,
    limit: int = 200,
    include_deleted: bool = False,
) -> list[FearBehaviorLog]:
    """按时间倒序列出窗口内的行为记录，供 App 逐条展示与撤回。

    汇总卡片只给次数，删不了；要让人自己删，必须先把「哪一条」摆出来。
    `include_deleted` 只给医护端用：患者删掉的那条对本人不再出现，
    但已同步给医护的记录要留着，复诊时还能核对，并标注「患者已删除」。
    """

    window_end = now or datetime.now(UTC)
    window_start = window_end - timedelta(days=days)
    statement = select(FearBehaviorLog).where(
        FearBehaviorLog.user_id == user_id,
        FearBehaviorLog.occurred_at >= window_start,
        FearBehaviorLog.occurred_at <= window_end,
    )
    if not include_deleted:
        statement = statement.where(FearBehaviorLog.deleted_at.is_(None))
    rows = await session.execute(
        statement.order_by(FearBehaviorLog.occurred_at.desc()).limit(limit)
    )
    return list(rows.scalars())


async def read_belief_events(
    session: AsyncSession,
    *,
    user_id: UUID,
    days: int = 90,
    now: datetime | None = None,
    limit: int = 200,
) -> list[HypoglycemiaBeliefEvent]:
    """按时间倒序列出窗口内的信念观测，同样是为了能逐条删。"""

    window_end = now or datetime.now(UTC)
    window_start = window_end - timedelta(days=days)
    rows = await session.execute(
        select(HypoglycemiaBeliefEvent)
        .where(
            HypoglycemiaBeliefEvent.user_id == user_id,
            HypoglycemiaBeliefEvent.detected_at >= window_start,
            HypoglycemiaBeliefEvent.detected_at <= window_end,
        )
        .order_by(HypoglycemiaBeliefEvent.detected_at.desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def delete_behavior_log(
    session: AsyncSession,
    *,
    user_id: UUID,
    log_id: UUID,
) -> bool:
    """患者自己撤回一条行为记录：只从本人界面上删掉（软删除）。

    为什么不做真删：这条记录可能已经被医护端看到（比如自行调药），
    而「患者单方面抹掉、医生那边也消失」这种设计会让复诊核对失去依据。
    所以这里打 deleted_at：患者的列表、统计、上下文都不再包含它，
    医护端查询仍能看到，并标注「患者已删除」。只能删自己的。
    """

    deleted_id = await session.scalar(
        update(FearBehaviorLog)
        .where(
            FearBehaviorLog.id == log_id,
            FearBehaviorLog.user_id == user_id,
            FearBehaviorLog.deleted_at.is_(None),
        )
        .values(deleted_at=datetime.now(UTC))
        .returning(FearBehaviorLog.id)
    )
    return deleted_id is not None


async def delete_belief_event(
    session: AsyncSession,
    *,
    user_id: UUID,
    event_id: UUID,
) -> bool:
    """患者自己撤回一次信念观测：真删，连同那句原话。"""

    deleted_id = await session.scalar(
        delete(HypoglycemiaBeliefEvent)
        .where(
            HypoglycemiaBeliefEvent.id == event_id,
            HypoglycemiaBeliefEvent.user_id == user_id,
        )
        .returning(HypoglycemiaBeliefEvent.id)
    )
    return deleted_id is not None


async def read_fear_summary(
    session: AsyncSession,
    *,
    user_id: UUID,
    days: int = 28,
    now: datetime | None = None,
) -> FearSummary:
    """读出最近窗口内的行为频次、信念状态，并与显性分数比对。"""

    window_end = now or datetime.now(UTC)
    window_start = window_end - timedelta(days=days)
    weeks = days / 7

    behavior_rows = (
        await session.execute(
            select(FearBehaviorLog).where(
                FearBehaviorLog.user_id == user_id,
                FearBehaviorLog.occurred_at >= window_start,
                FearBehaviorLog.occurred_at <= window_end,
                # 患者删掉的那条不再进本人的统计与上下文；
                # 医护端自己读库时另走 include_deleted。
                FearBehaviorLog.deleted_at.is_(None),
            )
        )
    ).scalars()

    counts: Counter[str] = Counter()
    for row in behavior_rows:
        counts[row.kind] += row.count

    belief_rows = (
        await session.execute(
            select(HypoglycemiaBeliefEvent)
            .where(
                HypoglycemiaBeliefEvent.user_id == user_id,
                HypoglycemiaBeliefEvent.detected_at >= window_start,
                HypoglycemiaBeliefEvent.detected_at <= window_end,
            )
            .order_by(HypoglycemiaBeliefEvent.detected_at.desc())
        )
    ).scalars()

    per_belief: dict[str, dict[str, int]] = {}
    latest: dict[str, HypoglycemiaBeliefEvent] = {}
    for row in belief_rows:
        bucket = per_belief.setdefault(
            row.belief_code, {"mentioned": 0, "reframed": 0, "denied": 0}
        )
        bucket[row.occurrence] += 1
        latest.setdefault(row.belief_code, row)

    beliefs = tuple(
        BeliefNode(
            belief_code=code,
            label=_BELIEF_LABELS.get(code, code),
            mentions=bucket["mentioned"],
            reframes=bucket["reframed"],
            denials=bucket["denied"],
            latest_quote=latest[code].quote,
            latest_detected_at=latest[code].detected_at,
        )
        for code, bucket in sorted(
            per_belief.items(),
            key=lambda item: latest[item[0]].detected_at,
            reverse=True,
        )
    )

    assessment_row = await read_latest_assessment_row(session, user_id)
    assessment = (
        PsychosocialProfile(
            fear_behavior_percent=assessment_row.fear_behavior_percent,
            fear_worry_percent=assessment_row.fear_worry_percent,
            gold_score=assessment_row.gold_score,
        )
        if assessment_row is not None
        else None
    )

    monitoring_weekly = (
        sum(counts[kind] for kind in MONITORING_KINDS) / weeks if weeks else 0.0
    )
    avoidance_weekly = (
        sum(counts[kind] for kind in AVOIDANCE_KINDS) / weeks if weeks else 0.0
    )

    discrepancy, note = _judge_discrepancy(
        assessment,
        avoidance_weekly=avoidance_weekly,
        monitoring_weekly=monitoring_weekly,
    )

    return FearSummary(
        window_start=window_start,
        window_end=window_end,
        behavior_counts=dict(counts),
        behavior_total=sum(counts.values()),
        monitoring_weekly=monitoring_weekly,
        avoidance_weekly=avoidance_weekly,
        beliefs=beliefs,
        assessment=assessment,
        assessment_row=assessment_row,
        discrepancy=discrepancy,
        discrepancy_note=note,
    )


_BEHAVIOR_KIND_LABELS = {
    "extra_check": "额外多测了一次血糖",
    "night_check": "夜间反复确认血糖",
    "comfort_meal": "因为担心而加餐",
    "exercise_avoidance": "运动回避",
    "insulin_hold": "自行减少胰岛素",
    "insulin_change": "自行调整胰岛素剂量",
}

# 对外暴露同一份词表，App 的「我的记录」与汇总卡片因此不会出现两套叫法。
BEHAVIOR_KIND_LABELS = dict(_BEHAVIOR_KIND_LABELS)


def render_behavior_context(summary: FearSummary) -> str:
    """把窗口内的行为与信念压成一段内部提示，供对话侧使用。

    三条约定的理由：
    - 只写次数与用户原话，不写「该用户恐惧程度高」这类判断——判断不是这个系统的输出；
    - 信念最多列三条，按提及次数降序、未松动的优先，避免长期占据上下文；
    - 明确禁止在回复里复述这些数字，否则行为监测本身会变成新的焦虑来源。
    """

    if summary.behavior_total == 0 and not summary.beliefs:
        return ""

    lines = [
        "[近期行为与低血糖信念]",
        "以下是服务器统计的最近记录，只用于决定问什么、怎么问。",
    ]

    counted = [
        (kind, count) for kind, count in summary.behavior_counts.items() if count > 0
    ]
    if counted:
        detail = "、".join(
            f"{_BEHAVIOR_KIND_LABELS.get(kind, kind)} {count} 次"
            for kind, count in counted
        )
        lines.append(f"近 {_window_days(summary)} 天记录到的行为：{detail}。")

    active = [node for node in summary.beliefs if not node.resolved][:3]
    if active:
        lines.append("反复出现、还没松动的信念：")
        lines.extend(f"- {node.label}（提到 {node.mentions} 次）" for node in active)

    resolved = [node for node in summary.beliefs if node.resolved][:3]
    if resolved:
        labels = "、".join(node.label for node in resolved)
        lines.append(
            f"已经松动或用户否认过的信念：{labels}。这些不要再当成仍然成立的前提。"
        )

    repeated = [node for node in active if node.mentions >= 2]
    if repeated:
        hint = render_reframe_hint(repeated[0].belief_code, repeated[0].label)
        if hint:
            lines.append(hint)

    if summary.discrepancy in {"implicit_higher", "explicit_higher"}:
        lines.append(f"两路证据不一致：{summary.discrepancy_note}")

    lines.append(
        "约束：不要在回复里报这些次数，也不要说「你在回避」；"
        "先问清当时的情境，再把要不要调整交给用户决定。"
    )
    return "\n".join(lines)


def _window_days(summary: FearSummary) -> int:
    span = summary.window_end - summary.window_start
    return max(1, round(span.total_seconds() / 86400))


def _judge_discrepancy(
    assessment: PsychosocialProfile | None,
    *,
    avoidance_weekly: float,
    monitoring_weekly: float,
) -> tuple[Discrepancy, str]:
    """比对「问卷上承认的回避」与「实际发生的回避」。

    返回的是提示，不是结论：它只说明两路证据不一致、值得在对话里核实。
    """

    if assessment is None:
        if avoidance_weekly or monitoring_weekly:
            return (
                "unknown",
                "还没有心理社会评估，行为记录已经存在；建议先补一次评估再比对。",
            )
        return "unknown", "窗口内既没有评估也没有行为记录。"

    explicit_high = assessment.fear_behavior_percent >= FEAR_HIGH_PERCENT
    implicit_high = avoidance_weekly >= AVOIDANCE_WEEKLY_THRESHOLD

    if explicit_high and not implicit_high:
        return (
            "explicit_higher",
            (
                f"问卷的回避分量表为 {assessment.fear_behavior_percent} 分（偏高），"
                "但窗口内几乎没有回避行为记录；可能是实际回避没有被说出来，"
                "也可能分数受近期事件影响。"
            ),
        )
    if implicit_high and not explicit_high:
        return (
            "implicit_higher",
            (
                f"问卷的回避分量表只有 {assessment.fear_behavior_percent} 分，"
                f"但实际回避行为约 {avoidance_weekly:.1f} 次/周；"
                "这属于「嘴上说不怕、行为上怕」，不宜只按问卷分数判断。"
            ),
        )
    if explicit_high and implicit_high:
        return "aligned", "问卷与行为都指向回避偏高，方向一致。"
    if monitoring_weekly:
        return (
            "aligned",
            (
                f"问卷与行为都不指向明显回避，但过度监测约 {monitoring_weekly:.1f} 次/周，"
                "关注点可能在「随时确认」而不是「回避」。"
            ),
        )
    return "aligned", "问卷与行为都未见明显回避。"
