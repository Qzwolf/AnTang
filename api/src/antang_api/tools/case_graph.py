"""认知图与情感图的记录工具。

三个动作分开：记一个节点、把两个节点连起来、把这个人现在的两张图读出来。
分开是为了让模型一次只做一件说得清的事——先记他说了「凌晨三点醒来」（情境），
再记他说的「又要来一次了」（念头），最后才把这两条连起来。
"""

from typing import Annotated, Literal
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.database import session_factory as default_session_factory
from antang_api.models import AgentRun, Message, MessageRole
from antang_api.psychosocial.graphs import (
    KIND_LABELS,
    RELATION_LABELS,
    add_edge,
    add_node,
    graph_snapshot,
)

RECORD_CASE_NODE_TOOL_NAME = "record_case_node"
CONNECT_CASE_NODES_TOOL_NAME = "connect_case_nodes"
READ_CASE_GRAPH_TOOL_NAME = "read_case_graph"

Layer = Annotated[
    Literal["cognitive", "affective"],
    Field(
        description=(
            "cognitive=念头这一侧（情境、发生的事、念头、当成事实的想法）；"
            "affective=情绪这一侧（情绪、身体感觉、冲动）。"
        )
    ),
]
Kind = Annotated[
    Literal["situation", "event", "thought", "belief", "emotion", "body", "urge"],
    Field(
        description=(
            "situation=情境；event=发生的事；thought=念头；belief=他当成事实的想法；"
            "emotion=情绪；body=身体感觉；urge=冲动。念头那一侧只能填前四个，"
            "情绪那一侧只能填后三个。"
        )
    ),
]
NodeText = Annotated[
    str,
    Field(
        min_length=1,
        max_length=400,
        description="他自己说出来的那句话，尽量逐字，不要改写成书面语。",
    ),
]
Intensity = Annotated[
    int | None,
    Field(ge=1, le=5, description="情绪的强度 1–5；念头那一侧不填。"),
]
Relation = Annotated[
    Literal["causes", "supports", "triggers", "soothes", "avoids"],
    Field(
        description=(
            "念头→念头：causes（引出）/ supports（支持）；念头→情绪：triggers（触发）；"
            "情绪→情绪：soothes（安抚）；情绪→情境：avoids（回避）。"
        )
    ),
]
EdgeNote = Annotated[
    str | None,
    Field(
        max_length=200,
        description="他自己说的「因为…所以…」原话片段，用来事后核对；没有就留空。",
    ),
]


async def _trigger_message_id(
    session: AsyncSession, runtime: ToolRuntime[AgentContext]
) -> UUID | None:
    """这一轮是由哪条患者消息触发的；连不上就不写来源。"""

    run = await session.get(AgentRun, runtime.context.run_id)
    if run is None or run.trigger_message_id is None:
        return None
    message = await session.get(Message, run.trigger_message_id)
    if message is None or message.role != MessageRole.USER:
        return None
    return message.id


def build_case_graph_tools(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> tuple[BaseTool, BaseTool, BaseTool]:
    """创建记节点、连边、读图三个工具。"""

    @tool(RECORD_CASE_NODE_TOOL_NAME)
    async def record_case_node(
        runtime: ToolRuntime[AgentContext],
        layer: Layer,
        kind: Kind,
        text: NodeText,
        intensity: Intensity = None,
    ) -> dict[str, object]:
        """把患者这一轮说出来的一个念头、一段情绪或一种身体感觉记下来。

        他明确说出了自己的想法或感受时才记（例如「我一到晚上就害怕」
        「我觉得我肯定撑不过去」「心里发紧」「心跳很快」）。只接住情绪、不调工具，
        这一条就不会留在他的图里。不能从语气推断，也不能把他没有的话写进去。
        """

        async with sessions() as session:
            node = await add_node(
                session,
                user_id=runtime.context.user_id,
                layer=layer,
                kind=kind,
                text=text,
                intensity=intensity,
                source_message_id=await _trigger_message_id(session, runtime),
            )
            await session.commit()
            return {
                "recorded": True,
                "node_id": str(node.id),
                "layer": node.layer,
                "kind": node.kind,
                "label": KIND_LABELS[node.kind],
            }

    @tool(CONNECT_CASE_NODES_TOOL_NAME)
    async def connect_case_nodes(
        runtime: ToolRuntime[AgentContext],
        from_node_id: str,
        to_node_id: str,
        relation: Relation,
        note: EdgeNote = None,
    ) -> dict[str, object]:
        """把两个已记下的节点连起来，前提是患者自己说清了这层因果。

        他说「因为怕夜里低血糖，我一到晚上就睡不着」这类话时可以连：念头→情绪用
        triggers，念头→念头用 causes/supports，情绪→情绪用 soothes，情绪→情境用
        avoids。两边 id 从 read_case_graph 拿。他没说清因果就不要猜着连。
        """

        async with sessions() as session:
            edge = await add_edge(
                session,
                user_id=runtime.context.user_id,
                from_node_id=UUID(from_node_id),
                to_node_id=UUID(to_node_id),
                relation=relation,
                note=note,
                source_message_id=await _trigger_message_id(session, runtime),
            )
            await session.commit()
            return {
                "connected": True,
                "relation": edge.relation,
                "label": RELATION_LABELS[edge.relation],
            }

    @tool(READ_CASE_GRAPH_TOOL_NAME)
    async def read_case_graph(
        runtime: ToolRuntime[AgentContext],
        layer: Layer | None = None,
    ) -> dict[str, object]:
        """读出患者已经被记下的念头与情绪（含 id），用于连边或回看。

        只看某一侧就填 layer；不填就两层一起读。回复里引用时用他自己的说法，
        不要把这些当成诊断结论，也不要在对话里念 id。
        """

        async with sessions() as session:
            snapshot = await graph_snapshot(
                session, user_id=runtime.context.user_id, layer=layer
            )
        nodes = snapshot.get("nodes")
        edges = snapshot.get("edges")
        return {
            "nodes": nodes if isinstance(nodes, list) else [],
            "edges": edges if isinstance(edges, list) else [],
        }

    return record_case_node, connect_case_nodes, read_case_graph


__all__ = [
    "CONNECT_CASE_NODES_TOOL_NAME",
    "READ_CASE_GRAPH_TOOL_NAME",
    "RECORD_CASE_NODE_TOOL_NAME",
    "build_case_graph_tools",
]
