"""主动关怀的打扰预算：连续没回应就降频、再停

Revision ID: z3e4f5a6b7c8
Revises: y2d3e4f5a6b7
Create Date: 2026-10-26 00:40:00

连续两次问候没人回应就把频率降到每三天，第三次直接暂停并记下
``auto_paused_at``——设置页要能说「是我们替你停的」，而不是用户
以为软件坏了。用户只要回一次，连续计数清零。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "z3e4f5a6b7c8"
down_revision: str | None = "y2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proactive_care_settings",
        sa.Column(
            "unanswered_contacts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "proactive_care_settings",
        sa.Column(
            "auto_downgraded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "proactive_care_settings",
        sa.Column(
            "auto_paused_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("proactive_care_settings", "auto_paused_at")
    op.drop_column("proactive_care_settings", "auto_downgraded_at")
    op.drop_column("proactive_care_settings", "unanswered_contacts")
