"""add insulin_change to fear behavior kinds

Revision ID: x1c2d3e4f5a6
Revises: w2b3c4d5e6f7
Create Date: 2026-10-25

患者自己加减胰岛素要能记成一条行为，否则「改剂量」只能落在自由文本里，
后续既没法统计，也没法在复诊前把这类条目挑出来给医生看。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "x1c2d3e4f5a6"
down_revision: str | None = "w2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_WITH_INSULIN_CHANGE = (
    "'extra_check'",
    "'night_check'",
    "'comfort_meal'",
    "'exercise_avoidance'",
    "'insulin_hold'",
    "'insulin_change'",
)

KINDS_WITHOUT_INSULIN_CHANGE = (
    "'extra_check'",
    "'night_check'",
    "'comfort_meal'",
    "'exercise_avoidance'",
    "'insulin_hold'",
)


def _replace_kind_constraint(kinds: tuple[str, ...]) -> None:
    op.drop_constraint("ck_fear_behavior_kind", "fear_behavior_logs", type_="check")
    op.create_check_constraint(
        "ck_fear_behavior_kind",
        "fear_behavior_logs",
        f"kind IN ({', '.join(kinds)})",
    )


def upgrade() -> None:
    _replace_kind_constraint(KINDS_WITH_INSULIN_CHANGE)


def downgrade() -> None:
    # 回滚前先把这类记录降级成「少打或推迟胰岛素」，避免删约束时被现有数据卡住。
    op.execute(
        "UPDATE fear_behavior_logs SET kind = 'insulin_hold' "
        "WHERE kind = 'insulin_change'"
    )
    _replace_kind_constraint(KINDS_WITHOUT_INSULIN_CHANGE)
