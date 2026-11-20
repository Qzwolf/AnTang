"""心理评估加一列 WHO-5 百分比

Revision ID: p5e6f7a8b9c0
Revises: o4d5e6f7a8b9
Create Date: 2026-11-01

WHO-5（世界卫生组织五项身心健康指数）可以免费使用，量的是「最近两周整体状态」，
和低血糖恐惧（行为/担忧两个分量表）不是同一个维度，所以单独存一列，不并进旧分。
可空：老记录和没答 WHO-5 的那次评估都不该被迫填 0。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p5e6f7a8b9c0"
down_revision: str | None = "o4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "psychosocial_assessments",
        sa.Column("who5_percent", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_psychosocial_who5",
        "psychosocial_assessments",
        "who5_percent IS NULL OR (who5_percent BETWEEN 0 AND 100)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_psychosocial_who5",
        "psychosocial_assessments",
        type_="check",
    )
    op.drop_column("psychosocial_assessments", "who5_percent")
