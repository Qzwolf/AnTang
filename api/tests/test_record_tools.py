"""记录工具：时间口径、必填项，以及它们确实注册进了 Agent 的工具集。"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Self, cast
from uuid import UUID, uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.runtime import AgentContext
from antang_api.chat import TOOL_ACTIVITY_PHASES, prepare_chat_run
from antang_api.models import (
    MealLog,
    MedicationLog,
    PersonalProfile,
    ProactiveCareSettings,
    User,
)
from antang_api.tools.records import (
    RECORD_MEAL_TOOL_NAME,
    RECORD_MEDICATION_TOOL_NAME,
    MealRequest,
    MedicationRequest,
    _check_event_time,
    build_record_tools,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


class _KeepOpen:
    """让工具复用测试事务里的会话，写完能在同一个事务里读回来。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def _create_user(session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    session.add(user)
    await session.flush()
    session.add_all(
        [
            PersonalProfile(user_id=user.id),
            ProactiveCareSettings(user_id=user.id),
        ]
    )
    await session.flush()
    return user


class _ToolLoopFakeModel(FakeMessagesListChatModel):
    """让 create_agent 能给它绑定工具。"""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Self:
        del tools, tool_choice, kwargs
        return self


def _runtime(user_id: UUID, run_id: UUID) -> ToolRuntime[AgentContext]:
    return ToolRuntime(
        state={"messages": []},
        context=AgentContext(
            user_id=user_id,
            run_id=run_id,
            input_message_count=1,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="record-call",
        store=None,
        tools=[],
    )


def test_both_recording_tools_are_registered() -> None:
    assert TOOL_ACTIVITY_PHASES[RECORD_MEAL_TOOL_NAME] == "organizing"
    assert TOOL_ACTIVITY_PHASES[RECORD_MEDICATION_TOOL_NAME] == "organizing"

    tools = build_record_tools()
    assert [tool.name for tool in tools] == [
        RECORD_MEAL_TOOL_NAME,
        RECORD_MEDICATION_TOOL_NAME,
    ]


def test_a_time_that_has_not_happened_yet_is_refused() -> None:
    with pytest.raises(ValueError, match="还没到"):
        _check_event_time(NOW + timedelta(minutes=3), NOW)
    # 手机时钟会差一两分钟，两分钟以内放行
    assert _check_event_time(NOW + timedelta(minutes=1), NOW) == NOW + timedelta(
        minutes=1
    )


def test_a_backfill_older_than_a_month_is_refused() -> None:
    with pytest.raises(ValueError, match="最近 30 天"):
        _check_event_time(NOW - timedelta(days=31), NOW)
    assert _check_event_time(NOW - timedelta(days=29), NOW) == NOW - timedelta(days=29)


def test_a_time_without_a_timezone_is_refused() -> None:
    with pytest.raises(ValueError, match="带时区"):
        _check_event_time(datetime(2026, 10, 24, 10, 0), NOW)


def test_an_afternoon_record_of_a_morning_dose_keeps_the_morning_time() -> None:
    morning = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)

    request = MedicationRequest(name="门冬胰岛素", dose_text="6 单位", taken_at=morning)

    assert request.taken_at == morning


def test_a_medication_without_a_dose_is_refused() -> None:
    with pytest.raises(ValidationError):
        MedicationRequest(name="二甲双胍", taken_at=NOW)  # pyright: ignore[reportCallIssue]


def test_a_meal_may_omit_the_carbs_the_patient_did_not_mention() -> None:
    request = MealRequest(meal_type="lunch", eaten_at=NOW, description="一碗面条")

    assert request.carbs_grams is None


def test_meal_carbs_outside_a_plausible_range_are_refused() -> None:
    with pytest.raises(ValidationError):
        MealRequest(meal_type="lunch", eaten_at=NOW, description="火锅", carbs_grams=500)

async def test_a_vague_medication_name_has_to_be_asked_about(
    db_session: AsyncSession,
) -> None:
    """「吃了点感冒药」不是药名：工具拒绝，模型只能回头问是哪一种。"""

    user = await _create_user(db_session, "vague_medication")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "吃了点感冒药")
    await db_session.commit()

    tools = build_record_tools(sessions=cast(Any, lambda: _KeepOpen(db_session)))
    _meal, medication = tools
    assert isinstance(medication, StructuredTool)

    with pytest.raises(ValueError, match="不是具体药名"):
        await cast("Callable[..., Awaitable[object]]", medication.coroutine)(
            runtime=_runtime(user.id, prepared_run.run_id),
            name="感冒药",
            dose_text="两片",
            taken_at=datetime.now(UTC).isoformat(),
        )

    rows = await db_session.execute(
        select(MedicationLog).where(MedicationLog.user_id == user.id)
    )
    assert list(rows.scalars()) == []


async def test_a_meal_without_a_portion_has_to_be_asked_about(
    db_session: AsyncSession,
) -> None:
    """「我吃了凉皮」还差一句「吃了多少」：记下来之前必须先问。"""

    user = await _create_user(db_session, "vague_meal")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "我吃了凉皮")
    await db_session.commit()

    meal, _medication = build_record_tools(sessions=cast(Any, lambda: _KeepOpen(db_session)))
    assert isinstance(meal, StructuredTool)

    with pytest.raises(ValueError, match="吃了多少"):
        await cast("Callable[..., Awaitable[object]]", meal.coroutine)(
            runtime=_runtime(user.id, prepared_run.run_id),
            meal_type="dinner",
            eaten_at=datetime.now(UTC).isoformat(),
            description="凉皮",
        )

    rows = await db_session.execute(select(MealLog).where(MealLog.user_id == user.id))
    assert list(rows.scalars()) == []


