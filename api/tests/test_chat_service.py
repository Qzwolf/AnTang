import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from langchain.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api import chat
from antang_api.agents.core import CoreAgentGraph
from antang_api.agents.relative_day import subjective_day
from antang_api.agents.runtime import AgentContext
from antang_api.chat import (
    ActiveAgentRunError,
    ChatRetryError,
    DuplicateClientMessageError,
    extract_agent_result,
    prepare_chat_retry,
    prepare_chat_run,
    stream_chat_run,
)
from antang_api.companion_memory import CompanionMemory
from antang_api.context.builder import ChatContext
from antang_api.context.preparation import PreparedChatContext
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    CarePlan,
    CarePlanStatus,
    GlucoseReading,
    Message,
    MessageAttachment,
    MessageAttachmentKind,
    MessageRole,
    MessageStatus,
    PersonalProfile,
    ProactiveCareSettings,
    SpeechCue,
    User,
)
from antang_api.voice import SpeechCueData, record_speech_cue
from antang_api.schemas.chat import (
    AgentActivityEvent,
    MessageCompletedEvent,
    TextDeltaEvent,
)


class FakeAgent:
    """按顺序返回指定 LangGraph v2 流事件。"""

    def __init__(
        self,
        events: list[dict[str, Any]],
        error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.error = error
        self.config: dict[str, Any] | None = None
        self.context: AgentContext | None = None

    async def astream(
        self,
        _input: dict[str, Any],
        *,
        config: dict[str, Any],
        context: AgentContext,
        stream_mode: list[str],
        version: str,
    ) -> AsyncIterator[dict[str, Any]]:
        assert stream_mode == ["messages", "custom", "values"]
        assert version == "v2"
        self.config = config
        self.context = context

        for event in self.events:
            yield event

        if self.error is not None:
            raise self.error


class FlakyAgent:
    """前几次抛断流错误，之后正常返回事件的 Agent。"""

    def __init__(
        self,
        events: list[dict[str, Any]],
        error: BaseException,
        *,
        failures: int = 1,
        emit_before_failure: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events = events
        self.error = error
        self.failures = failures
        self.emit_before_failure = emit_before_failure or []
        self.attempts = 0

    async def astream(
        self,
        _input: dict[str, Any],
        *,
        config: dict[str, Any],
        context: AgentContext,
        stream_mode: list[str],
        version: str,
    ) -> AsyncIterator[dict[str, Any]]:
        self.attempts += 1

        if self.attempts <= self.failures:
            for event in self.emit_before_failure:
                yield event
            raise self.error

        for event in self.events:
            yield event


def rendered_prompt(agent: FakeAgent) -> str:
    """返回本轮真正交给模型的系统提示词。

    `FakeAgent.context` 与 `AgentContext.rendered_system_prompt` 都可为空，
    测试里每次都写两行 assert 太吵，这里收成一个助手，类型检查器也能跟着收窄。
    """

    assert agent.context is not None
    prompt = agent.context.rendered_system_prompt
    assert prompt is not None
    return prompt


class FakeCompanionMemory:
    def __init__(
        self,
        context: str = "",
        error: Exception | None = None,
    ) -> None:
        self.context = context
        self.error = error

    async def prepare_context(self, *_args: Any) -> str:
        if self.error is not None:
            raise self.error
        return self.context


DISABLED_COMPANION_MEMORY = cast(CompanionMemory, FakeCompanionMemory())


async def create_user(db_session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            PersonalProfile(user_id=user.id),
            ProactiveCareSettings(user_id=user.id),
        ]
    )
    await db_session.flush()
    return user


def prepared_context(message: str = "你好") -> PreparedChatContext:
    return PreparedChatContext(
        context=ChatContext(
            messages=(HumanMessage(content=message),),
            summary_id=None,
            summary_through_message_id=None,
        ),
        input_tokens=12,
        was_compacted=False,
        cleared_tool_result_count=0,
    )


def message_event(
    text: str,
    step: int = 0,
    *,
    visibility: str = "user",
    usage: tuple[int, int] | None = None,
) -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            AIMessageChunk(
                content=text,
                usage_metadata=(
                    None
                    if usage is None
                    else {
                        "input_tokens": usage[0],
                        "output_tokens": usage[1],
                        "total_tokens": usage[0] + usage[1],
                    }
                ),
            ),
            {
                "langgraph_node": "model",
                "langgraph_step": step,
                "stream_visibility": visibility,
            },
        ),
    }


