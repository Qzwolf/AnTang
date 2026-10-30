"""血糖读数：手工录入与将来的报告解析共用一张表。"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
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
# lab = 化验单；report = 从报告解析出来；public_dataset / ohio_cgm = 研究用的公开
# 数据集（Ohio T1DM 等），不是患者自己测的。永远带着 source，免得拿别人/解析来的
# 数字去冒充患者自己的测量。
GLUCOSE_SOURCES: tuple[str, ...] = (
    "manual",
    "fingerstick",
    "cgm",
    "lab",
    "report",
    "public_dataset",
    "ohio_cgm",
)

# 测量时间的精度。exact = 有时刻（自己挑的、报告上印的）；
# daypart = 患者只说得出「早上 / 晚上 / 半夜」这种时段，存的是该时段的代表时刻，
# 界面上要写成「早上（大概）」，不能显示成一个精确到分钟的钟点。
GLUCOSE_PRECISIONS: tuple[str, ...] = ("exact", "daypart")

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
_PRECISIONS_SQL = ", ".join(f"'{value}'" for value in GLUCOSE_PRECISIONS)


class GlucoseReading(Base):
    __tablename__ = "glucose_readings"
    __table_args__ = (
        CheckConstraint("value_mg_dl BETWEEN 20 AND 600", name="ck_glucose_value_range"),
        CheckConstraint(f"context IN ({_CONTEXTS_SQL})", name="ck_glucose_context"),
        CheckConstraint(f"source IN ({_SOURCES_SQL})", name="ck_glucose_source"),
        CheckConstraint(
            f"measured_precision IN ({_PRECISIONS_SQL})",
            name="ck_glucose_measured_precision",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    value_mg_dl: Mapped[int] = mapped_column(Integer)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # 这条时间是精确的钟点，还是只说了「早上 / 晚上」：daypart 时界面上要写
    # 「早上（大概）」，别让一个编出来的 07:00 冒充精确数据。
    measured_precision: Mapped[str] = mapped_column(
        String(16), server_default=text("'exact'")
    )
    context: Mapped[str] = mapped_column(String(32), server_default=text("'other'"))
    source: Mapped[str] = mapped_column(String(16), server_default=text("'manual'"))
    # 哪台血糖仪测的。只对手指血有意义（化验单没有机器、传感器走 cgm_samples 的
    # source_system），空字符串表示没选。不加 CHECK：品牌会增补，别让加品牌要改表。
    source_device: Mapped[str] = mapped_column(String(32), server_default=text("''"))
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


class GlucoseReport(Base):
    """一份 CGM/化验报告的速览：区间、均值、变异、TIR，留在记录里可以回看。

    报告往往只有图形和汇总指标，没有逐点读数；把汇总指标硬拆成 glucose_readings
    会变成一堆编出来的时间点。所以单独存一条「这份报告说了什么」：患者和医生回看时
    看到的是原始指标，而不是我们重构出来的曲线。
    """

    __tablename__ = "glucose_reports"
    __table_args__ = (
        CheckConstraint(
            "period_end >= period_start", name="ck_glucose_reports_period_order"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    device: Mapped[str] = mapped_column(String(64), server_default=text("''"))
    # 同一款探头有不同序列号，随访时能对上「这份报告是哪一次戴的」。
    device_serial: Mapped[str] = mapped_column(String(32), server_default=text("''"))
    report_generated_at: Mapped[date | None] = mapped_column(Date, default=None)
    # 指标一律可空：报告里没写的那一项就空着，不猜、不补默认值。
    # 均值按两位小数存：报告上印的就是 5.81，只留一位会把它抹成 5.8。
    mean_mmol: Mapped[float | None] = mapped_column(Numeric(4, 2), default=None)
    sd_mmol: Mapped[float | None] = mapped_column(Numeric(4, 2), default=None)
    cv_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    ehba1c_percent: Mapped[float | None] = mapped_column(Numeric(4, 2), default=None)
    tir_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    tar_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    tbr_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    # 报告把 TIR 拆成五档（正常 / 低 / 很低 / 高 / 很高）。三档的 TAR/TBR 是合并值，
    # 合并后就对不上报告上的原数了，所以五档也分开存，显示时按报告自己的口径念。
    normal_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    low_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    very_low_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    high_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    very_high_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    # 报告上的「低血糖风险」档位（LBGI 预测）：high / moderate / low / lowest。
    lbgi_level: Mapped[str] = mapped_column(String(12), server_default=text("''"))
    # 这份速览是从哪张附件里读出来的：患者想回看原始报告时按它去取。
    # 附件被清理就置空，速览本身还在——指标是患者的数据，不该跟着文件一起消失。
    source_attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("message_attachments.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
