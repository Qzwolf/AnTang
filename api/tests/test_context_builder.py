import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.context.builder import (
    HISTORICAL_WEB_RESULT_NOTICE,
    SUMMARY_PREFIX,
    build_chat_context,
)
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    ConversationSummary,
    Message,
    MessageAttachment,
    MessageAttachmentKind,
    MessageRole,
    MessageStatus,
    User,
)


async def test_build_chat_context_without_summary(
    db_session: AsyncSession,
) -> None:
    """首次聊天会读取当前消息之前的全部已完成原始消息。"""

    username = f"context_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    old_user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="昨天晚上我有点担心低血糖。",
    )
    db_session.add(old_user_message)
    await db_session.flush()

    old_assistant_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="当时发生了什么？",
    )
    db_session.add(old_assistant_message)
    await db_session.flush()

    current_user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="我睡前一直反复测血糖。",
    )
    db_session.add(current_user_message)
    await db_session.flush()

    generating_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.GENERATING,
        content="",
    )
    db_session.add(generating_message)
    await db_session.flush()

    context = await build_chat_context(
        db_session,
        user.id,
        current_user_message.id,
    )

    assert context.summary_id is None
    assert context.summary_through_message_id is None
    assert [type(message) for message in context.messages] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert [message.content for message in context.messages] == [
        "昨天晚上我有点担心低血糖。",
        "当时发生了什么？",
        "我睡前一直反复测血糖。",
    ]


async def test_build_chat_context_includes_pdf_for_the_model(
    db_session: AsyncSession,
) -> None:
    username = f"pdf_context_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    current_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="请解读这份报告",
    )
    db_session.add(current_message)
    await db_session.flush()
    db_session.add(
        MessageAttachment(
            user_id=user.id,
            message_id=current_message.id,
            kind=MessageAttachmentKind.REPORT,
            filename="报告.pdf",
            mime_type="application/pdf",
            size_bytes=8,
            data=b"%PDF-1.4",
        )
    )
    await db_session.flush()

    context = await build_chat_context(db_session, user.id, current_message.id)

    content = context.messages[-1].content
    assert isinstance(content, list)
    assert isinstance(content[0], dict)
    assert content[0]["type"] == "file"
    assert content[0]["file"]["filename"] == "报告.pdf"
    assert content[0]["file"]["file_data"].startswith(
        "data:application/pdf;base64,"
    )
    assert content[1] == {"type": "text", "text": "请解读这份报告"}


async def _pdf_message(db_session: AsyncSession, data: bytes) -> tuple[User, Message]:
    username = f"pdf_text_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="这份指南怎么说的",
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        MessageAttachment(
            user_id=user.id,
            message_id=message.id,
            kind=MessageAttachmentKind.REPORT,
            filename="指南.pdf",
            mime_type="application/pdf",
            size_bytes=len(data),
            data=data,
        )
    )
    await db_session.flush()
    return user, message


