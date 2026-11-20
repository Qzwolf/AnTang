"""日常问候的固定时刻

Revision ID: o4d5e6f7a8b9
Revises: n3c4d5e6f7a8
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o4d5e6f7a8b9"
down_revision: str | None = "n3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proactive_care_settings",
        sa.Column("check_in_hour", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_proactive_care_check_in_hour",
        "proactive_care_settings",
        "check_in_hour IS NULL OR (check_in_hour >= 0 AND check_in_hour <= 23)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_proactive_care_check_in_hour",
        "proactive_care_settings",
        type_="check",
    )
    op.drop_column("proactive_care_settings", "check_in_hour")
