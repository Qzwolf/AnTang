"""会话（conversation）：把消息分成一段一段的对话。

侧边栏要像常见 AI 助手那样按「一段一段」列出历史，就必须有这一层；
否则所有消息只是一条连续流，换会话也换不掉上下文。
主动关怀单独一段（kind='care'），不和患者自己开的对话混在一起。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base

CONVERSATION_KINDS: tuple[str, ...] = ("chat", "care")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_id_last_message_at", "user_id", "last_message_at"),
        CheckConstraint(
            "kind IN ('chat', 'care')",
            name="ck_conversations_kind",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    # 标题由第一条用户消息截断而来，不额外花一次模型调用。
    title: Mapped[str] = mapped_column(String(80), nullable=False, default="新对话")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