def tool_call_event(text: str = "") -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            AIMessageChunk(
                content=text,
                tool_call_chunks=[
                    {
                        "name": "web_search",
                        "args": '{"query":"低血糖"}',
                        "id": "call-1",
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            ),
            {
                "langgraph_node": "model",
                "langgraph_step": 0,
                "stream_visibility": "user",
            },
        ),
    }


def tool_message_event() -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            ToolMessage(
                content='{"results":[]}',
                tool_call_id="call-1",
                name="web_search",
            ),
            {"langgraph_node": "tools", "langgraph_step": 1},
        ),
    }


def tool_activity_event(
    status: str,
    *,
    tool_name: str = "web_search",
    tool_call_id: str = "call-1",
) -> dict[str, Any]:
    return {
        "type": "custom",
        "data": {
            "event": "tool_activity",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": status,
        },
    }


def thinking_event(secret: str, step: int = 0) -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            AIMessageChunk(
                content=[{"type": "thinking", "thinking": secret, "index": 0}]
            ),
            {
                "langgraph_node": "model",
                "langgraph_step": step,
                "stream_visibility": "user",
            },
        ),
    }


def values_event(content: str) -> dict[str, Any]:
    final_message = AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": 21,
            "output_tokens": 3,
            "total_tokens": 24,
        },
    )
    return {
        "type": "values",
        "data": {
            "messages": [HumanMessage(content="你好"), final_message],
        },
    }


def test_new_tools_reuse_stable_activity_phases() -> None:
    assert chat.TOOL_ACTIVITY_PHASES["read_wearable_data"] == "reading"
    assert chat.TOOL_ACTIVITY_PHASES["search_diabetes_knowledge"] == "reading"
    assert chat.TOOL_ACTIVITY_PHASES["delegate_health_profile"] == "organizing"
    assert chat.TOOL_ACTIVITY_PHASES["manage_care_plan"] == "organizing"
    assert AgentActivityEvent(phase="reading").phase == "reading"


def tool_values_event(content: str, preamble: str = "") -> dict[str, Any]:
    tool_call_message = AIMessage(
        content=preamble,
        tool_calls=[
            {
                "name": "web_search",
                "args": {"query": "低血糖"},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
        usage_metadata={
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
        },
    )
    tool_result = ToolMessage(
        content='{"results":[]}',
        tool_call_id="call-1",
        name="web_search",
    )
    final_message = AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
        },
    )
    return {
        "type": "values",
        "data": {
            "messages": [
                HumanMessage(content="你好"),
                tool_call_message,
                tool_result,
                final_message,
            ],
        },
    }


def fetch_values_event(content: str) -> dict[str, Any]:
    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "web_fetch",
                "args": {"url": "https://example.com/source", "query": "结论"},
                "id": "fetch-call",
                "type": "tool_call",
            }
        ],
        usage_metadata={
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
        },
    )
    tool_result = ToolMessage(
        content=json.dumps(
            {
                "source_id": "S1",
                "title": "资料页",
                "url": "https://example.com/source",
                "content": "资料正文",
            },
            ensure_ascii=False,
        ),
        tool_call_id="fetch-call",
        name="web_fetch",
    )
    final_message = AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
        },
    )
    return {
        "type": "values",
        "data": {
            "messages": [
                HumanMessage(content="你好"),
                tool_call_message,
                tool_result,
                final_message,
            ],
        },
    }


async def test_prepare_chat_run_rejects_duplicate_and_active_run(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "prepare")
    user_id = user.id
    client_message_id = uuid4()

    await prepare_chat_run(
        db_session,
        user_id,
        client_message_id,
        "第一条消息",
    )

    with pytest.raises(DuplicateClientMessageError):
        await prepare_chat_run(
            db_session,
            user_id,
            client_message_id,
            "重复消息",
        )

    with pytest.raises(ActiveAgentRunError):
        await prepare_chat_run(
            db_session,
            user_id,
            uuid4(),
            "并发消息",
        )


