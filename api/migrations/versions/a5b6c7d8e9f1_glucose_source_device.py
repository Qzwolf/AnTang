"""血糖读数记下是哪台血糖仪测的

Revision ID: a5b6c7d8e9f1
Revises: z3e4f5a6b7c8
Create Date: 2026-10-25 22:10:00

``glucose_readings.source`` 只分「指尖血/传感器/化验单/手动」，没法回答
「这台机器和化验单对不上」。加 ``source_device`` 记品牌，空字符串表示
没选：旧记录不需要回填，也不加 CHECK——品牌会增补，加品牌不该要改表。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5b6c7d8e9f1"
down_revision: str | None = "z3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "glucose_readings",
        sa.Column(
            "source_device",
            sa.String(length=32),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("glucose_readings", "source_device")
