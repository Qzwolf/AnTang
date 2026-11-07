"""行为痕迹校准期 + 「本来会发」的记录表

Revision ID: x4a5b6c7d8e9
Revises: w3f4a5b6c7d8
Create Date: 2026-11-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "x4a5b6c7d8e9"
down_revision = "w3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proactive_care_settings",
        sa.Column("behavior_calibration_until", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "behavior_signal_records",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suppressed_reason", sa.String(length=32)),
        sa.Column("evidence", postgresql.JSONB()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "suppressed_reason IS NULL OR suppressed_reason IN "
            "('calibration', 'already_queued', 'cooling_down', 'expired_window')",
            name="ck_behavior_signal_records_reason",
        ),
    )
    op.create_index(
        "ix_behavior_signal_records_user_detected",
        "behavior_signal_records",
        ["user_id", "detected_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_behavior_signal_records_user_detected",
        table_name="behavior_signal_records",
    )
    op.drop_table("behavior_signal_records")
    op.drop_column("proactive_care_settings", "behavior_calibration_until")