async def test_prepare_chat_run_binds_uploaded_attachment(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "attachment")
    attachment = MessageAttachment(
        user_id=user.id,
        kind=MessageAttachmentKind.REPORT,
        filename="血糖报告.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        data=b"%PDF-1.4",
    )
    db_session.add(attachment)
    await db_session.commit()

    prepared = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "",
        attachment.id,
    )

    await db_session.refresh(attachment)
    message = await db_session.get(Message, prepared.user_message_id)
    assert attachment.message_id == prepared.user_message_id
    assert message is not None
    assert message.content == "请帮我解读这份报告。"


async def test_prepare_chat_retry_reuses_the_original_user_message(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "retry")
    original = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "帮我更新体重",
    )
    failed_message = await db_session.get(Message, original.assistant_message_id)
    failed_run = await db_session.get(AgentRun, original.run_id)
    assert failed_message is not None
    assert failed_run is not None
    failed_message.status = MessageStatus.FAILED
    failed_run.status = AgentRunStatus.FAILED
    await db_session.commit()

    retried = await prepare_chat_retry(
        db_session,
        user.id,
        original.assistant_message_id,
    )

    assert retried.user_message_id == original.user_message_id
    assert retried.assistant_message_id != original.assistant_message_id
    assert retried.run_id != original.run_id
    retry_run = await db_session.get(AgentRun, retried.run_id)
    assert retry_run is not None
    assert retry_run.trigger_message_id == original.user_message_id
    user_messages = list(
        await db_session.scalars(
            select(Message).where(
                Message.user_id == user.id,
                Message.role == MessageRole.USER,
            )
        )
    )
    assert [message.id for message in user_messages] == [original.user_message_id]


async def test_prepare_chat_retry_rejects_a_turn_after_the_user_continued(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "retry_continued")
    original = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "第一条消息",
    )
    failed_message = await db_session.get(Message, original.assistant_message_id)
    failed_run = await db_session.get(AgentRun, original.run_id)
    assert failed_message is not None
    assert failed_run is not None
    failed_message.status = MessageStatus.FAILED
    failed_run.status = AgentRunStatus.FAILED
    db_session.add(
        Message(
            client_message_id=uuid4(),
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="我已经继续聊了",
        )
    )
    await db_session.commit()

    with pytest.raises(ChatRetryError) as error:
        await prepare_chat_retry(
            db_session,
            user.id,
            original.assistant_message_id,
        )

    assert error.value.code == "chat_retry_not_allowed"


async def test_prepare_chat_retry_rejects_when_a_newer_answer_exists(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "retry_completed")
    original = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "需要重试",
    )
    failed_message = await db_session.get(Message, original.assistant_message_id)
    failed_run = await db_session.get(AgentRun, original.run_id)
    assert failed_message is not None
    assert failed_run is not None
    failed_message.status = MessageStatus.FAILED
    failed_run.status = AgentRunStatus.FAILED
    await db_session.commit()
    retried = await prepare_chat_retry(
        db_session,
        user.id,
        original.assistant_message_id,
    )
    retry_message = await db_session.get(Message, retried.assistant_message_id)
    retry_run = await db_session.get(AgentRun, retried.run_id)
    assert retry_message is not None
    assert retry_run is not None
    retry_message.status = MessageStatus.FAILED
    retry_run.status = AgentRunStatus.FAILED
    await db_session.commit()

    with pytest.raises(ChatRetryError) as error:
        await prepare_chat_retry(
            db_session,
            user.id,
            original.assistant_message_id,
        )

    assert error.value.code == "chat_retry_not_allowed"


