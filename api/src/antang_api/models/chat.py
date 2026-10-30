from datetime import datetime
from enum import Enum
from uuid import UUID
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from antang_api.database import Base


class MessageRole(str, Enum):
    """str枚举：聊天消息的发送方"""

    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(str, Enum):
    """str枚举：聊天消息的状态"""

    GENERATING = "generating"  # 正在生成中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 生成失败
    CANCELLED = "cancelled"  # 已取消


class MessageAttachmentKind(str, Enum):
    """用户发送到聊天中的附件类型。"""

    PHOTO = "photo"
    REPORT = "report"


class AgentRunStatus(str, Enum):
    """str枚举：一次 Agent 工作从开始到结束的状态"""

    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def enum_values(enum_class: type[Enum]) -> list[str]:
    """让数据库保存枚举值，比如 user、assistant，而不是枚举的名字，比如 USER、ASSISTANT"""
    return [e.value for e in enum_class]


class Message(Base):
    """message表记录了用户和AI之间的聊天消息。"""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_user_id_id", "user_id", "id"),
        Index(
            "uq_messages_user_client_message_id",
            "user_id",
            "client_message_id",
            unique=True,
        ),
    )  # 建立联合索引，user_id和id的，还有user_id和client_message_id的，后者是为了保证同一个用户的client_message_id唯一

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )  # UUIDv7，按时间排序的UUID，便于查询最近的消息，作为主键对B+树索引比较友好

    # 手机在发送前生成的消息 ID，用于识别网络重试造成的重复请求。
    # AI 消息由后端生成，因此该字段为空。
    client_message_id: Mapped[UUID | None] = mapped_column(Uuid)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )  # 用户表里的数据被删了这里也一起删

    role: Mapped[MessageRole] = mapped_column(
        SqlEnum(MessageRole, name="message_role", values_callable=enum_values)
    )

    status: Mapped[MessageStatus] = mapped_column(
        SqlEnum(
            MessageStatus,
            name="message_status",
            values_callable=enum_values,
        )
    )

    # 属于哪一段对话。历史数据在迁移里归到每个人最早的那一段。
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )

    content: Mapped[str] = mapped_column(Text)

    # 最终回答实际引用的网页快照。详细工具结果仍保存在 agent_tool_calls。
    sources: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # 用户消息不需要完成时间，因此可以为None，AI消息刚创建时也没有completed_at
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )


class MessageAttachment(Base):
    """保存用户上传的原始图片或 PDF，并在发送时绑定一条消息。"""

    __tablename__ = "message_attachments"
    __table_args__ = (
        Index("ix_message_attachments_user_id_id", "user_id", "id"),
        CheckConstraint("size_bytes > 0", name="ck_message_attachments_positive_size"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        unique=True,
    )
    kind: Mapped[MessageAttachmentKind] = mapped_column(
        SqlEnum(
            MessageAttachmentKind,
            name="message_attachment_kind",
            values_callable=enum_values,
        )
    )
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AgentRun(Base):
    """记录某个 Agent 为用户消息或服务器关怀任务执行的一次工作。

    一条用户消息以后可能对应 Core Agent、Auditor 和多个 Sub-agent，
    每个 Agent 都会拥有自己的运行记录。
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_user_id_id", "user_id", "id"),
        Index(
            "uq_agent_runs_parent_agent_name",
            "parent_run_id",
            "agent_name",
            unique=True,
            postgresql_where=text("parent_run_id IS NOT NULL"),
        ),
        Index(
            "uq_agent_runs_active_root_user",
            "user_id",
            unique=True,
            postgresql_where=text(
                "parent_run_id IS NULL "
                "AND agent_name = 'core_agent' "
                "AND status IN ('running', 'waiting_for_user')"
            ),
        ),
        Index(
            "uq_agent_runs_active_care_task_agent",
            "trigger_care_task_id",
            "agent_name",
            unique=True,
            postgresql_where=text(
                "trigger_care_task_id IS NOT NULL "
                "AND status IN ('running', 'waiting_for_user')"
            ),
        ),
        CheckConstraint(
            "num_nonnulls(trigger_message_id, trigger_care_task_id) = 1",
            name="ck_agent_runs_exactly_one_trigger",
        ),
    )
    # 这次运行的唯一 ID
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    # 哪个用户触发的
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )

    # Core 或现有子 Agent 由真实用户消息触发。
    trigger_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
    )

    # 主动关怀及其 Auditor 由服务器中的持久关怀任务触发。
    trigger_care_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("proactive_care_tasks.id", ondelete="CASCADE"),
        index=True,
    )

    # 运行结束后，生成了哪条消息
    result_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        unique=True,
    )

    # 调用了这个 AgentRun 的父 AgentRun 的ID，如果没有父AgentRun则为None，比如Core Agent调用了Memory Sub-agent
    parent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
    )

    # 创建子 AgentRun 的那次父工具调用。它与 parent_run_id 一起让委派可审计、可去重。
    parent_tool_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "agent_tool_calls.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_agent_runs_parent_tool_call_id_agent_tool_calls",
        ),
        index=True,
    )

    # 被调用的Agent名称
    agent_name: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))

    # 当前状态（running / completed / failed / cancelled）
    status: Mapped[AgentRunStatus] = mapped_column(
        SqlEnum(
            AgentRunStatus,
            name="agent_run_status",
            values_callable=enum_values,
        )
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
