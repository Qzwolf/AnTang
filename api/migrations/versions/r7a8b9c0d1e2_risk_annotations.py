"""同一时刻的生理风险与感受风险

Revision ID: r7a8b9c0d1e2
Revises: q6f7a8b9c0d1
Create Date: 2026-11-02

把「血糖危险」和「他感觉到了吗」分开存：最要紧的一档是血糖已经很低、他却
毫无感觉，压成一个风险等级就看不出来了。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r7a8b9c0d1e2"
down_revision: str | None = "q6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_annotations",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("glucose_reading_id", sa.Uuid(), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("physiological_risk", sa.String(length=16), nullable=False),
        sa.Column("felt_risk", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["glucose_reading_id"], ["glucose_readings.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "physiological_risk IN ('none', 'moderate', 'high')",
            name="ck_risk_annotations_physiological",
        ),
        sa.CheckConstraint(
            "felt_risk IN ('none', 'some', 'strong')",
            name="ck_risk_annotations_felt",
        ),
        sa.CheckConstraint(
            "label IN ('both', 'silent', 'felt_only', 'neither')",
            name="ck_risk_annotations_label",
        ),
    )
    op.create_index(
        "ix_risk_annotations_user_id", "risk_annotations", ["user_id"]
    )
    op.create_index(
        "ix_risk_annotations_measured_at", "risk_annotations", ["measured_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_risk_annotations_measured_at", table_name="risk_annotations")
    op.drop_index("ix_risk_annotations_user_id", table_name="risk_annotations")
    op.drop_table("risk_annotations")
