"""饮食与用药记录：患者自己记下的「吃了什么」「打了什么」。

两张表都只记录事实，不给建议：碳水是估计值（患者可改），剂量是患者自己填的，
系统不计算也不推荐。用药自行调量的行为由患者自己记录，必要时可以给医生看。
"""

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

MEAL_TYPES: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack")

MEDICATION_KINDS: tuple[str, ...] = ("insulin", "oral", "other")


class MealLog(Base):
    __tablename__ = "meal_logs"
    __table_args__ = (
        CheckConstraint(
            "meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')",
            name="ck_meal_type",
        ),
        CheckConstraint(
            "carbs_grams IS NULL OR carbs_grams BETWEEN 0 AND 400",
            name="ck_meal_carbs_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    eaten_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 碳水是估计值，允许为空（患者只记了「吃了什么」）；范围挡住明显录错的数字。
    carbs_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MedicationLog(Base):
    __tablename__ = "medication_logs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('insulin', 'oral', 'other')", name="ck_medication_kind"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 只记患者输入的原文（「诺和锐 6 单位」「二甲双胍 一片」），不在这里做任何计算。
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    dose_text: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
