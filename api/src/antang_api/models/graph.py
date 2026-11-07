from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base

# 两张图（认知 / 情感）放在同一对表里，用 layer 区分。分开建四张表没有好处：
# 查询总是「这个人的两张图一起看」，而 CASE 的核心正是把两张图对齐。
GRAPH_LAYERS: tuple[str, ...] = ("cognitive", "affective")

# 认知图的节点：情境、发生的事、当时的念头、被当成事实的假设。
COGNITIVE_KINDS: tuple[str, ...] = ("situation", "event", "thought", "belief")

# 情感图的节点：情绪、身体感觉、冲动（想做的事）。
AFFECTIVE_KINDS: tuple[str, ...] = ("emotion", "body", "urge")

# 两条图之间的边只有四种：念头触发情绪、念头引出念头、情绪安抚情绪、
# 情绪驱动回避某个情境。这里刻意不做「任意两节点都能连」——
# 连不上的边通常说明节点记错了，而不是发现了新关系。
GRAPH_RELATIONS: tuple[str, ...] = ("causes", "supports", "triggers", "soothes", "avoids")

# 允许的边：起点图层 + 终点图层 → 可以用哪些关系。校验放在服务层，
# 因为 CHECK 约束读不到另一端的 layer。
ALLOWED_RELATIONS: dict[tuple[str, str], frozenset[str]] = {
    ("cognitive", "cognitive"): frozenset({"causes", "supports"}),
    ("cognitive", "affective"): frozenset({"triggers"}),
    ("affective", "affective"): frozenset({"soothes"}),
    ("affective", "cognitive"): frozenset({"avoids"}),
}

_LAYERS_SQL = ", ".join(f"'{layer}'" for layer in GRAPH_LAYERS)
_KINDS_SQL = ", ".join(f"'{kind}'" for kind in (*COGNITIVE_KINDS, *AFFECTIVE_KINDS))
_RELATIONS_SQL = ", ".join(f"'{relation}'" for relation in GRAPH_RELATIONS)

# 认知图的 kind 与情感图的 kind 不能混用：这由一条 CHECK 约束在库层面卡住。
_KIND_MATCHES_LAYER_SQL = (
    "(layer = 'cognitive' AND kind IN ("
    + ", ".join(f"'{kind}'" for kind in COGNITIVE_KINDS)
    + ")) OR (layer = 'affective' AND kind IN ("
    + ", ".join(f"'{kind}'" for kind in AFFECTIVE_KINDS)
    + "))"
)


class GraphNode(Base):
    """认知图或情感图上的一个节点。

    只记患者在对话里真正说过的东西，不预填、不推断。`text` 保留他自己的说法
    （「我一想到夜里没人就睡不着」），不要把模型的话写进来。
    """

    __tablename__ = "graph_nodes"
    __table_args__ = (
        CheckConstraint(f"layer IN ({_LAYERS_SQL})", name="ck_graph_nodes_layer"),
        CheckConstraint(f"kind IN ({_KINDS_SQL})", name="ck_graph_nodes_kind"),
        CheckConstraint(_KIND_MATCHES_LAYER_SQL, name="ck_graph_nodes_kind_matches_layer"),
        # 强度只对情感图有意义（情绪有多强），认知图不填。
        CheckConstraint(
            "intensity IS NULL OR (intensity BETWEEN 1 AND 5)",
            name="ck_graph_nodes_intensity",
        ),
        CheckConstraint(
            "layer = 'affective' OR intensity IS NULL",
            name="ck_graph_nodes_cognitive_has_no_intensity",
        ),
        Index("ix_graph_nodes_user_layer", "user_id", "layer"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(String(400), nullable=False)
    intensity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GraphEdge(Base):
    """两个节点之间的关系。

    `note` 用来记「为什么这么连」，例如「他一想到要打针，就觉得手会抖」——
    节点本身存不下这条连接的含义。
    """

    __tablename__ = "graph_edges"
    __table_args__ = (
        CheckConstraint(f"relation IN ({_RELATIONS_SQL})", name="ck_graph_edges_relation"),
        UniqueConstraint(
            "from_node_id", "to_node_id", "relation", name="uq_graph_edge_triplet"
        ),
        Index("ix_graph_edges_user", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    from_node_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False
    )
    to_node_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