def test_extract_agent_result_requires_complete_usage() -> None:
    first = AIMessage(
        content="中间消息",
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
        },
    )
    final = AIMessage(
        content="最终回答",
        usage_metadata={
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
        },
    )

    result = extract_agent_result(
        {"messages": [HumanMessage(content="问题"), first, final]},
        input_message_count=1,
    )

    assert result[0] == "中间消息\n\n最终回答"
    assert [turn.content for turn in result[1]] == ["中间消息", "最终回答"]
    assert result[2:] == (22, 6)

    with pytest.raises(RuntimeError, match="usage_metadata"):
        extract_agent_result(
            {
                "messages": [
                    HumanMessage(content="问题"),
                    AIMessage(content="缺少用量"),
                ]
            },
            input_message_count=1,
        )

    with pytest.raises(RuntimeError, match="空回答"):
        extract_agent_result(
            {
                "messages": [
                    HumanMessage(content="问题"),
                    AIMessage(
                        content="   ",
                        usage_metadata={
                            "input_tokens": 10,
                            "output_tokens": 1,
                            "total_tokens": 11,
                        },
                    ),
                ]
            },
            input_message_count=1,
        )


async def test_stream_chat_run_completes_and_persists_result(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_success")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "你好",
    )
    plan = CarePlan(
        user_id=user.id,
        summary="晚饭后散步二十分钟",
        status=CarePlanStatus.ACTIVE,
        follow_up_at=datetime.now(timezone.utc) + timedelta(days=1),
        revision=1,
        created_by_message_id=prepared_run.user_message_id,
        last_changed_by_message_id=prepared_run.user_message_id,
    )
    db_session.add(plan)
    await db_session.flush()
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [
            message_event("收到"),
            message_event("。"),
            values_event("收到。"),
        ]
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert [event.type for event in events] == [
        "message_started",
        "agent_activity",
        "text_delta",
        "text_delta",
        "message_completed",
    ]
    assert fake_agent.config is not None
    assert fake_agent.config["configurable"]["thread_id"] == str(prepared_run.run_id)
    assert fake_agent.config["metadata"] == {
        "run_id": str(prepared_run.run_id),
        "user_id": str(user.id),
        "stream_visibility": "user",
    }
    assert fake_agent.context is not None
    assert fake_agent.context.user_id == user.id
    assert fake_agent.context.run_id == prepared_run.run_id
    assert fake_agent.context.input_message_count == 1
    prompt = rendered_prompt(fake_agent)
    assert "[当前健康档案]" in prompt
    assert "[当前主动关怀计划]" in prompt
    assert str(plan.id) in prompt
    assert "晚饭后散步二十分钟" in prompt

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.COMPLETED
    assert message.content == "收到。"
    assert run is not None
    assert run.status == AgentRunStatus.COMPLETED
    assert run.input_tokens == 21
    assert run.output_tokens == 3