async def test_a_meal_that_already_says_how_much_is_recorded(
    db_session: AsyncSession,
) -> None:
    """「一碗凉皮」份量已经在话里了，不用再追问一遍。"""

    user = await _create_user(db_session, "portion_meal")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "吃了一碗凉皮")
    await db_session.commit()

    meal, _medication = build_record_tools(sessions=cast(Any, lambda: _KeepOpen(db_session)))
    assert isinstance(meal, StructuredTool)

    result = await cast("Callable[..., Awaitable[object]]", meal.coroutine)(
        runtime=_runtime(user.id, prepared_run.run_id),
        meal_type="dinner",
        eaten_at=datetime.now(UTC).isoformat(),
        description="凉皮",
        portion_hint="一碗",
    )

    assert cast("dict[str, object]", result)["recorded"] is True
    rows = await db_session.execute(select(MealLog).where(MealLog.user_id == user.id))
    assert [row.description for row in rows.scalars()] == ["凉皮（一碗）"]


def test_an_empty_dose_is_refused_so_the_model_has_to_ask() -> None:
    """照片里只看得见药名、看不见剂量时：工具收不下这条，模型只能回去问。"""

    with pytest.raises(ValidationError):
        MedicationRequest(name="门冬胰岛素", dose_text="", taken_at=NOW)


def test_the_tool_schema_is_flat_so_the_model_cannot_nest_it_wrong() -> None:
    """线上踩过：模型把字段平铺着传，框架报 "request: Field required"，工具没执行。

    参数是一个 request 对象时，模型只要少套一层就全军覆没；扁平之后没有这一层，
    模型怎么传都是字段本身。
    """

    meal, medication = build_record_tools()

    assert "request" not in meal.args
    assert "request" not in medication.args
    assert set(medication.args) == {"name", "dose_text", "taken_at", "kind", "note"}
    assert set(meal.args) == {
        "meal_type",
        "eaten_at",
        "description",
        "carbs_grams",
        "portion_hint",
    }


async def test_flat_arguments_actually_reach_the_database(
    db_session: AsyncSession,
) -> None:
    """模型平铺着传字段时，这一条必须真的写进 medication_logs。

    线上那次失败就死在这一步：工具没执行，回复照样说「已经帮你记下来了」。
    所以这里验证的是「真的落库」。
    """

    from langchain_core.tools.structured import StructuredTool
    from sqlalchemy import select

    user = await _create_user(db_session, "flat_args")
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "吃了连花清瘟胶囊 2 颗"
    )
    await db_session.commit()

    def sessions() -> _KeepOpen:
        return _KeepOpen(db_session)

    # 工具只用到 `async with sessions() as session` 这一件事，
    # 这里给的是测试事务里的会话，不是真的 sessionmaker。
    _meal, medication = build_record_tools(sessions=cast(Any, sessions))
    assert isinstance(medication, StructuredTool)
    assert medication.coroutine is not None

    result = await cast("Callable[..., Awaitable[object]]", medication.coroutine)(
        name="连花清瘟胶囊",
        dose_text="2 颗",
        kind="other",
        taken_at=datetime.now(UTC).isoformat(),
        runtime=_runtime(user.id, prepared_run.run_id),
    )

    assert cast("dict[str, object]", result)["recorded"] is True
    rows = await db_session.execute(
        select(MedicationLog).where(MedicationLog.user_id == user.id)
    )
    assert [row.name for row in rows.scalars()] == ["连花清瘟胶囊"]


async def test_the_agent_loop_can_invoke_the_record_tool(db_session: AsyncSession) -> None:
    """真的让 Agent 跑一轮工具调用——线上就是这一步失败的。

    之前的写法 `record_meal(runtime, **kwargs)` 在 ToolNode 里拿不到注入的 runtime，
    每次调用都是 `missing 1 required positional argument: 'runtime'`；
    直接调 coroutine 测不出来，必须走 Agent 的图。
    """

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from sqlalchemy import select

    from antang_api.agents.core import SYSTEM_PROMPT, build_core_agent

    user = await _create_user(db_session, "agent_meal")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "刚吃了凉皮")
    await db_session.commit()

    meal, _medication = build_record_tools(
        sessions=cast(Any, lambda: _KeepOpen(db_session))
    )
    model = _ToolLoopFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": RECORD_MEAL_TOOL_NAME,
                        "args": {
                            "meal_type": "dinner",
                            "eaten_at": datetime.now(UTC).isoformat(),
                            "description": "凉皮",
                            "portion_hint": "一碗",
                        },
                        "id": "meal-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="记下了：今晚吃了凉皮。"),
        ]
    )
    agent = build_core_agent(model, InMemorySaver(), tools=(meal,), middleware=())
    state = await agent.ainvoke(
        {"messages": [HumanMessage(content="我吃了凉皮")]},
        config={"configurable": {"thread_id": str(prepared_run.run_id)}},
        context=AgentContext(
            user_id=user.id,
            run_id=prepared_run.run_id,
            input_message_count=1,
            rendered_system_prompt=SYSTEM_PROMPT,
        ),
    )

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status != "error", str(tool_messages[0].content)

    rows = await db_session.execute(select(MealLog).where(MealLog.user_id == user.id))
    assert [row.description for row in rows.scalars()] == ["凉皮（一碗）"]


