"""双风险回访：任务类型多一个值

「血糖低到 3.2、当时没觉得」之后隔几天问一句，不是重复提醒，是把那一天的
后半段补上：后来有没有再低过、有没有跟家人说过、手边有没有放糖。

PostgreSQL 的枚举值不能在同一个事务里既 ADD VALUE 又使用它，所以这里先
autocommit 加值，再另起事务改 CHECK。

Revision ID: w3f4a5b6c7d8
Revises: v2e3f4a5b6c7
Create Date: 2026-11-07
"""

from __future__ import annotations

from alembic import op

revision = "w3f4a5b6c7d8"
down_revision = "v2e3f4a5b6c7"
branch_labels = None
depends_on = None

_KIND_CHECK = (
    "(kind = 'health_event' AND wearable_import_id IS NOT NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
    "(kind = 'routine_check_in' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
    "(kind = 'plan_follow_up' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NOT NULL AND care_plan_revision IS NOT NULL) OR "
    "(kind = 'behavior_signal' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
    "(kind = 'dual_risk_follow_up' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL)"
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE proactive_care_task_kind "
            "ADD VALUE IF NOT EXISTS 'dual_risk_follow_up'"
        )
    op.drop_constraint(
        "ck_proactive_care_tasks_source", "proactive_care_tasks", type_="check"
    )
    op.create_check_constraint(
        "ck_proactive_care_tasks_source", "proactive_care_tasks", _KIND_CHECK
    )


def downgrade() -> None:
    # 回退前先清掉这类任务：收窄 CHECK 时留着它们会违约。
    op.execute("DELETE FROM proactive_care_tasks WHERE kind = 'dual_risk_follow_up'")
    op.drop_constraint(
        "ck_proactive_care_tasks_source", "proactive_care_tasks", type_="check"
    )
    op.create_check_constraint(
        "ck_proactive_care_tasks_source",
        "proactive_care_tasks",
        _KIND_CHECK.replace(
            " OR "
            "(kind = 'dual_risk_follow_up' AND wearable_import_id IS NULL "
            "AND care_plan_id IS NULL AND care_plan_revision IS NULL)",
            "",
        ),
    )