async def test_stream_chat_run_tells_the_model_which_day_it_is(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提示词里的「今天」按用户的睡眠日算，不是日历日。"""

    user = await create_user(db_session, "day_context")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "今天几号")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("收到"), values_event("收到。")])
    # 算前后两次，避免测试刚好跨过凌晨 4 点的边界。
    day_before = subjective_day(datetime.now(timezone.utc))

    async for _ in stream_chat_run(
        session=db_session,
        model=cast("BaseChatModel", object()),
        agent=cast("CoreAgentGraph", fake_agent),
        tools=(),
        companion_memory=DISABLED_COMPANION_MEMORY,
        user_id=user.id,
        prepared_run=prepared_run,
    ):
        pass

    day_after = subjective_day(datetime.now(timezone.utc))
    rendered = rendered_prompt(fake_agent)
    assert "[当前时间]" in rendered
    assert "用户口中的「今天」" in rendered
    assert str(day_before) in rendered or str(day_after) in rendered


async def test_stream_chat_run_never_forwards_internal_subagent_text(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "internal_hidden")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "更新我的健康档案",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context("更新我的健康档案")),
    )
    fake_agent = FakeAgent(
        [
            message_event("不应让用户看到的子 Agent 输出", visibility="internal"),
            message_event("已经整理好了。", step=2),
            values_event("已经整理好了。"),
        ]
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    text = "".join(event.delta for event in events if isinstance(event, TextDeltaEvent))
    assert text == "已经整理好了。"
    assert "子 Agent" not in text


async def test_stream_chat_run_answers_even_when_companion_memory_fails(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """记忆服务写不进去时，这一轮照常回答，只是不带记忆。"""

    user = await create_user(db_session, "memory_failure")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "继续聊聊",
    )
    prepare_context_mock = AsyncMock(return_value=prepared_context())
    monkeypatch.setattr(chat, "prepare_chat_context", prepare_context_mock)
    fake_agent = FakeAgent([message_event("在的。"), values_event("在的。")])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=cast(
                CompanionMemory,
                FakeCompanionMemory(error=RuntimeError("Hindsight recall failed")),
            ),
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    completed = cast("MessageCompletedEvent", events[-1])
    assert completed.type == "message_completed"
    text = "".join(event.delta for event in events if isinstance(event, TextDeltaEvent))
    assert text == "在的。"
    prepare_context_mock.assert_awaited()
    assert "[相关陪伴记忆]" not in rendered_prompt(fake_agent)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert run is not None
    assert run.status == AgentRunStatus.COMPLETED


async def test_stream_chat_run_validates_and_persists_cited_sources(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_sources")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "帮我查资料",
    )
    db_session.add_all(
        [
            AgentToolCall(
                agent_run_id=prepared_run.run_id,
                tool_call_id="search-call",
                tool_name="web_search",
                model_turn_index=1,
                tool_call_index=1,
                arguments={"query": "测试资料"},
                result=json.dumps(
                    {
                        "results": [
                            {
                                "title": "资料页",
                                "url": "https://example.com/source",
                                "snippet": "摘要",
                                "score": 0.95,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                status=AgentToolCallStatus.COMPLETED,
            ),
            AgentToolCall(
                agent_run_id=prepared_run.run_id,
                tool_call_id="fetch-call",
                tool_name="web_fetch",
                model_turn_index=2,
                tool_call_index=1,
                arguments={"url": "https://example.com/source", "query": "结论"},
                result=json.dumps(
                    {
                        "source_id": "S1",
                        "title": "资料页",
                        "url": "https://example.com/source",
                        "content": "资料正文",
                    },
                    ensure_ascii=False,
                ),
                status=AgentToolCallStatus.COMPLETED,
            ),
        ]
    )
    await db_session.commit()
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context("帮我查资料")),
    )
    answer = "这个结论来自刚才读取的网页。[S1]"
    fake_agent = FakeAgent([message_event(answer), fetch_values_event(answer)])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    completed = cast("MessageCompletedEvent", events[-1])
    assert [source.model_dump() for source in completed.sources] == [
        {
            "source_id": "S1",
            "title": "资料页",
            "url": "https://example.com/source",
        }
    ]
    db_session.expire_all()
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.sources == [
        {
            "source_id": "S1",
            "title": "资料页",
            "url": "https://example.com/source",
        }
    ]
    assert message.status == MessageStatus.COMPLETED
    assert run is not None
    assert run.status == AgentRunStatus.COMPLETED


async def test_stream_chat_run_rejects_uncited_fetch_and_keeps_sources_empty(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_uncited")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "读取网页",
    )
    db_session.add(
        AgentToolCall(
            agent_run_id=prepared_run.run_id,
            tool_call_id="fetch-call",
            tool_name="web_fetch",
            model_turn_index=1,
            tool_call_index=1,
            arguments={"url": "https://example.com/source", "query": "结论"},
            result=json.dumps(
                {
                    "source_id": "S1",
                    "title": "资料页",
                    "url": "https://example.com/source",
                    "content": "资料正文",
                },
                ensure_ascii=False,
            ),
            status=AgentToolCallStatus.COMPLETED,
        )
    )
    await db_session.commit()
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context("读取网页")),
    )
    answer = "这段回答忘记引用来源。"
    fake_agent = FakeAgent([message_event(answer), fetch_values_event(answer)])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_failed"
    db_session.expire_all()
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.FAILED
    assert message.sources == []
    assert run is not None
    assert run.status == AgentRunStatus.FAILED
    assert run.error_type == "CitationValidationError"


async def test_stream_chat_run_streams_tool_preamble_and_activity(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具前说明和最终回答都可见，工具细节只转成活动阶段。"""

    user = await create_user(db_session, "stream_tool_loop")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "帮我查一下",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [
            tool_call_event("我先帮你查一下。"),
            tool_activity_event("started"),
            tool_message_event(),
            tool_activity_event("completed"),
            thinking_event("不应暴露的内部推理", step=2),
            message_event("查到了。", step=2),
            tool_values_event("查到了。", preamble="我先帮你查一下。"),
        ]
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert [event.type for event in events] == [
        "message_started",
        "agent_activity",
        "text_delta",
        "agent_activity",
        "agent_activity",
        "text_delta",
        "text_delta",
        "message_completed",
    ]
    activities = [
        cast("AgentActivityEvent", event).phase
        for event in events
        if event.type == "agent_activity"
    ]
    assert activities == ["thinking", "searching", "organizing"]
    deltas = [
        cast("TextDeltaEvent", event).delta
        for event in events
        if event.type == "text_delta"
    ]
    assert deltas == ["我先帮你查一下。", "\n\n", "查到了。"]
    assert "不应暴露的内部推理" not in str(events)
    completed = cast("MessageCompletedEvent", events[-1])
    assert completed.input_tokens == 50
    assert completed.output_tokens == 10

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    assert message is not None
    assert message.content == "我先帮你查一下。\n\n查到了。"


async def test_stream_chat_run_marks_mismatched_output_failed_without_logging_error_text(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_failed")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "包含敏感正文的用户消息",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("部分文字"), values_event("不同的最终文字")])
    running_tool = AgentToolCall(
        agent_run_id=prepared_run.run_id,
        tool_call_id="running-before-failure",
        tool_name="web_search",
        model_turn_index=1,
        tool_call_index=1,
        arguments={"query": "测试"},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(running_tool)
    await db_session.commit()
    captured_logs: list[str] = []
    sink_id = logger.add(
        captured_logs.append,
        format="{message}|{extra}",
        diagnose=False,
        catch=False,
    )

    try:
        events = [
            event
            async for event in stream_chat_run(
                session=db_session,
                model=cast("BaseChatModel", object()),
                agent=cast("CoreAgentGraph", fake_agent),
                tools=(),
                companion_memory=DISABLED_COMPANION_MEMORY,
                user_id=user.id,
                prepared_run=prepared_run,
            )
        ]
    finally:
        logger.remove(sink_id)

    assert events[-1].type == "message_failed"
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.FAILED
    assert message.content == "部分文字"
    assert run is not None
    assert run.status == AgentRunStatus.FAILED
    assert run.error_type == "RuntimeError"
    await db_session.refresh(running_tool)
    assert running_tool.status == AgentToolCallStatus.FAILED
    assert running_tool.error_type == "RuntimeError"
    assert running_tool.finished_at is not None

    logs = "".join(captured_logs)
    assert "包含敏感正文的用户消息" not in logs
    assert "部分文字" not in logs
    assert "不同的最终文字" not in logs


async def test_stream_chat_run_marks_client_cancellation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_cancelled")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "测试取消",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [message_event("已经生成")],
        error=asyncio.CancelledError(),
    )
    running_tool = AgentToolCall(
        agent_run_id=prepared_run.run_id,
        tool_call_id="running-before-cancel",
        tool_name="web_search",
        model_turn_index=1,
        tool_call_index=1,
        arguments={"query": "测试"},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(running_tool)
    await db_session.commit()

    with pytest.raises(asyncio.CancelledError):
        _ = [
            event
            async for event in stream_chat_run(
                session=db_session,
                model=cast("BaseChatModel", object()),
                agent=cast("CoreAgentGraph", fake_agent),
                tools=(),
                companion_memory=DISABLED_COMPANION_MEMORY,
                user_id=user.id,
                prepared_run=prepared_run,
            )
        ]

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.CANCELLED
    assert message.content == "已经生成"
    assert run is not None
    assert run.status == AgentRunStatus.CANCELLED
    await db_session.refresh(running_tool)
    assert running_tool.status == AgentToolCallStatus.CANCELLED
    assert running_tool.error_type == "CancelledError"
    assert running_tool.finished_at is not None


async def test_stream_chat_run_marks_closed_generator_cancelled(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """StreamingResponse 主动关闭生成器时也要保存取消状态。"""

    user = await create_user(db_session, "stream_closed")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "测试关闭响应流",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("已经发送的部分")])
    stream = cast(
        "AsyncGenerator[Any, None]",
        stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        ),
    )

    assert (await anext(stream)).type == "message_started"
    assert (await anext(stream)).type == "agent_activity"
    assert (await anext(stream)).type == "text_delta"
    await stream.aclose()

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.CANCELLED
    assert message.content == "已经发送的部分"
    assert run is not None
    assert run.status == AgentRunStatus.CANCELLED


async def _low_reading(
    db_session: AsyncSession,
    user_id: UUID,
    *,
    minutes_ago: int = 30,
    source: str = "manual",
    reviewed_at: datetime | None = None,
) -> GlucoseReading:
    """在窗口内造一条偏低读数，默认还没复盘。"""

    reading = GlucoseReading(
        user_id=user_id,
        value_mg_dl=58,
        measured_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        context="other",
        source=source,
        reviewed_at=reviewed_at,
    )
    db_session.add(reading)
    await db_session.flush()
    return reading


async def test_stream_chat_run_opens_the_hypo_review_once(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24 小时内有未复盘的偏低时，这一轮要带上复盘话题，答完才打标记。"""

    user = await create_user(db_session, "hypo_review_due")
    reading = await _low_reading(db_session, user.id, source="manual")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "嗯")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    answer = "你说的那次低血糖，后来补了什么、有没有再测一次？"
    fake_agent = FakeAgent([message_event(answer), values_event(answer)])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_completed"
    assert fake_agent.context is not None
    prompt = rendered_prompt(fake_agent)
    assert "[低血糖之后的复盘]" in prompt
    assert "你说的那次" in prompt

    reviewed = await db_session.get(GlucoseReading, reading.id)
    assert reviewed is not None
    assert reviewed.reviewed_at is not None


async def test_stream_chat_run_sees_the_glucose_the_patient_wrote_down(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """患者自己记的读数要进本轮提示词，否则模型只能从文字里猜血糖。"""

    user = await create_user(db_session, "glucose_in_prompt")
    await _low_reading(db_session, user.id, source="manual")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "今天有点飘")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("在的。"), values_event("在的。")])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_completed"
    prompt = rendered_prompt(fake_agent)
    assert "[血糖记录：患者自己记的" in prompt
    assert "不是连续监测" in prompt


async def test_stream_chat_run_does_not_ask_about_a_reviewed_reading(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """问过的那次不再问第二遍。"""

    user = await create_user(db_session, "hypo_review_done")
    await _low_reading(
        db_session,
        user.id,
        reviewed_at=datetime.now(timezone.utc),
    )
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "你好")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("收到。"), values_event("收到。")])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_completed"
    assert fake_agent.context is not None
    assert "[低血糖之后的复盘]" not in rendered_prompt(fake_agent)


async def test_failed_run_does_not_consume_the_hypo_review(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败的轮次用户没收到问题，下一次还得再问。"""

    user = await create_user(db_session, "hypo_review_failed")
    reading = await _low_reading(db_session, user.id)
    reading_id = reading.id
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "嗯")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("收到"), values_event("另一句话")])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_failed"
    # 失败路径 rollback 过会话，ORM 对象已过期；直接查列，别在异步上下文里
    # 触发懒加载。
    reviewed_at = await db_session.scalar(
        select(GlucoseReading.reviewed_at).where(GlucoseReading.id == reading_id)
    )
    assert reviewed_at is None


