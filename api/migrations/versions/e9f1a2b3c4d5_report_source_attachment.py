"""给报告速览加上「原文件是哪张附件」。

Revision ID: e9f1a2b3c4d5
Revises: d8e9f1a2b3c4

患者发进对话的那份 PDF 一直存在 message_attachments 里，但速览没记下它是从哪份
拆出来的，所以详情页没法「打开原报告」。这里加一列外键指过去：附件删了列会置空
（ON DELETE SET NULL），速览里的指标仍然留着。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f1a2b3c4d5"
down_revision: str | None = "d8e9f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "glucose_reports",
        sa.Column("source_attachment_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_glucose_reports_source_attachment",
        "glucose_reports",
        "message_attachments",
        ["source_attachment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_glucose_reports_source_attachment_id",
        "glucose_reports",
        ["source_attachment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_glucose_reports_source_attachment_id",
        table_name="glucose_reports",
    )
    op.drop_constraint(
        "fk_glucose_reports_source_attachment", "glucose_reports", type_="foreignkey"
    )
    op.drop_column("glucose_reports", "source_attachment_id")
