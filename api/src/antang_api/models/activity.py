"""患者自己补记的运动与睡眠。

手环导入是主路径，但很多人（尤其怕低血糖不敢运动的人）根本不戴表：只导入的话，
这两页对他们永远是空的。所以补一对手工记录表，字段刻意做得少——做了什么、多久、
什么时候；睡了多久、几点睡几点醒。不记心率、不记卡路里，免得看起来像体测报告。

同样只记录事实：不算消耗、不评价「运动量够不够」，也不因为某天没动就提醒。
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

ACTIVITY_KINDS: tuple[str, ...] = (
    "walk",
    "run",
    "cycle",
    "strength",
    "swim",
    "yoga",
    "other",
)

ACTIVITY_INTENSITIES: tuple[str, ...] = ("light", "moderate", "vigorous")

ACTIVITY_KIND_LABELS: dict[str, str] = {
    "walk": "走路",
    "run": "跑步",
    "cycle": "骑行",
    "strength": "力量",
    "swim": "游泳",
    "yoga": "瑜伽 / 拉伸",
    "other": "其它",
}

ACTIVITY_INTENSITY_LABELS: dict[str, str] = {
    "light": "轻",
    "moderate": "中等",
    "vigorous": "挺累",
}


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('walk', 'run', 'cycle', 'strength', 'swim', 'yoga', 'other')",
            name="ck_activity_kind",
        ),
        CheckConstraint(
            "intensity IN ('light', 'moderate', 'vigorous')",
            name="ck_activity_intensity",
        ),
        # 一次运动 1 分钟到 10 小时：再长基本是忘了停表。
        CheckConstraint(
            "minutes BETWEEN 1 AND 600",
            name="ck_activity_minutes",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="walk")
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    intensity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="moderate"
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SleepLog(Base):
    __tablename__ = "sleep_logs"
    __table_args__ = (
        CheckConstraint("woke_at > fell_asleep_at", name="ck_sleep_order"),
        # 一段睡眠不超过 24 小时：跨两夜的记录是两次，不是一次。
        CheckConstraint(
            "woke_at - fell_asleep_at <= interval '24 hours'",
            name="ck_sleep_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fell_asleep_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    woke_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
