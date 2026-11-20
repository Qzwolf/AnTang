"""让患者自己定「一周最多主动联系几次」。

原来这个上限只由心理社会评估的四象限算出来，患者只能在设置页看到数字，改不了。
有人明明只想要一周一次，也有人希望多问两句——次数是最该交给本人的参数。

空值（NULL）表示「跟着评估走」：没做过评估是 3 次，有评估按四象限剂量。
上限只允许 1–7：0 次等于关掉日常问候，那是 routine_cadence 的开关，
不该藏在「次数」里，否则设置页会出现两个互相矛盾的「关掉」。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n3c4d5e6f7a8"
down_revision: str | None = "m2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proactive_care_settings",
        sa.Column("weekly_contact_limit", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_proactive_care_weekly_limit",
        "proactive_care_settings",
        "weekly_contact_limit IS NULL OR "
        "(weekly_contact_limit >= 1 AND weekly_contact_limit <= 7)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_proactive_care_weekly_limit",
        "proactive_care_settings",
        type_="check",
    )
    op.drop_column("proactive_care_settings", "weekly_contact_limit")
