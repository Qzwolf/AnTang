from datetime import datetime, time
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base
from antang_api.models.chat import enum_values


class ProactiveCareTaskKind(str, Enum):
    HEALTH_EVENT = "health_event"
    ROUTINE_CHECK_IN = "routine_check_in"
    PLAN_FOLLOW_UP = "plan_follow_up"


class ProactiveCareTaskStatus(str, Enum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RoutineCareCadence(str, Enum):
    DISABLED = "disabled"
    DAILY = "daily"
    EVERY_3_DAYS = "every_3_days"
    WEEKLY = "weekly"


class CarePlanStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PushPlatform(str, Enum):
    ANDROID = "android"


class PushPermissionState(str, Enum):
    UNDETERMINED = "undetermined"
    DENIED = "denied"
    GRANTED = "granted"


class PushDeliveryStatus(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    TICKET_ACCEPTED = "ticket_accepted"
    RECEIPT_ACCEPTED = "receipt_accepted"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    EXPIRED = "expired"


class ProactiveCareSettings(Base):
    """用户对服务器主动发起关怀的明确授权与时间偏好。"""

    __tablename__ = "proactive_care_settings"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    routine_cadence: Mapped[RoutineCareCadence] = mapped_column(
        SqlEnum(
            RoutineCareCadence,
            name="routine_care_cadence",
            values_callable=enum_values,
        ),
        default=RoutineCareCadence.DISABLED,
        server_default=RoutineCareCadence.DISABLED.value,
    )
    plan_follow_up_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
    )
    health_events_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="Asia/Shanghai",
        server_default="Asia/Shanghai",
    )
    quiet_hours_start: Mapped[time] = mapped_column(
        Time,
        default=time(22, 0),
        server_default="22:00:00",
    )
    quiet_hours_end: Mapped[time] = mapped_column(
        Time,
        default=time(8, 0),
        server_default="08:00:00",
    )
    health_notification_preview_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    # 连续没被回应的日常问候次数：达到 2 次自动降到每 3 天，达到 3 次自动暂停。
    # 只统计「问候」这一种，回访计划与健康事件有自己的授权，不参与降频。
    unanswered_contacts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    # 患者自己定的「一周最多主动联系几次」。空 = 跟着心理社会评估的四象限剂量走。
    # 只让调 1–7：0 次等于关掉日常问候，那是 routine_cadence 的开关，不该藏在次数里。
    weekly_contact_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    auto_downgraded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # 自动暂停与用户主动关掉要分开记：设置页要能说清「是我们替你停的」。
    auto_paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "weekly_contact_limit IS NULL OR "
            "(weekly_contact_limit >= 1 AND weekly_contact_limit <= 7)",
            name="ck_proactive_care_weekly_limit",
        ),
    )


