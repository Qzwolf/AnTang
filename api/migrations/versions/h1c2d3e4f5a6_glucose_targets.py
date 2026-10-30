"""血糖目标范围变成患者自己的设置。

原来 70 mg/dL（3.9）写死在代码里。老年人的目标常被医生放宽到 4.4–10.0 甚至更高，
孕妇要更严；写死的下线会把这些人的正常值算成「偏低」，也会让 AI 拿错标准说话。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h1c2d3e4f5a6"
down_revision: str | None = "g1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "personal_profiles",
        sa.Column(
            "glucose_target_low_mg_dl",
            sa.Integer(),
            nullable=False,
            server_default="70",
        ),
    )
    op.add_column(
        "personal_profiles",
        sa.Column(
            "glucose_target_high_mg_dl",
            sa.Integer(),
            nullable=False,
            server_default="180",
        ),
    )
    op.create_check_constraint(
        "ck_personal_profiles_glucose_target",
        "personal_profiles",
        "glucose_target_low_mg_dl > 0 AND glucose_target_low_mg_dl < glucose_target_high_mg_dl",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_personal_profiles_glucose_target", "personal_profiles", type_="check"
    )
    op.drop_column("personal_profiles", "glucose_target_high_mg_dl")
    op.drop_column("personal_profiles", "glucose_target_low_mg_dl")
