"""运动与睡眠的手工补录：接口三个动作 + 对话里两个工具。

守的是同一件事：只记患者说过的、真实发生过的那一次。时间必须带时区、
不能是未来、不能久到记不清；睡觉那段必须醒来晚于入睡。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.runtime import AgentContext
from antang_api.chat import prepare_chat_run
from antang_api.models import ActivityLog, ProactiveCareSettings, SleepLog, User
from antang_api.tools.activity import build_activity_tools


class _Sessions:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _Sessions:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _coroutine(tool: Any) -> Callable[..., Awaitable[dict[str, object]]]:
    assert isinstance(tool, StructuredTool)
    return cast(Callable[..., Awaitable[dict[str, object]]], tool.coroutine)


def _runtime(user_id: UUID, run_id: UUID) -> ToolRuntime[AgentContext]:
    return ToolRuntime(
        state={"messages": []},
        context=AgentContext(user_id=user_id, run_id=run_id, input_message_count=1),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="activity-call",
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
    session.add(ProactiveCareSettings(user_id=user.id))
    await session.flush()
    return user


async def _headers(client: Any, prefix: str) -> dict[str, str]:
    username = f"{prefix}_{uuid4().hex[:16]}"
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "consent_accepted": True,
            "username": username,
            "password": "correct-password",
        },
    )
    assert registered.status_code == 201, registered.text
    return {"Authorization": f"Bearer {registered.json()['access_token']}"}


async def test_an_activity_is_logged_listed_and_deleted(client: Any) -> None:
    headers = await _headers(client, "activity_route")
    performed_at = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

    created = await client.post(
        "/api/v1/health-profile/activity-logs",
        headers=headers,
        json={
            "performed_at": performed_at,
            "kind": "walk",
            "minutes": 30,
            "intensity": "light",
            "note": "怕低血糖，只在小区里走",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "walk"
    assert body["minutes"] == 30

    listed = await client.get(
        "/api/v1/health-profile/activity-logs", headers=headers
    )
    assert listed.status_code == 200
    assert [row["minutes"] for row in listed.json()] == [30]

    removed = await client.delete(
        f"/api/v1/health-profile/activity-logs/{body['id']}", headers=headers
    )
    assert removed.status_code == 204
    again = await client.get(
        "/api/v1/health-profile/activity-logs", headers=headers
    )
    assert again.json() == []


async def test_a_sleep_log_needs_to_wake_up_after_falling_asleep(client: Any) -> None:
    headers = await _headers(client, "sleep_route")
    woke = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    fell = (datetime.now(UTC) - timedelta(hours=9)).isoformat()

    created = await client.post(
        "/api/v1/health-profile/sleep-logs",
        headers=headers,
        json={"fell_asleep_at": fell, "woke_at": woke},
    )
    assert created.status_code == 201, created.text

    inverted = await client.post(
        "/api/v1/health-profile/sleep-logs",
        headers=headers,
        json={"fell_asleep_at": woke, "woke_at": fell},
    )
    assert inverted.status_code == 422

    # 没有时区的时间不能进库：它会让人以为「23:30」是个确定时刻。
    naive = await client.post(
        "/api/v1/health-profile/sleep-logs",
        headers=headers,
        json={
            "fell_asleep_at": "2026-11-05T23:30:00",
            "woke_at": "2026-11-06T07:00:00",
        },
    )
    assert naive.status_code == 422


async def test_the_tools_write_what_he_actually_said(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, "activity_tool")
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "今天下午出去走了半小时，没敢走远"
    )
    await db_session.commit()
    activity_tool, sleep_tool = build_activity_tools(cast(Any, _Sessions(db_session)))
    runtime = _runtime(user.id, prepared_run.run_id)
    now = datetime.now(UTC)

    recorded = await _coroutine(activity_tool)(
        runtime=runtime,
        kind="walk",
        minutes=30,
        performed_at=(now - timedelta(hours=4)).isoformat(),
        intensity="light",
        note="没敢走远",
    )
    assert recorded["recorded"] is True
    assert recorded["minutes"] == 30

    slept = await _coroutine(sleep_tool)(
        runtime=runtime,
        fell_asleep_at=(now - timedelta(hours=9)).isoformat(),
        woke_at=(now - timedelta(hours=1)).isoformat(),
    )
    assert slept["recorded"] is True
    assert slept["hours"] == 8.0

    activities = list((await db_session.scalars(select(ActivityLog))).all())
    sleeps = list((await db_session.scalars(select(SleepLog))).all())
    assert [row.note for row in activities] == ["没敢走远"]
    assert len(sleeps) == 1


async def test_a_muddled_sleep_is_sent_back_to_ask(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, "activity_bad")
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "昨晚没睡好，翻来覆去"
    )
    await db_session.commit()
    _activity_tool, sleep_tool = build_activity_tools(cast(Any, _Sessions(db_session)))
    runtime = _runtime(user.id, prepared_run.run_id)
    now = datetime.now(UTC)

    with pytest.raises(ValueError):
        await _coroutine(sleep_tool)(
            runtime=runtime,
            fell_asleep_at=(now - timedelta(hours=1)).isoformat(),
            woke_at=(now - timedelta(hours=6)).isoformat(),
        )

    # 未来时间同样不许记：那不是一个发生过的事。
    with pytest.raises(ValueError):
        await _coroutine(sleep_tool)(
            runtime=runtime,
            fell_asleep_at=(now + timedelta(hours=1)).isoformat(),
            woke_at=(now + timedelta(hours=9)).isoformat(),
        )
