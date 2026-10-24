"""血糖读数：手工录入与将来的报告解析共用一张表。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base

GLUCOSE_CONTEXTS: tuple[str, ...] = (
    "fasting",
    "pre_meal",
    "post_meal",
    "bedtime",
    "night",
    "other",
)

# manual = 患者自己填（旧数据保留这个值）；fingerstick = 指尖血；cgm = CGM 点值；
# lab = 化验单；report = 从报告解析出来。永远带着 source，免得拿解析结果去冒充手工测量。
GLUCOSE_SOURCES: tuple[str, ...] = ("manual", "fingerstick", "cgm", "lab", "report")

# 数据来自哪个系统。手工录入留空；导入时必填，方便回溯"这份数据是从哪来的"。
GLUCOSE_SOURCE_SYSTEMS: tuple[str, ...] = (
    "sibionics",
    "nightscout",
    "libre",
    "dexcom",
    "generic",
)

_CGM_QUALITIES_SQL = "'ok', 'gap', 'artifact'"

# 撤回只用于点测：CGM 是连续序列，撤回一条没有意义，导入错误整批删。
# 唯一键让同一份导出文件重复导入不会重复入库。
_CGM_UNIQUE = "uq_cgm_sample_source_time"


class CgmSample(Base):
    __tablename__ = "cgm_samples"
    __table_args__ = (
        CheckConstraint("value_mg_dl BETWEEN 20 AND 600", name="ck_cgm_value_range"),
        CheckConstraint(f"quality IN ({_CGM_QUALITIES_SQL})", name="ck_cgm_quality"),
        UniqueConstraint(
            "user_id", "source_system", "device_id", "recorded_at", name=_CGM_UNIQUE
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    import_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("glucose_imports.id", ondelete="CASCADE"), index=True
    )
    source_system: Mapped[str] = mapped_column(String(16))
    device_id: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value_mg_dl: Mapped[int] = mapped_column(Integer)
    trend: Mapped[str | None] = mapped_column(String(16), default=None)
    quality: Mapped[str] = mapped_column(String(16), server_default=text("'ok'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GlucoseImport(Base):
    """一次导入的账本：谁导的、从哪个系统、多少行进来、多少行被跳过。"""

    __tablename__ = "glucose_imports"

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_system: Mapped[str] = mapped_column(String(16))
    file_name: Mapped[str] = mapped_column(String(200))
    parsed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    inserted_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

_CONTEXTS_SQL = ", ".join(f"'{value}'" for value in GLUCOSE_CONTEXTS)
_SOURCES_SQL = ", ".join(f"'{value}'" for value in GLUCOSE_SOURCES)


class GlucoseReading(Base):
    __tablename__ = "glucose_readings"
    __table_args__ = (
        CheckConstraint("value_mg_dl BETWEEN 20 AND 600", name="ck_glucose_value_range"),
        CheckConstraint(f"context IN ({_CONTEXTS_SQL})", name="ck_glucose_context"),
        CheckConstraint(f"source IN ({_SOURCES_SQL})", name="ck_glucose_source"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    value_mg_dl: Mapped[int] = mapped_column(Integer)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    context: Mapped[str] = mapped_column(String(32), server_default=text("'other'"))
    source: Mapped[str] = mapped_column(String(16), server_default=text("'manual'"))
    note: Mapped[str | None] = mapped_column(String(200), default=None)
    # 撤回只打标记、不删行：数据不再被读取或送进模型，但"记录过又撤回"这件事
    # 本身对低血糖恐惧是有信息的（回避行为），所以留一个时间戳。
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # 复盘只打一次标记：同一次低血糖问第二遍比不问更伤人，
    # 而这个时间戳也让"问过没有"成为可查的事实，不靠记忆。
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