class CarePlan(Base):
    """用户明确同意以后由系统回访的一项计划。"""

    __tablename__ = "care_plans"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "last_changed_by_message_id",
            name="uq_care_plans_user_last_changed_message",
        ),
        Index(
            "ix_care_plans_user_status_follow_up",
            "user_id",
            "status",
            "follow_up_at",
        ),
        CheckConstraint("revision >= 1", name="ck_care_plans_revision"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[CarePlanStatus] = mapped_column(
        SqlEnum(CarePlanStatus, name="care_plan_status", values_callable=enum_values)
    )
    follow_up_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer)
    created_by_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"),
    )
    last_changed_by_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProactiveCareTask(Base):
    """一次已经进入 PostgreSQL、可由后台 Worker 恢复的关怀任务。"""

    __tablename__ = "proactive_care_tasks"
    __table_args__ = (
        Index("ix_proactive_care_tasks_status_due_at", "status", "due_at"),
        Index(
            "ix_proactive_care_tasks_running_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "uq_proactive_care_tasks_running_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "uq_proactive_care_tasks_scheduled_routine_user",
            "user_id",
            unique=True,
            postgresql_where=text(
                "kind = 'routine_check_in' AND status = 'scheduled'"
            ),
        ),
        UniqueConstraint(
            "wearable_import_id",
            name="uq_proactive_care_tasks_wearable_import",
        ),
        UniqueConstraint(
            "care_plan_id",
            "care_plan_revision",
            name="uq_proactive_care_tasks_plan_revision",
        ),
        UniqueConstraint(
            "response_message_id",
            name="uq_proactive_care_tasks_response_message",
        ),
        CheckConstraint(
            "expires_at > due_at",
            name="ck_proactive_care_tasks_expiry",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_proactive_care_tasks_attempt_count",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_proactive_care_tasks_lease",
        ),
        CheckConstraint(
            "(kind = 'health_event' AND wearable_import_id IS NOT NULL "
            "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
            "(kind = 'routine_check_in' AND wearable_import_id IS NULL "
            "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
            "(kind = 'plan_follow_up' AND wearable_import_id IS NULL "
            "AND care_plan_id IS NOT NULL AND care_plan_revision IS NOT NULL)",
            name="ck_proactive_care_tasks_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[ProactiveCareTaskKind] = mapped_column(
        SqlEnum(
            ProactiveCareTaskKind,
            name="proactive_care_task_kind",
            values_callable=enum_values,
        )
    )
    status: Mapped[ProactiveCareTaskStatus] = mapped_column(
        SqlEnum(
            ProactiveCareTaskStatus,
            name="proactive_care_task_status",
            values_callable=enum_values,
        )
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    wearable_import_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("wearable_imports.id", ondelete="RESTRICT")
    )
    care_plan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("care_plans.id", ondelete="CASCADE")
    )
    care_plan_revision: Mapped[int | None] = mapped_column(Integer)
    health_event_key: Mapped[str | None] = mapped_column(String(255))
    health_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rule_id: Mapped[str | None] = mapped_column(String(128))
    rule_version: Mapped[str | None] = mapped_column(String(64))
    source_version_hash: Mapped[str | None] = mapped_column(String(64))
    duplicate_of_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("proactive_care_tasks.id", ondelete="SET NULL")
    )
    response_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    # Worker 领取任务时看到的最后一条聊天消息。发送前再次比较，用户在
    # Agent 起草期间发过新消息时，旧草稿不会进入聊天。
    claimed_through_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    outcome_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushInstallation(Base):
    """一份 App 安装与当前登录会话之间的推送绑定。"""

    __tablename__ = "push_installations"
    __table_args__ = (
        CheckConstraint(
            "registration_revision >= 1",
            name="ck_push_installations_registration_revision",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    login_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("login_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    expo_push_token: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
    )
    registration_revision: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
    )
    permission: Mapped[PushPermissionState] = mapped_column(
        SqlEnum(
            PushPermissionState,
            name="push_permission_state",
            values_callable=enum_values,
        )
    )
    platform: Mapped[PushPlatform] = mapped_column(
        SqlEnum(PushPlatform, name="push_platform", values_callable=enum_values)
    )
    app_version: Mapped[str] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class PushDelivery(Base):
    """一条已保存聊天消息向一份 App 安装发送的可恢复过程。"""

    __tablename__ = "push_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "installation_id",
            name="uq_push_deliveries_message_installation",
        ),
        Index(
            "ix_push_deliveries_status_next_attempt",
            "status",
            "next_attempt_at",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_push_deliveries_attempt_count",
        ),
        CheckConstraint(
            "receipt_attempt_count >= 0",
            name="ck_push_deliveries_receipt_attempt_count",
        ),
        CheckConstraint(
            "installation_revision >= 1",
            name="ck_push_deliveries_installation_revision",
        ),
        CheckConstraint(
            "(status = 'sending' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'sending' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_push_deliveries_lease",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
    )
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("push_installations.id", ondelete="CASCADE"),
        index=True,
    )
    installation_revision: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
    )
    show_message_preview: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[PushDeliveryStatus] = mapped_column(
        SqlEnum(
            PushDeliveryStatus,
            name="push_delivery_status",
            values_callable=enum_values,
        )
    )
    provider_ticket_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    receipt_attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    receipt_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_error_code: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
