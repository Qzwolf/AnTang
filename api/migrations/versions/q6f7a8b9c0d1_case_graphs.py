"""认知图与情感图的节点、边

Revision ID: q6f7a8b9c0d1
Revises: p5e6f7a8b9c0
Create Date: 2026-11-01

CASE 的核心是「两张图对齐」：患者说出来的念头（认知图）和他当时的情绪、
身体感觉（情感图），以及它们之间怎么连。先把最小的存储定下来——
节点只放他自己的说法，边只允许四种关系，不做任意图。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q6f7a8b9c0d1"
down_revision: str | None = "p5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.String(length=400), nullable=False),
        sa.Column("intensity", sa.Integer(), nullable=True),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "layer IN ('cognitive', 'affective')",
            name="ck_graph_nodes_layer",
        ),
        sa.CheckConstraint(
            "kind IN ('situation', 'event', 'thought', 'belief', 'emotion',"
            " 'body', 'urge')",
            name="ck_graph_nodes_kind",
        ),
        sa.CheckConstraint(
            "(layer = 'cognitive' AND kind IN ('situation', 'event', 'thought',"
            " 'belief')) OR (layer = 'affective' AND kind IN ('emotion', 'body',"
            " 'urge'))",
            name="ck_graph_nodes_kind_matches_layer",
        ),
        sa.CheckConstraint(
            "intensity IS NULL OR (intensity BETWEEN 1 AND 5)",
            name="ck_graph_nodes_intensity",
        ),
        sa.CheckConstraint(
            "layer = 'affective' OR intensity IS NULL",
            name="ck_graph_nodes_cognitive_has_no_intensity",
        ),
    )
    op.create_index(
        "ix_graph_nodes_user_layer", "graph_nodes", ["user_id", "layer"]
    )

    op.create_table(
        "graph_edges",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("from_node_id", sa.Uuid(), nullable=False),
        sa.Column("to_node_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["from_node_id"], ["graph_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["to_node_id"], ["graph_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "relation IN ('causes', 'supports', 'triggers', 'soothes', 'avoids')",
            name="ck_graph_edges_relation",
        ),
        sa.UniqueConstraint(
            "from_node_id", "to_node_id", "relation", name="uq_graph_edge_triplet"
        ),
    )
    op.create_index("ix_graph_edges_user", "graph_edges", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_graph_edges_user", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index("ix_graph_nodes_user_layer", table_name="graph_nodes")
    op.drop_table("graph_nodes")