async def test_a_text_rich_pdf_goes_in_as_text_not_as_base64(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文字层够厚的 PDF 只发文字，省掉整份文件的 base64。"""

    monkeypatch.setattr(
        "antang_api.health_profile.report_parse.extract_pdf_text",
        lambda _data: "目标范围内时间应不低于百分之七十。" * 200,
    )
    user, message = await _pdf_message(db_session, b"%PDF-1.4 fake")

    context = await build_chat_context(db_session, user.id, message.id)

    content = context.messages[-1].content
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    assert first["type"] == "text"
    text = first["text"]
    assert isinstance(text, str)
    assert "文字" in text
    assert "目标范围内时间应不低于百分之七十" in text
    assert content[1] == {"type": "text", "text": "这份指南怎么说的"}


async def test_a_chart_heavy_pdf_still_goes_in_whole(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文字层太薄的报告（CGM 评估报告那类）还是把原文件交给模型看图。"""

    monkeypatch.setattr(
        "antang_api.health_profile.report_parse.extract_pdf_text",
        lambda _data: "监测时间:2025/07/28-2025/07/31",
    )
    user, message = await _pdf_message(db_session, b"%PDF-1.4 fake")

    context = await build_chat_context(db_session, user.id, message.id)

    content = context.messages[-1].content
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    assert first["type"] == "file"
    assert first["file"]["filename"] == "指南.pdf"


async def test_a_chart_heavy_report_goes_in_as_parsed_facts(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模板认得的报告直接把解析结果当事实给模型：别让它自己去数图上的数字。"""

    from datetime import date

    from antang_api.health_profile.report_parse import ParsedReport

    parsed = ParsedReport(
        period_start=date(2025, 7, 28),
        period_end=date(2025, 7, 31),
        generated_at=date(2025, 7, 31),
        device="硅基动感Pro",
        device_serial="LT2410FE3P",
        hospital="示例医院",
        mean_mmol=5.81,
        normal_percent=99.6,
        low_percent=0.4,
    )
    monkeypatch.setattr(
        "antang_api.health_profile.report_parse.extract_pdf_text",
        lambda _data: "监测时间:2025/07/28-2025/07/31",
    )
    monkeypatch.setattr(
        "antang_api.health_profile.report_parse.parse_report_pdf",
        lambda _data: parsed,
    )
    user, message = await _pdf_message(db_session, b"%PDF-1.4 fake")

    context = await build_chat_context(db_session, user.id, message.id)

    content = context.messages[-1].content
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    assert first["type"] == "text"
    text = first["text"]
    assert isinstance(text, str)
    assert "按报告模板解析" in text
    assert "平均葡萄糖 5.81mmol/L" in text
    assert "正常档 99.6%" in text
    assert "base64" not in text


async def test_a_pdf_without_a_text_layer_still_goes_in_whole(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描件抽不出文字时不能因此把文件从上下文里弄丢。"""

    def _explode(_data: bytes) -> str:
        from antang_api.health_profile.report_parse import UnreadableReport

        raise UnreadableReport("这个 PDF 里没有文字层")

    monkeypatch.setattr(
        "antang_api.health_profile.report_parse.extract_pdf_text",
        _explode,
    )
    user, message = await _pdf_message(db_session, b"%PDF-1.4 fake")

    context = await build_chat_context(db_session, user.id, message.id)

    content = context.messages[-1].content
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    assert first["type"] == "file"
    assert first["file"]["file_data"].startswith("data:application/pdf;base64,")


async def test_build_chat_context_keeps_proactive_opening_before_short_reply(
    db_session: AsyncSession,
) -> None:
    """主动关怀和用户的简短回复按真实顺序进入下一轮 Core 上下文。"""

    username = f"proactive_context_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    messages = [
        Message(
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="你昨天说晚上总担心低血糖，今天感觉好一点了吗？",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="如果你愿意，也可以只告诉我是不是还在担心。",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="对。",
        ),
    ]
    db_session.add_all(messages)
    await db_session.flush()

    context = await build_chat_context(db_session, user.id, messages[-1].id)

    assert [type(message) for message in context.messages] == [
        AIMessage,
        AIMessage,
        HumanMessage,
    ]
    assert [message.content for message in context.messages] == [
        "你昨天说晚上总担心低血糖，今天感觉好一点了吗？",
        "如果你愿意，也可以只告诉我是不是还在担心。",
        "对。",
    ]


async def test_build_chat_context_uses_latest_summary_boundary(
    db_session: AsyncSession,
) -> None:
    """存在摘要时只读取最新摘要覆盖范围之后的原始消息。"""

    username = f"summary_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    old_user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="旧的用户消息",
    )
    db_session.add(old_user_message)
    await db_session.flush()

    old_assistant_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="旧的助手消息",
    )
    db_session.add(old_assistant_message)
    await db_session.flush()

    first_summary = ConversationSummary(
        user_id=user.id,
        through_message_id=old_user_message.id,
        source_summary_id=None,
        content="第一份摘要",
        model="deepseek-v4-pro",
        prompt_version="v1",
        input_tokens=100,
        output_tokens=20,
    )
    db_session.add(first_summary)
    await db_session.flush()

    latest_summary = ConversationSummary(
        user_id=user.id,
        through_message_id=old_assistant_message.id,
        source_summary_id=first_summary.id,
        content="用户昨晚担心低血糖，助手询问了当时的情况。",
        model="deepseek-v4-pro",
        prompt_version="v1",
        input_tokens=120,
        output_tokens=24,
    )
    db_session.add(latest_summary)
    await db_session.flush()

    summarized_run = AgentRun(
        user_id=user.id,
        trigger_message_id=old_user_message.id,
        result_message_id=old_assistant_message.id,
        parent_run_id=None,
        agent_name="core_agent",
        model="deepseek-v4-pro",
        status=AgentRunStatus.COMPLETED,
    )
    db_session.add(summarized_run)
    await db_session.flush()
    db_session.add(
        AgentToolCall(
            agent_run_id=summarized_run.id,
            tool_call_id="summarized-search",
            tool_name="web_search",
            model_turn_index=0,
            tool_call_index=0,
            arguments={"query": "已经进入摘要边界的查询"},
            result="已经进入摘要边界的工具结果",
            status=AgentToolCallStatus.COMPLETED,
        )
    )
    await db_session.flush()

    recent_user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="最近的用户消息",
    )
    db_session.add(recent_user_message)
    await db_session.flush()

    recent_assistant_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="最近的助手消息",
    )
    db_session.add(recent_assistant_message)
    await db_session.flush()

    context = await build_chat_context(
        db_session,
        user.id,
        recent_assistant_message.id,
    )

    assert context.summary_id == latest_summary.id
    assert context.summary_through_message_id == old_assistant_message.id
    assert [type(message) for message in context.messages] == [
        HumanMessage,
        HumanMessage,
        AIMessage,
    ]
    assert context.messages[0].additional_kwargs == {"lc_source": "summarization"}
    assert [message.content for message in context.messages] == [
        f"{SUMMARY_PREFIX}{latest_summary.content}",
        "最近的用户消息",
        "最近的助手消息",
    ]


