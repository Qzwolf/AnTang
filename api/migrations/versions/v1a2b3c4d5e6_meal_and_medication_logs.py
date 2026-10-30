"""meal and medication logs

Revision ID: v1a2b3c4d5e6
Revises: u08fa1b2c3d4
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v1a2b3c4d5e6"
down_revision: str | None = "u08fa1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 饮食记录：碳水是估计值，允许为空；只记事实，不做营养计算。
    op.create_table(
        "meal_logs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meal_type", sa.String(length=16), nullable=False),
        sa.Column(
            "description", sa.String(length=200), server_default="", nullable=False
        ),
        sa.Column("carbs_grams", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')",
            name="ck_meal_type",
        ),
        sa.CheckConstraint(
            "carbs_grams IS NULL OR carbs_grams BETWEEN 0 AND 400",
            name="ck_meal_carbs_range",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_logs_user_id", "meal_logs", ["user_id"])

    # 用药记录：只存患者输入的原文（药名 + 剂量文字），系统不计算剂量、不给建议。
    op.create_table(
        "medication_logs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("dose_text", sa.String(length=64), server_default="", nullable=False),
        sa.Column(
            "kind", sa.String(length=16), server_default="other", nullable=False
        ),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('insulin', 'oral', 'other')", name="ck_medication_kind"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_medication_logs_user_id", "medication_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_medication_logs_user_id", table_name="medication_logs")
    op.drop_table("medication_logs")
    op.drop_index("ix_meal_logs_user_id", table_name="meal_logs")
    op.drop_table("meal_logs")
