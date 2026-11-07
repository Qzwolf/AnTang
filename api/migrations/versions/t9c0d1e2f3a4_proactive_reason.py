"""主动关怀的消息带一句「这次为什么是我先开口」。

Revision ID: t9c0d1e2f3a4
Revises: s8b9c0d1e2f3
Create Date: 2026-11-02

患者能看到的只有消息本身。主动关怀的消息和普通回答长得一样，读者无法判断
「这是他主动问的，还是我先说的」——于是多问一句「为什么」也没处问。这一列由程序写死
（见 `proactive_care/reasons.py`），不允许模型改。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t9c0d1e2f3a4"
down_revision: str | None = "s8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("proactive_reason", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "proactive_reason")
