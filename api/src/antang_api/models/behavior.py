from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base

# 因担心低血糖而做（或不做）的事。kind 由模型在对话中判定后写入，
# 代码只做取值约束，不做关键词匹配——判错时能追到具体那条消息。
BEHAVIOR_KINDS: tuple[str, ...] = (
    "extra_check",
    "night_check",
    "comfort_meal",
    "exercise_avoidance",
    "insulin_hold",
    # 自行增减剂量：与「少打」分开记，因为加量与减量的风险不同
    # （一个怕低血糖、一个怕高血糖），报告给医生时的处置也不同。
    "insulin_change",
)

# 认知图谱的节点词表。只在对话里真正出现时才落一条，不预填、不推断。
HYPOGLYCEMIA_BELIEFS: tuple[tuple[str, str], ...] = (
    ("hypo_causes_death", "低血糖会导致死亡"),
    ("hypo_causes_coma", "低血糖会让我昏迷、没人发现"),
    ("hypo_is_losing_control", "低血糖意味着失控"),
    ("hypo_reveals_weakness", "低血糖会在别人面前暴露我的软弱"),
    ("hypo_is_my_fault", "发生低血糖是我没管好自己"),
    ("hypo_always_repeats", "只要有过一次，就一定会再发生"),
    ("hypo_cannot_be_detected", "我感觉不到低血糖，所以必须随时检查"),
    ("hypo_blocks_normal_life", "有低血糖风险就不该运动、不该出门"),
)

# 一次信念观测是新增、被松动，还是被明确否认。用来追踪同一节点在时间上的走向，
# 而不是只留一个最新快照。
BELIEF_OCCURRENCES: tuple[str, ...] = ("mentioned", "reframed", "denied")

_BEHAVIOR_KINDS_SQL = ", ".join(f"'{kind}'" for kind in BEHAVIOR_KINDS)
_BELIEF_CODES_SQL = ", ".join(f"'{code}'" for code, _ in HYPOGLYCEMIA_BELIEFS)
_BELIEF_OCCURRENCES_SQL = ", ".join(f"'{value}'" for value in BELIEF_OCCURRENCES)


class FearBehaviorLog(Base):
    """一次「因为怕低血糖而做（或不做）的事」。

    为什么记行为而不是只记量表分数：量表测的是这个人愿意承认的恐惧，
    行为记录的是实际发生的回避与过度补偿。两者会分离——嘴上说不太怕、
    睡前却反复查五次。这种不一致本身就是需要干预的信号，也是本项目
    相对于纯问卷方案新增的那一路证据。

    为什么不合并成计数列：一行一次发生的记录保留时间与来源消息，
    既能算周频次，也能回溯到原话核对，事后不会被模型的重述污染。
    """

    __tablename__ = "fear_behavior_logs"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_BEHAVIOR_KINDS_SQL})", name="ck_fear_behavior_kind"),
        CheckConstraint("count BETWEEN 1 AND 50", name="ck_fear_behavior_count"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    count: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 患者可以把自己那条从界面上删掉，但已经同步给医护的记录不能一起消失：
    # 患者侧查询只看 deleted_at 为空的行，医护端带上已删除的行并标注。
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class HypoglycemiaBeliefEvent(Base):
    """对话里出现的一次低血糖灾难化信念。

    这是认知图谱的观测表，不是信念表本身：同一个节点会随时间出现多次
    （新增、被松动、被否认），图谱的当前状态由这些观测按时间推出来。

    三个字段构成 CASE 那套「认知与情感对齐」的最小实现：
    quote 是认知图里的证据，trigger_context 是触发情境，
    fear_intensity 是同一次事件在情感图上的强度。
    """

    __tablename__ = "hypoglycemia_belief_events"
    __table_args__ = (
        CheckConstraint(
            f"belief_code IN ({_BELIEF_CODES_SQL})",
            name="ck_belief_code",
        ),
        CheckConstraint(
            f"occurrence IN ({_BELIEF_OCCURRENCES_SQL})",
            name="ck_belief_occurrence",
        ),
        CheckConstraint(
            "fear_intensity IS NULL OR (fear_intensity BETWEEN 0 AND 100)",
            name="ck_belief_fear_intensity",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    belief_code: Mapped[str] = mapped_column(String(48), index=True)
    quote: Mapped[str] = mapped_column(String(300))
    trigger_context: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fear_intensity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurrence: Mapped[str] = mapped_column(String(16))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
