"""给血糖读数加上「时间精度」这一列。

患者说「我早上血糖 7.2」，能记的只有「早上」这个时段；如果只存一个 07:00，
记录页和趋势图会把编出来的钟点当成精确数据展示。这一列让「大概」成为可查的事实。

Revision ID: g1b2c3d4e5f6
Revises: f1a2b3c4d5e6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g1b2c3d4e5f6"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "glucose_readings",
        sa.Column(
            "measured_precision",
            sa.String(length=16),
            nullable=False,
            server_default="exact",
        ),
    )
    op.create_check_constraint(
        "ck_glucose_measured_precision",
        "glucose_readings",
        "measured_precision IN ('exact', 'daypart')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_glucose_measured_precision", "glucose_readings", type_="check"
    )
    op.drop_column("glucose_readings", "measured_precision")
