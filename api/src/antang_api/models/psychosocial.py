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


class PsychosocialAssessment(Base):
    """一次心理社会评估，用来决定「怎么跟这个人说话」。

    为什么存百分比而不是原始分：低血糖恐惧量表有 33 题全版、也有 9 题和 13 题的短版，
    各版本的分母不一样。存 0–100 的得分率，换量表版本也不用迁移历史数据，
    象限判定也不会因为换了版本就整体漂移。

    为什么不是每次对话都测：这些量表测的是一段时间内的稳定倾向，不是此刻的情绪。
    它的作用是给每个人定一个基线档位，而不是实时推断心情。
    """

    __tablename__ = "psychosocial_assessments"
    __table_args__ = (
        CheckConstraint(
            "fear_behavior_percent BETWEEN 0 AND 100",
            name="ck_psychosocial_fear_behavior",
        ),
        CheckConstraint(
            "fear_worry_percent BETWEEN 0 AND 100",
            name="ck_psychosocial_fear_worry",
        ),
        CheckConstraint(
            "gold_score IS NULL OR (gold_score BETWEEN 1 AND 7)",
            name="ck_psychosocial_gold_score",
        ),
        CheckConstraint(
            "who5_percent IS NULL OR (who5_percent BETWEEN 0 AND 100)",
            name="ck_psychosocial_who5",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 低血糖恐惧的行为分量表（回避、过度检查）与担忧分量表（焦虑、夜间担心）。
    fear_behavior_percent: Mapped[int] = mapped_column(Integer)
    fear_worry_percent: Mapped[int] = mapped_column(Integer)
    # Gold 计分法：1 表示总能察觉低血糖，7 表示从来察觉不到，>= 4 视为感知受损。
    gold_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # WHO-5 换算成 0–100 的百分比（原始分 0–25 × 4）。它量的是「最近两周整体状态」，
    # 与低血糖恐惧不是同一个维度，所以单独一列，不并进上面两个分量表。
    who5_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instrument: Mapped[str] = mapped_column(String(64))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RiskAnnotation(Base):
    """一个时刻的「生理风险」与「感受风险」，分开记。

    为什么分开：血糖 4.2 但患者慌得不行，和血糖 3.1 患者毫无感觉，是两种完全
    不同的处境，回应方式也不该一样。前者的风险主要是怕，后者是真的危险，而且
    往往因为他察觉不到而更危险。只存一个「风险等级」把这两件事压成一个数，
    复盘时分不出该练什么。

    为什么不实时推断：感受是患者自己在那一刻说的，不是我们从语气里猜的。
    模型读到他说了什么就填什么，看不到就留空——留空本身也是信息。
    """

    __tablename__ = "risk_annotations"
    __table_args__ = (
        CheckConstraint(
            "physiological_risk IN ('none', 'moderate', 'high')",
            name="ck_risk_annotations_physiological",
        ),
        CheckConstraint(
            "felt_risk IN ('none', 'some', 'strong')",
            name="ck_risk_annotations_felt",
        ),
        CheckConstraint(
            "label IN ('both', 'silent', 'felt_only', 'neither')",
            name="ck_risk_annotations_label",
        ),
        CheckConstraint(
            "value_mg_dl IS NULL OR (value_mg_dl >= 20 AND value_mg_dl <= 600)",
            name="ck_risk_annotations_value",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 指到那条读数；患者只说了个感觉、没说过数字时留空。
    glucose_reading_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("glucose_readings.id", ondelete="SET NULL"), nullable=True
    )
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # 数字单独存一份：那条读数可能被撤回或删掉，而「当时血糖 3.2」是这次对照的一半，
    # 跟着读数一起消失，患者回看时就只剩「血糖低」这种没头没尾的话。
    value_mg_dl: Mapped[int | None] = mapped_column(Integer, nullable=True)
    physiological_risk: Mapped[str] = mapped_column(String(16))
    felt_risk: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(16))
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