async def test_build_chat_context_rebuilds_completed_tool_calls_by_model_turn(
    db_session: AsyncSession,
) -> None:
    """已完成工具按原模型轮次和调用顺序插入最终助手消息之前。"""

    username = f"tools_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="帮我查一下近期资料。",
    )
    assistant_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        # 单条聊天消息保留用户实际看到的所有模型轮文字。
        content="我先帮你查一下。\n\n这是我查到的结论。[S1]",
        sources=[
            {
                "source_id": "S1",
                "title": "旧来源",
                "url": "https://example.com",
            }
        ],
    )
    current_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="你刚才还看了哪些网页？",
    )
    db_session.add_all([user_message, assistant_message, current_message])
    await db_session.flush()

    run = AgentRun(
        user_id=user.id,
        trigger_message_id=user_message.id,
        result_message_id=assistant_message.id,
        parent_run_id=None,
        agent_name="core_agent",
        model="deepseek-v4-pro",
        status=AgentRunStatus.COMPLETED,
    )
    db_session.add(run)
    await db_session.flush()

    # 故意按乱序写入，验证读取依赖显式顺序字段，而不是完成先后。
    db_session.add_all(
        [
            AgentToolCall(
                agent_run_id=run.id,
                tool_call_id="search-1",
                tool_name="web_search",
                model_turn_index=0,
                tool_call_index=1,
                arguments={"query": "第二个查询"},
                result="第二组搜索结果",
                status=AgentToolCallStatus.COMPLETED,
            ),
            AgentToolCall(
                agent_run_id=run.id,
                tool_call_id="fetch-0",
                tool_name="web_fetch",
                model_turn_index=1,
                tool_call_index=0,
                arguments={"url": "https://example.com", "query": "关键结论"},
                result=json.dumps(
                    {
                        "source_id": "S1",
                        "title": "旧来源",
                        "url": "https://example.com",
                        "content": "网页正文片段",
                    },
                    ensure_ascii=False,
                ),
                status=AgentToolCallStatus.COMPLETED,
            ),
            AgentToolCall(
                agent_run_id=run.id,
                tool_call_id="search-0",
                tool_name="web_search",
                model_turn_index=0,
                tool_call_index=0,
                arguments={"query": "第一个查询"},
                result="第一组搜索结果",
                status=AgentToolCallStatus.COMPLETED,
            ),
        ]
    )
    await db_session.flush()

    context = await build_chat_context(db_session, user.id, current_message.id)

    assert [type(message) for message in context.messages] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        ToolMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
        HumanMessage,
    ]
    first_tool_turn = context.messages[1]
    assert isinstance(first_tool_turn, AIMessage)
    assert [call["id"] for call in first_tool_turn.tool_calls] == [
        "search-0",
        "search-1",
    ]
    second_tool_turn = context.messages[4]
    assert isinstance(second_tool_turn, AIMessage)
    assert [call["id"] for call in second_tool_turn.tool_calls] == ["fetch-0"]
    historical_fetch = context.messages[5]
    assert isinstance(historical_fetch, ToolMessage)
    assert historical_fetch.additional_kwargs == {"lc_source": "historical_tool_result"}
    assert json.loads(str(historical_fetch.content)) == {
        "historical_result": True,
        "title": "旧来源",
        "url": "https://example.com",
        "content": "网页正文片段",
        "notice": HISTORICAL_WEB_RESULT_NOTICE,
    }
    assert [message.content for message in context.messages] == [
        "帮我查一下近期资料。",
        "",
        "第一组搜索结果",
        "第二组搜索结果",
        "",
        historical_fetch.content,
        "我先帮你查一下。\n\n这是我查到的结论。",
        "你刚才还看了哪些网页？",
    ]


