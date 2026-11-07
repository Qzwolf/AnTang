"""行为痕迹触发的任务：来源是「数据长成这样」，不是穿戴导入。

Revision ID: s8b9c0d1e2f3
Revises: r7a8b9c0d1e2
Create Date: 2026-11-02

原来的来源约束要求：心率事件必须绑一条穿戴导入、计划随访必须绑一个计划。行为痕迹
两者都没有——它来自对已有数据的一次统计，所以放宽约束、单独加一类来源，而不是硬塞
进另外两类里去。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s8b9c0d1e2f3"
down_revision: str | None = "r7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = (
    "(kind = 'health_event' AND wearable_import_id IS NOT NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
    "(kind = 'routine_check_in' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
    "(kind = 'plan_follow_up' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NOT NULL AND care_plan_revision IS NOT NULL)"
)

_NEW = (
    "(kind = 'health_event' AND wearable_import_id IS NOT NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
    "(kind = 'routine_check_in' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
    "(kind = 'plan_follow_up' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NOT NULL AND care_plan_revision IS NOT NULL) OR "
    "(kind = 'behavior_signal' AND wearable_import_id IS NULL "
    "AND care_plan_id IS NULL AND care_plan_revision IS NULL)"
)


def upgrade() -> None:
    # 新增枚举值必须在自动提交里做：同一个事务里加完又拿它去建约束，PostgreSQL
    # 会报「unsafe use of new value of enum type」。
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE proactive_care_task_kind "
            "ADD VALUE IF NOT EXISTS 'behavior_signal'"
        )
    op.drop_constraint(
        "ck_proactive_care_tasks_source",
        "proactive_care_tasks",
        type_="check",
    )
    op.create_check_constraint(
        "ck_proactive_care_tasks_source",
        "proactive_care_tasks",
        _NEW,
    )


def downgrade() -> None:
    # 回退前先把行为痕迹任务清掉，否则收窄的约束会让它们违约。
    op.execute(
        "DELETE FROM proactive_care_tasks WHERE kind = 'behavior_signal'"
    )
    op.drop_constraint(
        "ck_proactive_care_tasks_source",
        "proactive_care_tasks",
        type_="check",
    )
    op.create_check_constraint(
        "ck_proactive_care_tasks_source",
        "proactive_care_tasks",
        _OLD,
    )
