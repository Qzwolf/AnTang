"""给报告速览补上五档区间、低血糖风险与设备序列号

Revision ID: d8e9f1a2b3c4
Revises: c7d8e9f1a2b3
Create Date: 2026-10-27 03:20:00

真实报告（天津医科大学总医院《动态葡萄糖评估报告》，CGM.zip 里 20 份全同一模板）
除了均值/SD/CV/eHbA1c，还会给：五档区间占比（正常 / 低 / 很低 / 高 / 很高）、
低血糖风险档位（LBGI 高 / 中 / 低 / 最低）、设备序列号（如 LT2410FE3P）、
报告生成日期。三档的 TAR/TBR 是把「高+很高」「低+很低」合并过的值，合并就
对不上报告上的原数，所以五档也分开存。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e9f1a2b3c4"
down_revision: str | None = "c7d8e9f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("device_serial", sa.String(length=32), False, ""),
    ("report_generated_at", sa.Date(), True, None),
    ("normal_percent", sa.Numeric(precision=5, scale=2), True, None),
    ("low_percent", sa.Numeric(precision=5, scale=2), True, None),
    ("very_low_percent", sa.Numeric(precision=5, scale=2), True, None),
    ("high_percent", sa.Numeric(precision=5, scale=2), True, None),
    ("very_high_percent", sa.Numeric(precision=5, scale=2), True, None),
    ("lbgi_level", sa.String(length=12), False, ""),
)


def upgrade() -> None:
    for name, kind, nullable, server_default in _COLUMNS:
        op.add_column(
            "glucose_reports",
            sa.Column(name, kind, server_default=server_default, nullable=nullable),
        )


def downgrade() -> None:
    for name, _kind, _nullable, _default in reversed(_COLUMNS):
        op.drop_column("glucose_reports", name)
