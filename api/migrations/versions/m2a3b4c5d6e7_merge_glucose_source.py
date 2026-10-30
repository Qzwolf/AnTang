"""merge the two glucose source branches and keep every source

Revision ID: m2a3b4c5d6e7
Revises: h1c2d3e4f5a6, v19ae2b35d48
Create Date: 2026-10-30

两条线都改过 ck_glucose_source：
- 我们这条线 u08fa1b2c3d4 把它扩成 manual/fingerstick/cgm/lab/report；
- 团队那条线 u08fd1a24c37 扩到 manual/report/public_dataset，v19ae2b35d48 再加
  ohio_cgm。
两条线各自把约束写成自己认识的那几个值，谁最后跑谁说了算——患者录一条指尖血
就可能撞上团队的约束。合并点上把两边的值并起来，谁也不用牺牲。

revision 是指向两条线的 merge 点，alembic 之后只有一个 head。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "m2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = ("h1c2d3e4f5a6", "v19ae2b35d48")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 患者自己录的 + 报告解析的 + 研究数据集导入的，并集。
_MERGED_SOURCES_SQL = (
    "'manual', 'fingerstick', 'cgm', 'lab', 'report', 'public_dataset', 'ohio_cgm'"
)


def upgrade() -> None:
    op.drop_constraint("ck_glucose_source", "glucose_readings", type_="check")
    op.create_check_constraint(
        "ck_glucose_source", "glucose_readings", f"source IN ({_MERGED_SOURCES_SQL})"
    )


def downgrade() -> None:
    # 不把约束改回去：这条修订只做了「放宽」。降级时如果重新收窄，库里已经写进去的
    # 指尖血 / 化验单记录会立刻违反约束。数据留在这里，谁要降级谁自己决定怎么处理。
    pass
