from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base
from antang_api.models.chat import enum_values


class PersonalProfileField(str, Enum):
    SEX = "sex"
    AGE_YEARS = "age_years"
    HEIGHT_CM = "height_cm"
    WEIGHT_KG = "weight_kg"
    RESIDENT_AREA = "resident_area"
    SCHEDULE_TYPE = "schedule_type"
    OCCUPATION = "occupation"


class HealthFactType(str, Enum):
    MEDICAL_HISTORY = "medical_history"
    ALLERGY = "allergy"
    SEVERE_HYPOGLYCEMIA_HISTORY = "severe_hypoglycemia_history"
    TREATMENT = "treatment"


class FactAssertion(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"


class FactTemporalStatus(str, Enum):
    CURRENT = "current"
    PAST = "past"
    UNKNOWN = "unknown"


class HealthFactStatus(str, Enum):
    ACTIVE = "active"
    RETRACTED = "retracted"


class ProfileChangeMode(str, Enum):
    DIRECT = "direct"
    CONFIRMATION = "confirmation"
    CLARIFICATION = "clarification"


class ProfileChangeStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"


class ProfileTargetType(str, Enum):
    PERSONAL_PROFILE = "personal_profile"
    HEALTH_FACT = "health_fact"


class ProfileOperation(str, Enum):
    SET = "set"
    CLEAR = "clear"
    ADD = "add"
    UPDATE = "update"
    RETRACT = "retract"


class PersonalProfile(Base):
    """用户明确表达的当前基础档案；设备观测不会覆盖这里的值。"""

    __tablename__ = "personal_profiles"
    __table_args__ = (
        CheckConstraint(
            "age_years IS NULL OR (age_years >= 0 AND age_years <= 150)",
            name="ck_personal_profiles_age_years",
        ),
        CheckConstraint(
            "height_cm IS NULL OR height_cm > 0",
            name="ck_personal_profiles_height_cm",
        ),
        CheckConstraint(
            "weight_kg IS NULL OR weight_kg > 0",
            name="ck_personal_profiles_weight_kg",
        ),
        CheckConstraint(
            "glucose_target_low_mg_dl > 0 AND glucose_target_low_mg_dl < glucose_target_high_mg_dl",
            name="ck_personal_profiles_glucose_target",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sex: Mapped[str | None] = mapped_column(String(32))
    age_years: Mapped[int | None] = mapped_column(Integer)
    age_as_of_date: Mapped[date | None] = mapped_column(Date)
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    resident_area: Mapped[str | None] = mapped_column(String(255))
    schedule_type: Mapped[str | None] = mapped_column(String(128))
    occupation: Mapped[str | None] = mapped_column(String(128))

    # 血糖目标范围：医生给患者定的往往不是 3.9–10.0（老年人放宽、孕妇收紧），
    # 所以它是患者自己的设置，不是代码里的常量。单位跟库里一致用 mg/dL。
    glucose_target_low_mg_dl: Mapped[int] = mapped_column(
        Integer, server_default="70"
    )
    glucose_target_high_mg_dl: Mapped[int] = mapped_column(
        Integer, server_default="180"
    )

    # revision 用于读取缓存；field_revisions 用于只让目标字段参与冲突检查。
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    field_revisions: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HealthFact(Base):
    """用户确认或明确表达的一条健康事实；撤回只改状态，不删除历史。"""

    __tablename__ = "health_facts"
    __table_args__ = (
        Index("ix_health_facts_user_status", "user_id", "status"),
        CheckConstraint(
            "effective_end IS NULL OR effective_start IS NULL "
            "OR effective_end >= effective_start",
            name="ck_health_facts_effective_dates",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    fact_type: Mapped[HealthFactType] = mapped_column(
        SqlEnum(
            HealthFactType,
            name="health_fact_type",
            values_callable=enum_values,
        )
    )
    statement: Mapped[str] = mapped_column(Text)
    assertion: Mapped[FactAssertion] = mapped_column(
        SqlEnum(
            FactAssertion,
            name="health_fact_assertion",
            values_callable=enum_values,
        )
    )
    temporal_status: Mapped[FactTemporalStatus] = mapped_column(
        SqlEnum(
            FactTemporalStatus,
            name="health_fact_temporal_status",
            values_callable=enum_values,
        )
    )
    effective_start: Mapped[date | None] = mapped_column(Date)
    effective_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[HealthFactStatus] = mapped_column(
        SqlEnum(
            HealthFactStatus,
            name="health_fact_status",
            values_callable=enum_values,
        ),
        default=HealthFactStatus.ACTIVE,
        server_default=HealthFactStatus.ACTIVE.value,
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HealthProfileChange(Base):
    """一项档案修改的候选、卡片状态和最终审计记录。"""

    __tablename__ = "health_profile_changes"
    __table_args__ = (
        CheckConstraint(
            "origin IN ('agent', 'user')",
            name="ck_health_profile_changes_origin",
        ),
        CheckConstraint(
            "(origin = 'agent' AND trigger_message_id IS NOT NULL "
            "AND agent_run_id IS NOT NULL AND proposal_index IS NOT NULL) OR "
            "(origin = 'user' AND trigger_message_id IS NULL "
            "AND agent_run_id IS NULL AND proposal_index IS NULL "
            "AND decision_client_action_id IS NOT NULL "
            "AND mode = 'direct' AND status = 'applied')",
            name="ck_health_profile_changes_provenance",
        ),
        UniqueConstraint(
            "agent_run_id",
            "proposal_index",
            name="uq_health_profile_changes_run_proposal",
        ),
        Index(
            "ix_health_profile_changes_user_status_created",
            "user_id",
            "status",
            "created_at",
        ),
        Index(
            "uq_health_profile_changes_user_client_action",
            "user_id",
            "decision_client_action_id",
            unique=True,
            postgresql_where=text("decision_client_action_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    trigger_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    proposal_index: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(
        String(16), default="agent", server_default="agent"
    )
    mode: Mapped[ProfileChangeMode] = mapped_column(
        SqlEnum(
            ProfileChangeMode,
            name="profile_change_mode",
            values_callable=enum_values,
        )
    )
    status: Mapped[ProfileChangeStatus] = mapped_column(
        SqlEnum(
            ProfileChangeStatus,
            name="profile_change_status",
            values_callable=enum_values,
        )
    )
    target_type: Mapped[ProfileTargetType] = mapped_column(
        SqlEnum(
            ProfileTargetType,
            name="profile_target_type",
            values_callable=enum_values,
        )
    )
    field_name: Mapped[str] = mapped_column(String(64))
    operation: Mapped[ProfileOperation] = mapped_column(
        SqlEnum(
            ProfileOperation,
            name="profile_operation",
            values_callable=enum_values,
        )
    )
    target_id: Mapped[UUID | None] = mapped_column(Uuid)
    before_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    proposed_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expected_revision: Mapped[int | None] = mapped_column(Integer)
    result_revision: Mapped[int | None] = mapped_column(Integer)

    # 这里只能保存应用程序固定模板生成的文案，不能接收模型任意文案。
    question: Mapped[str | None] = mapped_column(Text)
    clarification_reason: Mapped[str | None] = mapped_column(String(32))
    decision_client_action_id: Mapped[UUID | None] = mapped_column(Uuid)
    client_action_hash: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str | None] = mapped_column(String(16))
    answer_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
