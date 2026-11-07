"""CASE 的三个工具：记节点、连边、读图。

只测「患者说的话进得去、说不清的进不去」：念头和情绪落到各自的层、强度只给情绪、
没连上的两边不许猜着连、别人的节点碰不到、读图时能按层筛、来源能追到这一轮的哪条消息。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.runtime import AgentContext
from antang_api.chat import prepare_chat_run
from antang_api.models import GraphEdge, GraphNode, ProactiveCareSettings, User
from antang_api.tools.case_graph import (
    CONNECT_CASE_NODES_TOOL_NAME,
    READ_CASE_GRAPH_TOOL_NAME,
    RECORD_CASE_NODE_TOOL_NAME,
    build_case_graph_tools,
)


class _Sessions:
    """让工具复用测试事务里的会话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _Sessions:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _coroutine(
    tool: Any,
) -> Callable[..., Awaitable[dict[str, object]]]:
    assert isinstance(tool, StructuredTool)
    return cast(Callable[..., Awaitable[dict[str, object]]], tool.coroutine)


def _tools(
    session: AsyncSession,
) -> tuple[
    Callable[..., Awaitable[dict[str, object]]],
    Callable[..., Awaitable[dict[str, object]]],
    Callable[..., Awaitable[dict[str, object]]],
]:
    node_tool, edge_tool, read_tool = build_case_graph_tools(cast(Any, _Sessions(session)))
    return _coroutine(node_tool), _coroutine(edge_tool), _coroutine(read_tool)


def _runtime(user_id: UUID, run_id: UUID | None = None) -> ToolRuntime[AgentContext]:
    return ToolRuntime(
        state={"messages": []},
        context=AgentContext(
            user_id=user_id,
            run_id=run_id if run_id is not None else uuid4(),
            input_message_count=1,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="case-graph-call",
        store=None,
        tools=[],
    )


async def _create_user(session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:10]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    session.add(user)
    await session.flush()
    # prepare_chat_run 会给这个用户排下一次日常问候，缺设置行会直接报错。
    session.add(ProactiveCareSettings(user_id=user.id))
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_a_thought_and_a_feeling_land_in_their_own_layers(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "case_tool")
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "夜里没人看着我，我可能会睡过去"
    )
    await db_session.commit()
    node_tool, _edge_tool, read_tool = _tools(db_session)
    runtime = _runtime(user.id, prepared_run.run_id)

    thought = await node_tool(
        runtime=runtime,
        layer="cognitive",
        kind="thought",
        text="夜里没人看着我，我可能会睡过去",
    )
    fear = await node_tool(
        runtime=runtime, layer="affective", kind="emotion", text="害怕", intensity=4
    )

    assert thought["recorded"] is True
    assert thought["layer"] == "cognitive"
    assert fear["label"] == "情绪"

    stored = list((await db_session.scalars(select(GraphNode))).all())
    assert [node.text for node in stored] == ["夜里没人看着我，我可能会睡过去", "害怕"]
    assert stored[0].source_message_id == prepared_run.user_message_id

    snapshot = await read_tool(runtime=runtime)
    nodes = snapshot["nodes"]
    assert isinstance(nodes, list)
    assert [node["text"] for node in nodes] == [
        "夜里没人看着我，我可能会睡过去",
        "害怕",
    ]


@pytest.mark.asyncio
async def test_a_thought_cannot_carry_an_intensity(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, "case_strength")
    node_tool, _edge_tool, _read_tool = _tools(db_session)

    with pytest.raises(ValueError, match="强度只对情绪记"):
        await node_tool(
            runtime=_runtime(user.id),
            layer="cognitive",
            kind="thought",
            text="我一定会低血糖",
            intensity=3,
        )

    assert list((await db_session.scalars(select(GraphNode))).all()) == []


@pytest.mark.asyncio
async def test_the_edge_the_patient_described_can_be_drawn(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "case_edge")
    node_tool, edge_tool, read_tool = _tools(db_session)
    runtime = _runtime(user.id)
    thought = await node_tool(
        runtime=runtime, layer="cognitive", kind="thought", text="我一到晚上就害怕"
    )
    fear = await node_tool(
        runtime=runtime, layer="affective", kind="emotion", text="心里发紧", intensity=3
    )

    edge = await edge_tool(
        runtime=runtime,
        from_node_id=str(thought["node_id"]),
        to_node_id=str(fear["node_id"]),
        relation="triggers",
        note="因为一到晚上就担心低血糖",
    )

    assert edge["connected"] is True
    assert edge["label"] == "触发"
    snapshot = await read_tool(runtime=runtime, layer="affective")
    nodes = snapshot["nodes"]
    assert isinstance(nodes, list)
    assert [node["text"] for node in nodes] == ["心里发紧"]


@pytest.mark.asyncio
async def test_another_patients_node_cannot_be_linked(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, "case_owner")
    stranger = await _create_user(db_session, "case_stranger")
    node_tool, edge_tool, _read_tool = _tools(db_session)
    mine = await node_tool(
        runtime=_runtime(owner.id), layer="cognitive", kind="situation", text="凌晨三点醒来"
    )
    theirs = await node_tool(
        runtime=_runtime(stranger.id),
        layer="affective",
        kind="emotion",
        text="害怕",
        intensity=2,
    )

    with pytest.raises(ValueError, match="找不到这两个节点"):
        await edge_tool(
            runtime=_runtime(owner.id),
            from_node_id=str(mine["node_id"]),
            to_node_id=str(theirs["node_id"]),
            relation="triggers",
        )

    assert list((await db_session.scalars(select(GraphEdge))).all()) == []


def test_the_three_tool_names_are_stable() -> None:
    session = cast(AsyncSession, object())
    names = [tool.name for tool in build_case_graph_tools(cast(Any, _Sessions(session)))]

    assert names == [
        RECORD_CASE_NODE_TOOL_NAME,
        CONNECT_CASE_NODES_TOOL_NAME,
        READ_CASE_GRAPH_TOOL_NAME,
    ]
