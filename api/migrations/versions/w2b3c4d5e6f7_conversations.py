"""conversations

Revision ID: w2b3c4d5e6f7
Revises: v1a2b3c4d5e6
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w2b3c4d5e6f7"
down_revision: str | None = "v1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="chat", nullable=False),
        sa.Column(
            "title", sa.String(length=80), server_default="新对话", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('chat', 'care')", name="ck_conversations_kind"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index(
        "ix_conversations_user_id_last_message_at",
        "conversations",
        ["user_id", "last_message_at"],
    )

    op.add_column("messages", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_messages_conversation_id",
        "messages",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_messages_conversation_id", "messages", ["conversation_id"]
    )

    op.add_column(
        "conversation_summaries",
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_summaries_conversation_id",
        "conversation_summaries",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_conversation_summaries_conversation_id",
        "conversation_summaries",
        ["conversation_id"],
    )

    # 历史数据：每个有过消息的用户收成一段「早期对话」，标题取第一条用户消息。
    op.execute(
        """
        WITH firsts AS (
            SELECT DISTINCT ON (user_id)
                user_id,
                content,
                created_at
            FROM messages
            WHERE role = 'user'
            ORDER BY user_id, id
        ),
        made AS (
            INSERT INTO conversations (user_id, kind, title, created_at, last_message_at)
            SELECT
                f.user_id,
                'chat',
                COALESCE(NULLIF(left(regexp_replace(f.content, '\\s+', ' ', 'g'), 24), ''), '早期对话'),
                f.created_at,
                (SELECT max(m.created_at) FROM messages m WHERE m.user_id = f.user_id)
            FROM firsts f
            RETURNING id, user_id
        )
        UPDATE messages m
        SET conversation_id = made.id
        FROM made
        WHERE m.user_id = made.user_id
        """
    )
    op.execute(
        """
        UPDATE conversation_summaries s
        SET conversation_id = c.id
        FROM conversations c
        WHERE s.user_id = c.user_id AND s.conversation_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_summaries_conversation_id",
        table_name="conversation_summaries",
    )
    op.drop_constraint(
        "fk_conversation_summaries_conversation_id",
        "conversation_summaries",
        type_="foreignkey",
    )
    op.drop_column("conversation_summaries", "conversation_id")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_constraint("fk_messages_conversation_id", "messages", type_="foreignkey")
    op.drop_column("messages", "conversation_id")
    op.drop_index(
        "ix_conversations_user_id_last_message_at", table_name="conversations"
    )
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
