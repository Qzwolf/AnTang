"""加一张血糖报告速览表

Revision ID: c7d8e9f1a2b3
Revises: b6c7d8e9f1a2
Create Date: 2026-10-27 01:10:00

患者把 CGM 报告发进对话时，模型能读、也能说，但读出来的均值/TIR 没有地方存，
记录页因此永远是空的。报告通常只有图形和汇总指标、没有逐点读数，硬拆成
glucose_readings 会变成一堆编出来的时间点，所以单独存一条「这份报告说了什么」。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f1a2b3"
down_revision: str | None = "b6c7d8e9f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "glucose_reports",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("device", sa.String(length=64), server_default="", nullable=False),
        sa.Column("mean_mmol", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("sd_mmol", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column("cv_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("ehba1c_percent", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column("tir_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("tar_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("tbr_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "period_end >= period_start", name="ck_glucose_reports_period_order"
        ),
    )
    op.create_index(
        "ix_glucose_reports_user_id", "glucose_reports", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_glucose_reports_user_id", table_name="glucose_reports")
    op.drop_table("glucose_reports")
