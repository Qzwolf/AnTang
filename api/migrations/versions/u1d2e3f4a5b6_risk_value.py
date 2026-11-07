"""给双风险标注单独存一份血糖数字

原来只存了指向 `glucose_readings` 的外键。读数被撤回或删掉后，这条对照的另一半
——「当时血糖 3.2」——就跟着没了，患者回看时只剩「血糖低」这种没头没尾的话。

Revision ID: u1d2e3f4a5b6
Revises: t9c0d1e2f3a4
Create Date: 2026-11-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u1d2e3f4a5b6"
down_revision = "t9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_annotations",
        sa.Column("value_mg_dl", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_risk_annotations_value",
        "risk_annotations",
        "value_mg_dl IS NULL OR (value_mg_dl >= 20 AND value_mg_dl <= 600)",
    )
    # 老数据从关联读数补回来；读数已经没了的就留空，界面按「当时没记数值」显示。
    op.execute(
        """
        UPDATE risk_annotations AS annotation
        SET value_mg_dl = reading.value_mg_dl
        FROM glucose_readings AS reading
        WHERE annotation.glucose_reading_id = reading.id
          AND annotation.value_mg_dl IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_risk_annotations_value", "risk_annotations", type_="check"
    )
    op.drop_column("risk_annotations", "value_mg_dl")
