"""侧边栏对话列表的 HTTP 行为：建、列，以及不能把消息发进别人的会话。"""

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient

from antang_api.conversations import NEW_CONVERSATION_TITLE


async def register(client: AsyncClient, prefix: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "consent_accepted": True,
            "username": f"{prefix}_{uuid4().hex[:12]}",
            "password": "correct-password",
        },
    )
    assert response.status_code == 201
    return response.json()


def auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


async def test_conversations_need_a_login(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/chat/conversations")).status_code == 401
    assert (await client.post("/api/v1/chat/conversations")).status_code == 401


async def test_a_new_conversation_shows_up_in_the_list(client: AsyncClient) -> None:
    auth = await register(client, "conv_route")

    created = await client.post(
        "/api/v1/chat/conversations", headers=auth_headers(auth)
    )
    assert created.status_code == 201
    assert created.json()["title"] == NEW_CONVERSATION_TITLE

    listed = await client.get("/api/v1/chat/conversations", headers=auth_headers(auth))
    assert listed.status_code == 200
    conversations = listed.json()["conversations"]
    assert [item["id"] for item in conversations] == [created.json()["id"]]


async def test_a_fresh_conversation_has_no_messages_yet(client: AsyncClient) -> None:
    """侧边栏用 message_count 藏掉「点了新的对话却一句没说」的空段。"""

    auth = await register(client, "conv_empty")

    await client.post("/api/v1/chat/conversations", headers=auth_headers(auth))
    listed = await client.get("/api/v1/chat/conversations", headers=auth_headers(auth))

    assert listed.json()["conversations"][0]["message_count"] == 0


async def test_one_patients_conversations_are_invisible_to_another(
    client: AsyncClient,
) -> None:
    first = await register(client, "conv_owner")
    second = await register(client, "conv_stranger")
    created = await client.post(
        "/api/v1/chat/conversations", headers=auth_headers(first)
    )

    listed = await client.get(
        "/api/v1/chat/conversations", headers=auth_headers(second)
    )

    assert created.status_code == 201
    assert listed.json()["conversations"] == []


async def test_a_message_into_a_foreign_conversation_is_refused(
    client: AsyncClient,
) -> None:
    owner = await register(client, "conv_own")
    intruder = await register(client, "conv_other")
    created = await client.post(
        "/api/v1/chat/conversations", headers=auth_headers(owner)
    )

    response = await client.post(
        "/api/v1/chat/messages",
        headers=auth_headers(intruder),
        json={
            "client_message_id": str(uuid4()),
            "content": "在吗",
            "conversation_id": created.json()["id"],
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "conversation_not_found"


@pytest.mark.parametrize("content", ["在吗"])
async def test_history_can_be_scoped_to_one_conversation(
    client: AsyncClient, content: str
) -> None:
    auth = await register(client, "conv_history")

    created = await client.post(
        "/api/v1/chat/conversations", headers=auth_headers(auth)
    )
    conversation_id = created.json()["id"]

    history = await client.get(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
        params={"conversation_id": conversation_id},
    )

    assert history.status_code == 200
    assert history.json()["messages"] == []
