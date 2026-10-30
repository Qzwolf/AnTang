"""cgm samples, import ledger and finer glucose sources

Revision ID: u08fa1b2c3d4
Revises: t97ec0913b26
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u08fa1b2c3d4"
down_revision: str | None = "t97ec0913b26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_SOURCES_SQL = "'manual', 'report'"
_NEW_SOURCES_SQL = "'manual', 'fingerstick', 'cgm', 'lab', 'report'"
_QUALITY_SQL = "'ok', 'gap', 'artifact'"


def upgrade() -> None:
    # 血糖来源从「手册/报告」两种，扩展为「手输/指尖血/CGM/化验单/报告」。
    # 旧值 manual 保留，历史数据不用迁移。
    op.drop_constraint("ck_glucose_source", "glucose_readings", type_="check")
    op.create_check_constraint(
        "ck_glucose_source", "glucose_readings", f"source IN ({_NEW_SOURCES_SQL})"
    )

    op.create_table(
        "glucose_imports",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(length=16), nullable=False),
        sa.Column("file_name", sa.String(length=200), nullable=False),
        sa.Column(
            "parsed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "inserted_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "skipped_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_glucose_imports_user_id", "glucose_imports", ["user_id"])

    op.create_table(
        "cgm_samples",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=True),
        sa.Column("source_system", sa.String(length=16), nullable=False),
        sa.Column(
            "device_id", sa.String(length=64), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_mg_dl", sa.Integer(), nullable=False),
        sa.Column("trend", sa.String(length=16), nullable=True),
        sa.Column(
            "quality", sa.String(length=16), server_default=sa.text("'ok'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "value_mg_dl BETWEEN 20 AND 600", name="ck_cgm_value_range"
        ),
        sa.CheckConstraint(f"quality IN ({_QUALITY_SQL})", name="ck_cgm_quality"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["glucose_imports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_system",
            "device_id",
            "recorded_at",
            name="uq_cgm_sample_source_time",
        ),
    )
    op.create_index("ix_cgm_samples_user_id", "cgm_samples", ["user_id"])
    op.create_index("ix_cgm_samples_recorded_at", "cgm_samples", ["recorded_at"])
    op.create_index("ix_cgm_samples_import_id", "cgm_samples", ["import_id"])


def downgrade() -> None:
    op.drop_index("ix_cgm_samples_import_id", table_name="cgm_samples")
    op.drop_index("ix_cgm_samples_recorded_at", table_name="cgm_samples")
    op.drop_index("ix_cgm_samples_user_id", table_name="cgm_samples")
    op.drop_table("cgm_samples")
    op.drop_index("ix_glucose_imports_user_id", table_name="glucose_imports")
    op.drop_table("glucose_imports")
    op.drop_constraint("ck_glucose_source", "glucose_readings", type_="check")
    op.create_check_constraint(
        "ck_glucose_source", "glucose_readings", f"source IN ({_OLD_SOURCES_SQL})"
    )