async def test_build_chat_context_excludes_unfinished_runs_and_tool_calls(
    db_session: AsyncSession,
) -> None:
    """失败运行和失败工具永久保留在数据库，但不会进入下一轮模型上下文。"""

    username = f"tool_status_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    messages = [
        Message(
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="第一次提问",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="第一次回答",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="第二次提问",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="第二次回答",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="当前提问",
        ),
    ]
    db_session.add_all(messages)
    await db_session.flush()

    failed_run = AgentRun(
        user_id=user.id,
        trigger_message_id=messages[0].id,
        result_message_id=messages[1].id,
        parent_run_id=None,
        agent_name="core_agent",
        model="deepseek-v4-pro",
        status=AgentRunStatus.FAILED,
    )
    completed_run = AgentRun(
        user_id=user.id,
        trigger_message_id=messages[2].id,
        result_message_id=messages[3].id,
        parent_run_id=None,
        agent_name="core_agent",
        model="deepseek-v4-pro",
        status=AgentRunStatus.COMPLETED,
    )
    db_session.add_all([failed_run, completed_run])
    await db_session.flush()

    db_session.add_all(
        [
            AgentToolCall(
                agent_run_id=failed_run.id,
                tool_call_id="completed-tool-in-failed-run",
                tool_name="web_search",
                model_turn_index=0,
                tool_call_index=0,
                arguments={"query": "不应读取"},
                result="不应出现的结果一",
                status=AgentToolCallStatus.COMPLETED,
            ),
            AgentToolCall(
                agent_run_id=completed_run.id,
                tool_call_id="failed-tool-in-completed-run",
                tool_name="web_search",
                model_turn_index=0,
                tool_call_index=0,
                arguments={"query": "也不应读取"},
                result=None,
                status=AgentToolCallStatus.FAILED,
            ),
        ]
    )
    await db_session.flush()

    context = await build_chat_context(db_session, user.id, messages[-1].id)

    assert not any(isinstance(message, ToolMessage) for message in context.messages)
    assert [message.content for message in context.messages] == [
        "第一次提问",
        "第一次回答",
        "第二次提问",
        "第二次回答",
        "当前提问",
    ]