async def _speech_cue(
    db_session: AsyncSession,
    user_id: UUID,
    *,
    minutes_ago: int = 1,
) -> SpeechCue:
    cue = SpeechCueData(pace="fast", energy="low", breathing="audible", confidence=0.6)
    row = await record_speech_cue(
        db_session,
        user_id=user_id,
        cue=cue,
        model_name="gemini-3.8-flash",
        now=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    await db_session.flush()
    return row


async def test_stream_chat_run_tells_the_model_how_it_was_said(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """说过的话带着听感进来，但只是提示，不是本轮任务。"""

    user = await create_user(db_session, "cue_due")
    cue = await _speech_cue(db_session, user.id)
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "我睡不着")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("我在。"), values_event("我在。")])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_completed"
    assert fake_agent.context is not None
    prompt = rendered_prompt(fake_agent)
    assert "[语音状态]" in prompt
    assert "语速偏快" in prompt
    # 用完即弃：这一轮之后它不该再影响下一句。
    consumed = await db_session.get(SpeechCue, cue.id)
    assert consumed is not None
    assert consumed.consumed_at is not None


async def test_stream_chat_run_ignores_a_stale_cue(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """十几分钟前那句话的语气，不该被带进新话题。"""

    user = await create_user(db_session, "cue_stale")
    await _speech_cue(db_session, user.id, minutes_ago=30)
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "今天吃什么")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("收到。"), values_event("收到。")])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_completed"
    assert fake_agent.context is not None
    assert "[语音状态]" not in rendered_prompt(fake_agent)


