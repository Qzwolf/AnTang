"""fear_behavior_logs 增加 deleted_at：患者删掉后医护端仍可追溯

Revision ID: y2d3e4f5a6b7
Revises: x1c2d3e4f5a6
Create Date: 2026-10-25 23:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y2d3e4f5a6b7"
down_revision: str | None = "x1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 患者在「心理 → 行为与念头」里删一条，希望它从自己的界面上消失；
    # 但医护端已经同步到的内容不能跟着消失，否则「自行调药」这类
    # 需要复诊核对的事就成了患者单方面可抹去的记录。
    # 所以患者侧改成软删除：列表与统计只看 deleted_at 为空的行，
    # 医护端查询带上已删除的行并标注「患者已删除」。
    op.add_column(
        "fear_behavior_logs",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_fear_behavior_logs_deleted_at",
        "fear_behavior_logs",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fear_behavior_logs_deleted_at", table_name="fear_behavior_logs")
    op.drop_column("fear_behavior_logs", "deleted_at")
