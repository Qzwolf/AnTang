"""认知图与情感图的读写。

只做两件事：把患者在对话里说出来的「念头 / 情绪」落成节点，把他自己承认的
「因为…所以…」落成边。谁也没有说的话不落，模型没有依据的连线不连。

关系词表是刻意窄的（见 `models/graph.py` 的 `ALLOWED_RELATIONS`）：
念头到念头只有「引起 / 支持」，念头到情绪只有「触发」，情绪到情绪只有
「安抚」，情绪到情境只有「回避」。连不上通常说明节点记错了。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import ALLOWED_RELATIONS, GraphEdge, GraphNode

LAYER_LABELS: dict[str, str] = {"cognitive": "念头", "affective": "情绪"}
_LAYER_KINDS: dict[str, tuple[str, ...]] = {
    "cognitive": ("situation", "event", "thought", "belief"),
    "affective": ("emotion", "body", "urge"),
}
KIND_LABELS: dict[str, str] = {
    "situation": "情境",
    "event": "发生的事",
    "thought": "念头",
    "belief": "当成事实的想法",
    "emotion": "情绪",
    "body": "身体感觉",
    "urge": "冲动",
}
RELATION_LABELS: dict[str, str] = {
    "causes": "引出",
    "supports": "支持",
    "triggers": "触发",
    "soothes": "安抚",
    "avoids": "回避",
}
_RELATION_HINTS: dict[tuple[str, str], str] = {
    ("cognitive", "cognitive"): "念头之间只能用「引出」或「支持」。",
    ("cognitive", "affective"): "念头指向情绪时只能用「触发」。",
    ("affective", "affective"): "情绪之间只能用「安抚」。",
    ("affective", "cognitive"): "情绪指向情境时只能用「回避」。",
}


def _check_node(layer: str, kind: str, text: str, intensity: int | None) -> str:
    if layer not in _LAYER_KINDS:
        raise ValueError(f"没有这张图：{layer}。只能记 cognitive 或 affective。")
    if kind not in _LAYER_KINDS[layer]:
        allowed = "、".join(_LAYER_KINDS[layer])
        raise ValueError(f"{LAYER_LABELS[layer]}图里没有「{kind}」这种节点，只能填：{allowed}。")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("这条要记的是他说的那句话，不能是空的。")
    if len(cleaned) > 400:
        raise ValueError("这一条太长了，只留他自己最关键的那句话。")
    if intensity is not None:
        if layer != "affective":
            raise ValueError("强度只对情绪记（1 到 5）；念头不记强度。")
        if not 1 <= intensity <= 5:
            raise ValueError("情绪强度只能填 1 到 5。")
    return cleaned


async def add_node(
    session: AsyncSession,
    *,
    user_id: UUID,
    layer: str,
    kind: str,
    text: str,
    intensity: int | None = None,
    source_message_id: UUID | None = None,
) -> GraphNode:
    """把一条节点落到对应的图上。"""

    node = GraphNode(
        user_id=user_id,
        layer=layer,
        kind=kind,
        text=_check_node(layer, kind, text, intensity),
        intensity=intensity,
        source_message_id=source_message_id,
    )
    session.add(node)
    await session.flush()
    return node


def _check_relation(from_node: GraphNode, to_node: GraphNode, relation: str) -> None:
    pair = (from_node.layer, to_node.layer)
    allowed = ALLOWED_RELATIONS.get(pair)
    if allowed is None:
        raise ValueError("这两类节点之间没有可用的关系。")
    if relation not in allowed:
        hint = _RELATION_HINTS[pair]
        raise ValueError(f"「{RELATION_LABELS.get(relation, relation)}」不能连这两种节点：{hint}")


async def add_edge(
    session: AsyncSession,
    *,
    user_id: UUID,
    from_node_id: UUID,
    to_node_id: UUID,
    relation: str,
    note: str | None = None,
    source_message_id: UUID | None = None,
) -> GraphEdge:
    """把两个节点连起来。同一条边重复连不会新增第二条。"""

    if from_node_id == to_node_id:
        raise ValueError("同一条不能连自己。")
    nodes = {
        node.id: node
        for node in (
            await session.scalars(
                select(GraphNode).where(
                    GraphNode.user_id == user_id,
                    GraphNode.id.in_((from_node_id, to_node_id)),
                )
            )
        ).all()
    }
    if len(nodes) != 2:
        # 不区分「不存在」和「是别人的」：这两句话都会让调用方去翻别人的图表。
        raise ValueError("找不到这两个节点。")
    from_node, to_node = nodes[from_node_id], nodes[to_node_id]
    _check_relation(from_node, to_node, relation)

    existing = await session.scalar(
        select(GraphEdge).where(
            GraphEdge.from_node_id == from_node_id,
            GraphEdge.to_node_id == to_node_id,
            GraphEdge.relation == relation,
        )
    )
    if existing is not None:
        return existing

    edge = GraphEdge(
        user_id=user_id,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        relation=relation,
        note=note.strip() if note else None,
        source_message_id=source_message_id,
    )
    session.add(edge)
    await session.flush()
    return edge


async def delete_node(session: AsyncSession, *, user_id: UUID, node_id: UUID) -> bool:
    """删掉一个节点，连在它上面的边一起删。返回是否真的删了。"""

    removed = await session.scalar(
        delete(GraphNode)
        .where(GraphNode.user_id == user_id, GraphNode.id == node_id)
        .returning(GraphNode.id)
    )
    return removed is not None


async def graph_snapshot(
    session: AsyncSession, *, user_id: UUID, layer: str | None = None
) -> dict[str, object]:
    """把这个人（某一层或两层）的图和边取出来，按节点组织。"""

    nodes_query = select(GraphNode).where(GraphNode.user_id == user_id)
    if layer is not None:
        nodes_query = nodes_query.where(GraphNode.layer == layer)
    nodes = list(
        (
            await session.scalars(
                # created_at 在同一个事务里可能完全相同，再用 uuidv7 的 id 兜一次顺序。
                nodes_query.order_by(GraphNode.created_at, GraphNode.id)
            )
        ).all()
    )
    node_ids = [node.id for node in nodes]
    edges: list[GraphEdge] = []
    if node_ids:
        edges = list(
            (
                await session.scalars(
                    select(GraphEdge)
                    .where(
                        GraphEdge.user_id == user_id,
                        GraphEdge.from_node_id.in_(node_ids),
                        GraphEdge.to_node_id.in_(node_ids),
                    )
                    .order_by(GraphEdge.created_at)
                )
            ).all()
        )
    return {
        "nodes": [
            {
                "id": str(node.id),
                "layer": node.layer,
                "kind": node.kind,
                "text": node.text,
                "intensity": node.intensity,
            }
            for node in nodes
        ],
        "edges": [
            {
                "from": str(edge.from_node_id),
                "to": str(edge.to_node_id),
                "relation": edge.relation,
                "note": edge.note,
            }
            for edge in edges
        ],
    }


def render_graph_lines(snapshot: dict[str, object]) -> list[str]:
    """把快照排成给提示词/界面看的中文句子。"""

    raw_nodes = snapshot.get("nodes") or []
    raw_edges = snapshot.get("edges") or []
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return []
    texts = {item["id"]: item for item in raw_nodes if isinstance(item, dict)}
    lines: list[str] = []
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        label = KIND_LABELS.get(str(item["kind"]), str(item["kind"]))
        strength = f"（强度 {item['intensity']}）" if item.get("intensity") else ""
        lines.append(f"{label}：{item['text']}{strength}")
    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue
        source = texts.get(str(edge["from"]))
        target = texts.get(str(edge["to"]))
        if source is None or target is None:
            continue
        relation = RELATION_LABELS.get(str(edge["relation"]), str(edge["relation"]))
        note = f"（{edge['note']}）" if edge.get("note") else ""
        lines.append(f"{source['text']} {relation} {target['text']}{note}")
    return lines

# 上下文里只放最近的几条：图会越记越长，全塞进去会把当轮对话挤掉。
CASE_GRAPH_CONTEXT_LIMIT = 12


def render_case_graph_context(
    snapshot: dict[str, object], *, limit: int = CASE_GRAPH_CONTEXT_LIMIT
) -> str:
    """把最近的念头与情绪排成一段给模型的上下文；没记过就返回空串。"""

    lines = render_graph_lines(snapshot)
    if not lines:
        return ""
    return "\n".join(
        [
            "[他之前说过的念头和情绪]",
            "这些是他自己在前面几轮里说出来的，只用来把话接住、把追问问准；"
            "不要念给他听，不要当成诊断结论，他没主动提起时也不要说「你上次说过」。",
            *lines[-limit:],
        ]
    )


@dataclass(frozen=True)
class GraphFocus:
    """一个值得再问一句的话题。"""

    node_id: str
    layer: str
    kind: str
    text: str
    intensity: int | None

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text if ch.strip() and ch not in "，。；、！？：,.;!?:")
    return {cleaned[index : index + 2] for index in range(len(cleaned) - 1)}


def _mentioned(text: str, said: tuple[str, ...]) -> bool:
    """这句话是不是最近刚聊过。

    患者很少原样重复自己说的话（「自习室太冷，一个人有点发慌」下次变成「自习室
    冷得我有点发慌」），所以按两字片段的重合度判，不按原文是否包含判。
    """

    text_parts = _bigrams(text)
    for message in said:
        if text in message:
            return True
        if len(text_parts) < 4:
            continue
        message_parts = _bigrams(message)
        if not message_parts:
            continue
        overlap = len(text_parts & message_parts) / min(
            len(text_parts), len(message_parts)
        )
        if overlap >= 0.5:
            return True
    return False


def choose_focus(
    snapshot: dict[str, object],
    *,
    mentioned: tuple[str, ...] = (),
    limit: int = 20,
) -> GraphFocus | None:
    """挑一个「他说过、但最近没被谈起」的话题。

    挑法刻意简单：先看情绪（尤其强度高的），再看念头；越近的越优先。已经在最近
    几条消息里出现过的跳过——否则主动消息会把同一个话题反复端上来。

    挑不出来就返回 None：没有话题时宁可只问一句「今天怎么样」，也不要从图上
    硬拽一条出来当借口。
    """

    raw_nodes = snapshot.get("nodes") or []
    if not isinstance(raw_nodes, list):
        return None
    candidates: list[GraphFocus] = []
    for item in raw_nodes[-limit:]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if _mentioned(text, mentioned):
            continue
        intensity = item.get("intensity")
        candidates.append(
            GraphFocus(
                node_id=str(item.get("id")),
                layer=str(item.get("layer")),
                kind=str(item.get("kind")),
                text=text,
                intensity=intensity if isinstance(intensity, int) else None,
            )
        )
    if not candidates:
        return None
    # 情绪优先、强度高的优先，其余按「越近越优先」保持原顺序。
    candidates.reverse()
    candidates.sort(
        key=lambda focus: (focus.layer == "affective", focus.intensity or 0),
        reverse=True,
    )
    return candidates[0]


def render_graph_focus(focus: GraphFocus | None) -> str:
    """把挑出来的话题拼成一句上下文；没有就返回空串。"""

    if focus is None:
        return ""
    strength = f"，强度 {focus.intensity}" if focus.intensity else ""
    return "\n".join(
        [
            "[他最近提过的一件心事]",
            f"{focus.kind_label}：{focus.text}{strength}",
            "要开口的话，可以顺着它问一句现在的样子；不要引用他的原话，"
            "不要说「你上次说过」，也不要把它当成结论。他今天显然不想聊这个，就换回日常的问候。",
        ]
    )