async def test_a_failed_run_leaves_the_cue_for_next_time(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答没送到用户手里，这条听感就还没算用掉。"""

    user = await create_user(db_session, "cue_failed")
    cue = await _speech_cue(db_session, user.id)
    cue_id = cue.id
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "我有点慌")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("收到"), values_event("另一句话")])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_failed"
    consumed_at = await db_session.scalar(
        select(SpeechCue.consumed_at).where(SpeechCue.id == cue_id)
    )
    assert consumed_at is None


async def test_a_broken_stream_is_retried_before_the_user_sees_anything(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上游在开口前就断开时，患者不该看到「回复失败」。"""

    user = await create_user(db_session, "retry_ok")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "在吗")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FlakyAgent(
        [message_event("在的。"), values_event("在的。")],
        httpx.RemoteProtocolError("peer closed connection"),
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert fake_agent.attempts == 2
    assert events[-1].type == "message_completed"
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert run is not None
    assert run.status == AgentRunStatus.COMPLETED


async def test_a_stream_that_broke_after_speaking_is_not_retried(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已经发出去的字不能重发一遍，宁可这一轮失败让用户重试。"""

    user = await create_user(db_session, "retry_partial")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "在吗")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FlakyAgent(
        [message_event("在的。"), values_event("在的。")],
        httpx.RemoteProtocolError("peer closed connection"),
        emit_before_failure=[message_event("我在")],
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert fake_agent.attempts == 1
    assert events[-1].type == "message_failed"


async def test_a_contract_error_is_not_retried(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地契约错误重试也是同一个错，只会让用户多等一次。"""

    user = await create_user(db_session, "retry_contract")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "在吗")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FlakyAgent(
        [message_event("在的。"), values_event("在的。")],
        RuntimeError("Core Agent 返回了空回答"),
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert fake_agent.attempts == 1
    assert events[-1].type == "message_failed"


async def test_an_exhausted_quota_is_visible_to_the_operator(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中转站余额不足时回 403，患者一个字都收不到；日志要直接说明是余额问题。"""

    user = await create_user(db_session, "quota_out")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "在吗")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FlakyAgent(
        [message_event("在的。"), values_event("在的。")],
        RuntimeError("Error code: 403 - {'code': 'insufficient_user_quota'}"),
    )
    logged: list[str] = []
    sink = chat.logger.add(logged.append, level="WARNING", format="{message}")

    try:
        events = [
            event
            async for event in stream_chat_run(
                session=db_session,
                model=cast("BaseChatModel", object()),
                agent=cast("CoreAgentGraph", fake_agent),
                tools=(),
                companion_memory=DISABLED_COMPANION_MEMORY,
                user_id=user.id,
                prepared_run=prepared_run,
            )
        ]
    finally:
        chat.logger.remove(sink)

    assert events[-1].type == "message_failed"
    assert any("chat_run_out_of_quota" in str(entry) for entry in logged)


async def test_token_usage_is_not_inflated_by_repeated_chunk_snapshots(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中转站在每个分片上重复累计用量，只该记一次真实值。"""

    user = await create_user(db_session, "usage_snapshot")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "在吗")
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [
            message_event("在", step=0, usage=(6_000, 3)),
            message_event("的。", step=0, usage=(6_200, 9)),
            values_event("在的。"),
        ]
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("BaseChatModel", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            companion_memory=DISABLED_COMPANION_MEMORY,
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    completed = events[-1]
    assert completed.type == "message_completed"
    assert completed.input_tokens == 6_200
    assert completed.output_tokens == 9
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert run is not None
    assert run.input_tokens == 6_200
    assert run.output_tokens == 9
