from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base


class ConversationSummary(Base):
    """这张表用来保存一次已经完成的对话摘要。"""

    __tablename__ = "conversation_summaries"
    __table_args__ = (
        Index(
            "ix_conversation_summaries_user_id_id",
            "user_id",
            "id",
        ),
        Index(
            "uq_conversation_summaries_user_through_message_id",
            "user_id",
            "through_message_id",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )

    # 摘要属于哪一段对话；不同会话的摘要不能互相沿用。
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )

    # 这份摘要已经覆盖到哪条原始消息。
    through_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
    )

    # 本次摘要基于哪一份旧摘要继续生成。
    source_summary_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversation_summaries.id", ondelete="SET NULL"),
    )

    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
