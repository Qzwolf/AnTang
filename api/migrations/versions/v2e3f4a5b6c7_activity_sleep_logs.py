"""运动与睡眠的手工补录

手环导入是主路径，但没戴表的人（尤其怕低血糖不敢动的人）两页会永远是空的。
两张表只存事实：做了什么做多久、几点睡几点醒。

Revision ID: v2e3f4a5b6c7
Revises: u1d2e3f4a5b6
Create Date: 2026-11-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v2e3f4a5b6c7"
down_revision = "u1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_logs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("intensity", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('walk', 'run', 'cycle', 'strength', 'swim', 'yoga', 'other')",
            name="ck_activity_kind",
        ),
        sa.CheckConstraint(
            "intensity IN ('light', 'moderate', 'vigorous')",
            name="ck_activity_intensity",
        ),
        sa.CheckConstraint("minutes BETWEEN 1 AND 600", name="ck_activity_minutes"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_activity_logs_user_id", "activity_logs", ["user_id"], unique=False
    )
    op.create_index(
        "ix_activity_logs_performed_at",
        "activity_logs",
        ["performed_at"],
        unique=False,
    )

    op.create_table(
        "sleep_logs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("fell_asleep_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("woke_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("woke_at > fell_asleep_at", name="ck_sleep_order"),
        sa.CheckConstraint(
            "woke_at - fell_asleep_at <= interval '24 hours'",
            name="ck_sleep_length",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sleep_logs_user_id", "sleep_logs", ["user_id"], unique=False)
    op.create_index(
        "ix_sleep_logs_fell_asleep_at",
        "sleep_logs",
        ["fell_asleep_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sleep_logs_fell_asleep_at", table_name="sleep_logs")
    op.drop_index("ix_sleep_logs_user_id", table_name="sleep_logs")
    op.drop_table("sleep_logs")
    op.drop_index("ix_activity_logs_performed_at", table_name="activity_logs")
    op.drop_index("ix_activity_logs_user_id", table_name="activity_logs")
    op.drop_table("activity_logs")
