"""认知图与情感图怎么存、怎么连。

只测「存进去的东西和挡住的错误」：节点带不带强度、想法和情绪能不能连、
别人的节点碰不到、删节点时边跟着走。
"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import GraphEdge, User
from antang_api.routers.health_profile import get_case_graph
from antang_api.psychosocial.graphs import (
    add_edge,
    add_node,
    delete_node,
    graph_snapshot,
    render_graph_lines,
)


async def create_user(db_session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:10]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_a_thought_and_the_feeling_it_triggers_are_kept_in_two_layers(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "g_pair")
    thought = await add_node(
        db_session,
        user_id=user.id,
        layer="cognitive",
        kind="thought",
        text="夜里没人看着我，我可能会睡过去",
    )
    fear = await add_node(
        db_session,
        user_id=user.id,
        layer="affective",
        kind="emotion",
        text="害怕",
        intensity=4,
    )
    edge = await add_edge(
        db_session,
        user_id=user.id,
        from_node_id=thought.id,
        to_node_id=fear.id,
        relation="triggers",
    )

    snapshot = await graph_snapshot(db_session, user_id=user.id)
    nodes = snapshot["nodes"]
    edges = snapshot["edges"]
    assert isinstance(nodes, list) and isinstance(edges, list)
    assert len(nodes) == 2
    assert len(edges) == 1
    assert edge.relation == "triggers"
    lines = render_graph_lines(snapshot)
    assert "念头：夜里没人看着我，我可能会睡过去" in lines
    assert "情绪：害怕（强度 4）" in lines
    assert any("触发" in line for line in lines)


@pytest.mark.asyncio
async def test_a_thought_cannot_carry_an_intensity(db_session: AsyncSession) -> None:
    user = await create_user(db_session, "g_strength")
    with pytest.raises(ValueError, match="强度只对情绪记"):
        await add_node(
            db_session,
            user_id=user.id,
            layer="cognitive",
            kind="thought",
            text="我肯定撑不过今晚",
            intensity=3,
        )


@pytest.mark.asyncio
async def test_a_feeling_cannot_be_stored_as_a_thought(db_session: AsyncSession) -> None:
    user = await create_user(db_session, "g_layer")
    with pytest.raises(ValueError, match="念头图里没有"):
        await add_node(
            db_session,
            user_id=user.id,
            layer="cognitive",
            kind="emotion",
            text="害怕",
        )


@pytest.mark.asyncio
async def test_two_thoughts_cannot_be_joined_by_triggering(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "g_relation")
    first = await add_node(
        db_session,
        user_id=user.id,
        layer="cognitive",
        kind="situation",
        text="凌晨三点醒过来",
    )
    second = await add_node(
        db_session,
        user_id=user.id,
        layer="cognitive",
        kind="thought",
        text="又要来一次了",
    )
    with pytest.raises(ValueError, match="只能用「引出」或「支持」"):
        await add_edge(
            db_session,
            user_id=user.id,
            from_node_id=first.id,
            to_node_id=second.id,
            relation="triggers",
        )


@pytest.mark.asyncio
async def test_connecting_the_same_pair_twice_keeps_one_edge(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "g_twice")
    thought = await add_node(
        db_session,
        user_id=user.id,
        layer="cognitive",
        kind="thought",
        text="我会像他们一样",
    )
    fear = await add_node(
        db_session,
        user_id=user.id,
        layer="affective",
        kind="emotion",
        text="担心",
    )
    first = await add_edge(
        db_session,
        user_id=user.id,
        from_node_id=thought.id,
        to_node_id=fear.id,
        relation="triggers",
    )
    again = await add_edge(
        db_session,
        user_id=user.id,
        from_node_id=thought.id,
        to_node_id=fear.id,
        relation="triggers",
    )
    count = await db_session.scalar(
        select(func.count()).select_from(GraphEdge).where(GraphEdge.user_id == user.id)
    )
    assert count == 1
    assert again.id == first.id


@pytest.mark.asyncio
async def test_someone_elses_node_cannot_be_wired_into_my_graph(
    db_session: AsyncSession,
) -> None:
    mine = await create_user(db_session, "g_mine")
    theirs = await create_user(db_session, "g_theirs")
    my_thought = await add_node(
        db_session,
        user_id=mine.id,
        layer="cognitive",
        kind="thought",
        text="我不该吃这顿",
    )
    their_fear = await add_node(
        db_session,
        user_id=theirs.id,
        layer="affective",
        kind="emotion",
        text="内疚",
    )
    with pytest.raises(ValueError, match="找不到这两个节点"):
        await add_edge(
            db_session,
            user_id=mine.id,
            from_node_id=my_thought.id,
            to_node_id=their_fear.id,
            relation="triggers",
        )


@pytest.mark.asyncio
async def test_deleting_a_node_takes_its_edges_with_it(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "g_delete")
    thought = await add_node(
        db_session,
        user_id=user.id,
        layer="cognitive",
        kind="thought",
        text="吃了甜的就会出事",
    )
    fear = await add_node(
        db_session,
        user_id=user.id,
        layer="affective",
        kind="body",
        text="心跳很快",
    )
    await add_edge(
        db_session,
        user_id=user.id,
        from_node_id=thought.id,
        to_node_id=fear.id,
        relation="triggers",
    )
    assert await delete_node(db_session, user_id=user.id, node_id=thought.id) is True
    remaining = await db_session.scalar(
        select(func.count()).select_from(GraphEdge).where(GraphEdge.user_id == user.id)
    )
    assert remaining == 0
    assert await delete_node(db_session, user_id=user.id, node_id=thought.id) is False


@pytest.mark.asyncio
async def test_the_read_only_view_lists_what_he_said(db_session: AsyncSession) -> None:
    """心理页的只读视图：节点带中文标签，边带关系名字，看不到内部 id 以外的字段。"""

    user = await create_user(db_session, "g_view")
    thought = await add_node(
        db_session,
        user_id=user.id,
        layer="cognitive",
        kind="thought",
        text="我一到晚上就害怕",
    )
    fear = await add_node(
        db_session,
        user_id=user.id,
        layer="affective",
        kind="emotion",
        text="心里发紧",
        intensity=3,
    )
    await add_edge(
        db_session,
        user_id=user.id,
        from_node_id=thought.id,
        to_node_id=fear.id,
        relation="triggers",
        note="因为怕夜里低血糖",
    )

    response = await get_case_graph(user=user, session=db_session)

    assert [node.kind_label for node in response.nodes] == ["念头", "情绪"]
    assert [node.text for node in response.nodes] == ["我一到晚上就害怕", "心里发紧"]
    assert response.nodes[1].intensity == 3
    assert len(response.edges) == 1
    assert response.edges[0].relation_label == "触发"
    assert response.edges[0].note == "因为怕夜里低血糖"


@pytest.mark.asyncio
async def test_the_read_only_view_is_empty_before_he_says_anything(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "g_empty")
    response = await get_case_graph(user=user, session=db_session)

    assert response.nodes == []
    assert response.edges == []
