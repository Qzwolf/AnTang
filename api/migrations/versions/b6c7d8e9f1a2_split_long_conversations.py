"""把一段长对话按消息间隔重新切成多段

Revision ID: b6c7d8e9f1a2
Revises: a5b6c7d8e9f1
Create Date: 2026-10-26 20:40:00

``w2b3c4d5e6f7`` 加会话表时，回填把每个患者的**全部**历史消息塞进了同一个
「早期对话」，侧边栏因此只看得见一段（线上 akaa 的 234 条消息就全在
「帮我看看我的心率」底下，点进去也只有这一次）。这里按相邻消息的间隔重切：
相差超过 6 小时就当作另一次对话，标题取那一段的第一条用户消息。

原有的 conversation id 留给了最早那一段，其它段新开；之后再跑一遍不会再有
超过 6 小时的空隙，所以这个迁移是幂等的。
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9f1a2"
down_revision: str | None = "a5b6c7d8e9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SESSION_GAP_SECONDS = 6 * 3600
TITLE_CHARS = 24
FALLBACK_TITLE = "新对话"

_messages = sa.table(
    "messages",
    sa.column("id", sa.Uuid),
    sa.column("conversation_id", sa.Uuid),
    sa.column("role", sa.String),
    sa.column("content", sa.Text),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

_conversations = sa.table(
    "conversations",
    sa.column("id", sa.Uuid),
    sa.column("user_id", sa.Uuid),
    sa.column("kind", sa.String),
    sa.column("title", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("last_message_at", sa.DateTime(timezone=True)),
)


def _segment_title(segment: Sequence[sa.Row]) -> str:
    for row in segment:
        if row.role == "user":
            text = " ".join(row.content.split())
            if text:
                return text[:TITLE_CHARS]
    return FALLBACK_TITLE


def upgrade() -> None:
    bind = op.get_bind()
    conversation_ids = (
        bind.execute(
            sa.select(_conversations.c.id)
            .where(_conversations.c.kind == "chat")
            .order_by(_conversations.c.created_at, _conversations.c.id)
        )
        .scalars()
        .all()
    )

    for conversation_id in conversation_ids:
        rows = bind.execute(
            sa.select(
                _messages.c.id,
                _messages.c.role,
                _messages.c.content,
                _messages.c.created_at,
            )
            .where(_messages.c.conversation_id == conversation_id)
            .order_by(_messages.c.created_at, _messages.c.id)
        ).all()
        if len(rows) < 2:
            continue

        segments: list[list[sa.Row]] = [[rows[0]]]
        for row in rows[1:]:
            previous = segments[-1][-1]
            gap = (row.created_at - previous.created_at).total_seconds()
            if gap > SESSION_GAP_SECONDS:
                segments.append([row])
            else:
                segments[-1].append(row)
        if len(segments) < 2:
            continue

        user_id = bind.execute(
            sa.select(_conversations.c.user_id).where(
                _conversations.c.id == conversation_id
            )
        ).scalar_one()

        for segment in segments[1:]:
            new_id = uuid4()
            bind.execute(
                sa.insert(_conversations).values(
                    id=new_id,
                    user_id=user_id,
                    kind="chat",
                    title=_segment_title(segment),
                    created_at=segment[0].created_at,
                    last_message_at=segment[-1].created_at,
                )
            )
            bind.execute(
                sa.update(_messages)
                .where(_messages.c.id.in_([row.id for row in segment]))
                .values(conversation_id=new_id)
            )

        first = segments[0]
        bind.execute(
            sa.update(_conversations)
            .where(_conversations.c.id == conversation_id)
            .values(
                title=_segment_title(first),
                created_at=first[0].created_at,
                last_message_at=first[-1].created_at,
            )
        )


def downgrade() -> None:
    """并回最早那一段：切分本身没有原始信息可还原，只能保证消息一条不丢。"""

    bind = op.get_bind()
    users = (
        bind.execute(
            sa.select(_conversations.c.user_id, sa.func.min(_conversations.c.created_at))
            .where(_conversations.c.kind == "chat")
            .group_by(_conversations.c.user_id)
        )
        .all()
    )
    for user_id, first_created_at in users:
        head_id = bind.execute(
            sa.select(_conversations.c.id)
            .where(
                _conversations.c.user_id == user_id,
                _conversations.c.kind == "chat",
                _conversations.c.created_at == first_created_at,
            )
            .order_by(_conversations.c.id)
            .limit(1)
        ).scalar_one()
        bind.execute(
            sa.update(_messages)
            .where(_messages.c.conversation_id.in_(
                sa.select(_conversations.c.id).where(
                    _conversations.c.user_id == user_id,
                    _conversations.c.kind == "chat",
                )
            ))
            .values(conversation_id=head_id)
        )
        bind.execute(
            sa.delete(_conversations).where(
                _conversations.c.user_id == user_id,
                _conversations.c.kind == "chat",
                _conversations.c.id != head_id,
            )
        )
