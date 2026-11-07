"""按患者自己的说法找读数：工具层（真查库）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.runtime import AgentContext
from antang_api.models import GlucoseReading, User
from antang_api.tools.glucose import GlucosePhraseRequest, build_find_glucose_tool

ZONE = ZoneInfo("Asia/Shanghai")


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


def _runtime(user_id: UUID) -> ToolRuntime[AgentContext]:
    return ToolRuntime(
        state={"messages": []},
        context=AgentContext(user_id=user_id, run_id=uuid4(), input_message_count=1),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="find-glucose-call",
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
    await session.commit()
    return user


async def _add_reading(
    session: AsyncSession,
    user: User,
    *,
    days_ago: int,
    hour: int,
    minute: int = 0,
    value_mg_dl: int = 126,
    context: str = "pre_meal",
) -> GlucoseReading:
    # 工具按用户时区（默认 Asia/Shanghai）切窗口，所以夹具也要按本地时间造。
    local_now = datetime.now(ZONE)
    measured_at = (local_now - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    row = GlucoseReading(
        user_id=user.id,
        value_mg_dl=value_mg_dl,
        measured_at=measured_at,
        context=context,
        source="manual",
    )
    session.add(row)
    await session.commit()
    return row


@pytest.mark.asyncio
async def test_the_phrase_picks_one_reading_out_of_many(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "find_glucose")
    await _add_reading(db_session, user, days_ago=10, hour=18, value_mg_dl=108)
    target = await _add_reading(
        db_session, user, days_ago=2, hour=18, value_mg_dl=198, context="pre_meal"
    )
    await _add_reading(db_session, user, days_ago=2, hour=9, value_mg_dl=90)

    tool = _coroutine(build_find_glucose_tool(cast(Any, _Sessions(db_session))))
    result = await tool(
        runtime=_runtime(user.id), request=GlucosePhraseRequest(when="前天晚饭前")
    )

    assert result["understood"] is True
    assert result["count"] == 1
    readings = cast(list[dict[str, object]], result["readings"])
    assert readings[0]["value_mmol"] == "11.0"
    assert readings[0]["measured_at"] == target.measured_at.astimezone(ZONE).strftime(
        "%m-%d %H:%M"
    )


@pytest.mark.asyncio
async def test_a_phrase_we_cannot_read_asks_instead_of_widening(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "find_glucose_vague")
    await _add_reading(db_session, user, days_ago=1, hour=8)

    tool = _coroutine(build_find_glucose_tool(cast(Any, _Sessions(db_session))))
    result = await tool(
        runtime=_runtime(user.id), request=GlucosePhraseRequest(when="那次")
    )

    assert result["understood"] is False
    assert "哪一天" in cast(str, result["note"])
    assert "readings" not in result


@pytest.mark.asyncio
async def test_an_empty_window_is_said_out_loud(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, "find_glucose_empty")
    await _add_reading(db_session, user, days_ago=1, hour=8)

    tool = _coroutine(build_find_glucose_tool(cast(Any, _Sessions(db_session))))
    result = await tool(
        runtime=_runtime(user.id), request=GlucosePhraseRequest(when="昨天睡前")
    )

    assert result["understood"] is True
    assert result["count"] == 0
    assert "不要说成" in cast(str, result["note"])


@pytest.mark.asyncio
async def test_someone_elses_readings_are_not_reachable(
    db_session: AsyncSession,
) -> None:
    mine = await _create_user(db_session, "find_glucose_mine")
    other = await _create_user(db_session, "find_glucose_other")
    await _add_reading(db_session, other, days_ago=1, hour=8)

    tool = _coroutine(build_find_glucose_tool(cast(Any, _Sessions(db_session))))
    result = await tool(
        runtime=_runtime(mine.id), request=GlucosePhraseRequest(when="昨天早上")
    )

    assert result["count"] == 0


@pytest.mark.asyncio
async def test_a_withdrawn_reading_is_not_returned(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, "find_glucose_revoked")
    row = await _add_reading(db_session, user, days_ago=1, hour=8)
    row.revoked_at = datetime.now(timezone.utc)
    await db_session.commit()

    tool = _coroutine(build_find_glucose_tool(cast(Any, _Sessions(db_session))))
    result = await tool(
        runtime=_runtime(user.id), request=GlucosePhraseRequest(when="昨天早上")
    )

    assert result["count"] == 0
