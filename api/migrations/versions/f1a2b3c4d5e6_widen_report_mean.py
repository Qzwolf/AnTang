"""报告均值改成两位小数

报告上印的均值是 5.81、6.29 这种两位小数，Numeric(4,1) 会把 5.81 存成 5.8——
记录页展示和医生看的都是这个数，少一位就是报告之外的另一个数字，所以放宽到两位。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e9f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "glucose_reports",
        "mean_mmol",
        existing_type=sa.Numeric(4, 1),
        type_=sa.Numeric(4, 2),
        existing_nullable=True,
    )


def downgrade() -> None:
    # 退回去会四舍五入丢掉第二位小数，这一步是不可逆的信息损失，只能接受。
    op.alter_column(
        "glucose_reports",
        "mean_mmol",
        existing_type=sa.Numeric(4, 2),
        type_=sa.Numeric(4, 1),
        existing_nullable=True,
    )
