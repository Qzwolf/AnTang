"""同一时刻的生理风险与感受风险，分开记、分开说。

合在一起会丢掉最要紧的那一档：血糖已经很低、患者却毫无感觉。只写「风险高」
的话，复盘时看不出该练的是「察觉」，还是该处理「过度害怕」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    RiskAnnotation,
)

PhysiologicalRisk = Literal["none", "moderate", "high"]
FeltRisk = Literal["none", "some", "strong"]
RiskLabel = Literal["both", "silent", "felt_only", "neither"]

PHYSIOLOGICAL_RISKS: tuple[PhysiologicalRisk, ...] = ("none", "moderate", "high")
FELT_RISKS: tuple[FeltRisk, ...] = ("none", "some", "strong")

PHYSIOLOGICAL_LABELS: dict[str, str] = {
    "none": "血糖不在危险范围",
    "moderate": "血糖偏离目标",
    "high": "血糖到了要马上处理的水平",
}
FELT_LABELS: dict[str, str] = {
    "none": "他说没什么感觉",
    "some": "他说有点难受",
    "strong": "他说难受得很厉害",
}
LABEL_LABELS: dict[str, str] = {
    "both": "血糖危险，他自己也感到了",
    "silent": "血糖危险，他没感觉到",
    "felt_only": "血糖不算危险，他很怕",
    "neither": "这一时刻两边都不高",
}
# 给患者自己看的那一版。模型上下文里用上面的第三人称（「他没感觉到」），
# 他自己页面上必须说「你」——同一张卡上写「他没觉得」会像在说别人。
PATIENT_LABEL_LABELS: dict[str, str] = {
    "both": "血糖偏离得厉害，你也感觉到了",
    "silent": "血糖偏离得厉害，你没觉得",
    "felt_only": "血糖不算离谱，你却很怕",
    "neither": "这一时刻两边都不算高",
}

# 54 mg/dL（3.0 mmol/L）以下按 ADA 的说法属于「需要马上处理」的水平，不再是
# 「偏低一点」。上限用 250 mg/dL：他刚吃过高碳水时冲到这个数是常见的，单独一个
# 高数不足以说要马上处理，所以只在这里标出来，不升级成「high」。
_CRITICAL_LOW_MG_DL = 54
_CRITICAL_HIGH_MG_DL = 250


def physiological_risk(
    value_mg_dl: int,
    *,
    target_low_mg_dl: int,
    target_high_mg_dl: int,
) -> PhysiologicalRisk:
    """把一条读数折成生理风险档。

    用患者自己的目标范围判「偏离」，用固定阈值判「危险」——目标范围是患者和
    医生定的，可能比通用范围宽，拿它判紧急会漏掉真正危险的低值。
    """

    if value_mg_dl < _CRITICAL_LOW_MG_DL or value_mg_dl > _CRITICAL_HIGH_MG_DL:
        return "high"
    if value_mg_dl < target_low_mg_dl or value_mg_dl > target_high_mg_dl:
        return "moderate"
    return "none"


def classify_label(physiological: str, felt: str) -> RiskLabel:
    """生理风险 × 感受风险 → 这一时刻的处境。

    四种里有两种值得单独说：`silent`（危险但没感觉，最需要提醒）和
    `felt_only`（不怎么危险但很怕，需要的是安抚而不是处理低血糖）。
    """

    if physiological not in PHYSIOLOGICAL_RISKS:
        raise ValueError(f"没有这种生理风险档：{physiological}")
    if felt not in FELT_RISKS:
        raise ValueError(f"没有这种感受档：{felt}")
    dangerous = physiological != "none"
    felt_something = felt != "none"
    if dangerous and felt_something:
        return "both"
    if dangerous:
        return "silent"
    if felt_something:
        return "felt_only"
    return "neither"


@dataclass(frozen=True)
class DualRiskView:
    physiological: str
    felt: str
    label: str
    label_text: str


def describe(physiological: str, felt: str) -> DualRiskView:
    label = classify_label(physiological, felt)
    return DualRiskView(
        physiological=physiological,
        felt=felt,
        label=label,
        label_text=LABEL_LABELS[label],
    )


async def record_annotation(
    session: AsyncSession,
    *,
    user_id,
    measured_at: datetime,
    physiological_risk: str,
    felt_risk: str,
    value_mg_dl: int | None = None,
    glucose_reading_id=None,
    source_message_id=None,
    note: str = "",
) -> RiskAnnotation:
    """记下一次双风险标注。两个档位都要合法，标签由它们推出来。"""

    view = describe(physiological_risk, felt_risk)
    annotation = RiskAnnotation(
        user_id=user_id,
        glucose_reading_id=glucose_reading_id,
        measured_at=measured_at,
        value_mg_dl=value_mg_dl,
        physiological_risk=view.physiological,
        felt_risk=view.felt,
        label=view.label,
        source_message_id=source_message_id,
        note=note,
    )
    session.add(annotation)
    await session.flush()
    return annotation


async def recent_annotations(
    session: AsyncSession,
    *,
    user_id,
    since: datetime,
    limit: int = 10,
) -> list[RiskAnnotation]:
    result = await session.execute(
        select(RiskAnnotation)
        .where(RiskAnnotation.user_id == user_id, RiskAnnotation.measured_at >= since)
        .order_by(RiskAnnotation.measured_at.desc(), RiskAnnotation.id.desc())
        .limit(limit)
    )
    return list(result.scalars())


def render_dual_risk_context(annotations: list[RiskAnnotation]) -> str:
    """给模型看的一小段：他以前把哪些时刻标成过哪一种处境。"""

    if not annotations:
        return ""
    lines = ["[他之前标过的双风险]"]
    for annotation in annotations:
        label = LABEL_LABELS.get(annotation.label, annotation.label)
        when = annotation.measured_at.strftime("%m-%d %H:%M")
        lines.append(f"{when} {label}")
    lines.append(
        "这些是他自己当时的说法，不要拿来当结论；他没提，就不要主动说「你上次没感觉到」。"
    )
    return "\n".join(lines)


# 主动关怀要照顾的是「察觉」这件事，不是复述最近的数字。所以这里只挑
# silent 那一档：血糖已经低下去、他身上却没有报警。90 天是够长到能看出来，
# 又短到不会把一年前的几次当成现在的问题。
SENSATION_LOOKBACK = timedelta(days=90)
_SENSATION_LINES = 3


def render_sensation_context(annotations: list[RiskAnnotation]) -> str:
    """给主动关怀 Agent 的一小段：这个人有过「低了却没感觉」的时刻。

    没有这一档就返回空串——不能因为「没有记录」就说他没有察觉问题。
    """

    silent = [item for item in annotations if item.label == "silent"]
    if not silent:
        return ""
    lines = ["[他有过血糖已经偏低、身上却没报警的时刻]"]
    for item in silent[:_SENSATION_LINES]:
        when = item.measured_at.strftime("%m-%d %H:%M")
        if item.value_mg_dl is None:
            lines.append(f"{when} 当时他没觉得")
        else:
            mmol = item.value_mg_dl / 18
            lines.append(f"{when} {mmol:.1f} mmol/L，当时他没觉得")
    lines.append(
        "需要问身体的时候，先问「有没有出汗、心慌、发虚」，别先问数字；"
        "不要把这些次数念给他，也不要只说「注意低血糖」——这句他已经听过很多遍。"
    )
    return "\n".join(lines)


# ── 回访 ────────────────────────────────────────────────────────────────────
#
# 只有 silent 这一档回访：血糖已经低到该处理、他却什么都没感觉到。这种时刻
# 过去就过去，真正要紧的是接下来几天——有没有再低过、有没有告诉身边的人、
# 手边有没有放糖。三天是「还记得那天」和「还没彻底翻篇」之间的折中；
# 当天再问一遍像追问，一周后再问他已经想不起细节了。
FOLLOW_UP_AFTER = timedelta(days=3)
FOLLOW_UP_TTL = timedelta(days=1)
FOLLOW_UP_RULE_ID = "dual_risk_follow_up"
FOLLOW_UP_RULE_VERSION = "dual-risk-follow-up-v1"


def needs_follow_up(annotation: RiskAnnotation) -> bool:
    """这一次值不值得几天后再问一句。

    只在「危险但他没感觉」时为真。`both`（感觉到了）他当场就会处理，`felt_only`
    （不危险但很怕）要处理的是怕，不是回访低血糖；`neither` 什么都不用问。
    """

    return annotation.label == "silent"


def follow_up_topic(annotation: RiskAnnotation) -> str:
    """同一件事只回访一次：键里带标注 id，不同夜晚互不占位。"""

    return f"dual-risk:{annotation.id}"


async def queue_follow_up(
    session: AsyncSession,
    *,
    user_id,
    annotation: RiskAnnotation,
    now: datetime,
) -> ProactiveCareTask | None:
    """给一次 silent 标注排一次回访；已经有排着的就不重复排。

    返回新建的任务（或已存在的那条）；不排则返回 None。不排的情况：这一档不需要
    回访、患者关掉了健康事件、这次标注本身太旧（补记的旧事没必要再回访）。
    """

    if not needs_follow_up(annotation):
        return None
    settings = await session.get(ProactiveCareSettings, user_id)
    if settings is None or not settings.health_events_enabled:
        return None
    due_at = annotation.measured_at + FOLLOW_UP_AFTER
    if due_at <= now:
        # 补记的是好几天前的事：那时该问的已经错过了，不补一个「三天前该问的」。
        return None

    key = follow_up_topic(annotation)
    existing = await session.scalar(
        select(ProactiveCareTask.id).where(
            ProactiveCareTask.user_id == user_id,
            ProactiveCareTask.health_event_key == key,
            ProactiveCareTask.status.in_(
                (
                    ProactiveCareTaskStatus.SCHEDULED,
                    ProactiveCareTaskStatus.RUNNING,
                )
            ),
        )
    )
    if existing is not None:
        return None

    task = ProactiveCareTask(
        user_id=user_id,
        kind=ProactiveCareTaskKind.DUAL_RISK_FOLLOW_UP,
        status=ProactiveCareTaskStatus.SCHEDULED,
        due_at=due_at,
        expires_at=due_at + FOLLOW_UP_TTL,
        health_event_key=key,
        health_evidence={
            "annotation_id": str(annotation.id),
            "value_mg_dl": annotation.value_mg_dl,
            "measured_at": annotation.measured_at.isoformat(),
            "label": annotation.label,
        },
        rule_id=FOLLOW_UP_RULE_ID,
        rule_version=FOLLOW_UP_RULE_VERSION,
    )
    session.add(task)
    await session.flush()
    return task
