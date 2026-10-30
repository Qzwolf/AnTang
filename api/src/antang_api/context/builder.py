import json
import base64
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    ConversationSummary,
    Message,
    MessageAttachment,
    MessageRole,
    MessageStatus,
)

SUMMARY_PREFIX = """ [较早对话摘要]
以下内容由系统根据更早的用户和你的聊天消息整理成固定栏目，不是用户的原话，
其中「近期事件」「未完成的约定」可以直接作为你继续对话的依据，
但不要把摘要里的措辞当成用户说过的句子。
"""

HISTORICAL_WEB_RESULT_NOTICE = (
    "这是以前一次对话读取的网页，只用于理解上下文。"
    "当前回答若要引用其中的事实，必须在本次运行中重新调用 web_fetch。"
)

# 文字层够厚的 PDF（指南、论文、说明书）直接读文字，比整份 base64 便宜也更准；
# 文字层太少的多半是图表型报告（比如 CGM 评估报告），还是把原文件交给模型看图。
PDF_TEXT_LAYER_MIN_CHARS = 2000
PDF_TEXT_LAYER_MAX_CHARS = 16000
PDF_TEXT_LAYER_NOTICE = (
    "【以下是这份 PDF 里能读出来的文字，最多前 16000 字。"
    "表格、曲线和版式不在其中；需要看图时请让患者把那一页发成截图。】"
)

# 图表型报告（CGM 评估报告那种）文字层很薄，整份 base64 又贵又容易读歪，
# 而模板解析恰好只认这类报告——那就把解析结果当既成事实给模型。
REPORT_FACTS_NOTICE = (
    "【这份 PDF 已经按报告模板解析过，下面的数字是程序从文件里读出来的，不是猜的。】"
)
REPORT_FACTS_TAIL = (
    "要记这份报告时直接调用记录工具，存下来的以这些数字为准：不要再自己读一遍，"
    "也不要改动这些数字。曲线和版式不在其中，患者要看图就请他把那一页发成截图。"
)


def _report_facts_part(attachment: MessageAttachment) -> dict[str, object] | None:
    """报告模板认得出来时，把字段拼成文本块；认不出来返回 None。"""

    from antang_api.health_profile.report_parse import (
        UnreadableReport,
        parse_report_pdf,
        render_parsed_facts,
    )

    try:
        parsed = parse_report_pdf(attachment.data)
    except UnreadableReport:
        return None
    facts = render_parsed_facts(parsed)
    return {
        "type": "text",
        "text": f"{REPORT_FACTS_NOTICE}{facts}。{REPORT_FACTS_TAIL}",
    }


def _pdf_text_part(attachment: MessageAttachment) -> dict[str, object] | None:
    """把 PDF 的文字层抽成文本块；抽不动或文字太少就返回 None。"""

    # 延迟导入：context 包很底层，别让 pypdf 在每次进程启动时都跟着加载。
    from antang_api.health_profile.report_parse import (
        UnreadableReport,
        extract_pdf_text,
    )

    try:
        text = extract_pdf_text(attachment.data).strip()
    except UnreadableReport:
        return None
    if len(text) < PDF_TEXT_LAYER_MIN_CHARS:
        return None
    return {
        "type": "text",
        "text": f"{PDF_TEXT_LAYER_NOTICE}\n\n{text[:PDF_TEXT_LAYER_MAX_CHARS]}",
    }


@dataclass(frozen=True)
class ChatContext:
    """一次Core Agent 调用所需要的所有对话上下文"""

    messages: tuple[BaseMessage, ...]
    summary_id: UUID | None
    summary_through_message_id: UUID | None


def to_model_message(
    message: Message,
    attachment: MessageAttachment | None = None,
) -> BaseMessage:
    """把数据库中的原始消息转换成 LangChain 消息。"""

    match message.role:
        case MessageRole.USER:
            if attachment is not None:
                part: dict[str, object] | None = None
                if attachment.mime_type == "application/pdf":
                    # 先试文字层：读得动就不必把整份 PDF 编码进请求。
                    part = _pdf_text_part(attachment)
                    if part is None:
                        part = _report_facts_part(attachment)
                if part is None:
                    encoded = base64.b64encode(attachment.data).decode("ascii")
                    if attachment.mime_type == "application/pdf":
                        part = {
                            "type": "file",
                            "file": {
                                "filename": attachment.filename,
                                "file_data": (
                                    f"data:{attachment.mime_type};base64,{encoded}"
                                ),
                            },
                        }
                    else:
                        part = {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{attachment.mime_type};base64,{encoded}"
                                ),
                            },
                        }
                return HumanMessage(
                    content=[
                        part,
                        {"type": "text", "text": message.content},
                    ]
                )
            return HumanMessage(content=message.content)

        case MessageRole.ASSISTANT:
            return AIMessage(content=message.content)

    raise ValueError(f"无法转换消息角色：{message.role}")


def to_historical_model_message(
    message: Message,
    attachment: MessageAttachment | None = None,
) -> BaseMessage:
    """把历史聊天交给模型，并移除只对原回答有效的来源编号。"""

    model_message = to_model_message(message, attachment)
    if message.role != MessageRole.ASSISTANT or not message.sources:
        return model_message

    content = message.content
    for source in message.sources:
        if not isinstance(source, dict):
            raise RuntimeError("助手消息包含无效的来源快照")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise RuntimeError("助手消息包含无效的来源快照")
        content = content.replace(f"[{source_id}]", "")

    return model_message.model_copy(update={"content": content})


def _tool_result_text(call: AgentToolCall) -> str:
    """取得模型当时看到的 JSON 文本；数据库结构异常时立即失败。"""

    result = call.result
    if isinstance(result, str):
        return result
    if isinstance(result, list) and len(result) == 1:
        block = result[0]
        if isinstance(block, str):
            return block
        if (
            isinstance(block, dict)
            and set(block) == {"type", "text"}
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            return block["text"]

    raise RuntimeError(f"已完成的工具调用结果不是文本：{call.tool_call_id}")


def _historical_tool_result(call: AgentToolCall) -> str:
    """历史网页结果保留正文，但不把旧的 S1 编号带入新一轮。"""

    result = _tool_result_text(call)
    if call.tool_name != "web_fetch":
        return result

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError as error:
        raise RuntimeError("历史 web_fetch 结果不是有效 JSON") from error

    if not isinstance(parsed, dict) or set(parsed) != {
        "source_id",
        "title",
        "url",
        "content",
    }:
        raise RuntimeError("历史 web_fetch 结果结构无效")

    title = parsed.get("title")
    url = parsed.get("url")
    content = parsed.get("content")
    if not all(
        isinstance(value, str) and value.strip() for value in (title, url, content)
    ):
        raise RuntimeError("历史 web_fetch 结果包含无效字段")

    return json.dumps(
        {
            "historical_result": True,
            "title": title,
            "url": url,
            "content": content,
            "notice": HISTORICAL_WEB_RESULT_NOTICE,
        },
        ensure_ascii=False,
    )


async def _load_completed_tool_messages(
    session: AsyncSession,
    user_id: UUID,
    result_message_ids: list[UUID],
    after_message_id: UUID | None,
) -> dict[UUID, tuple[BaseMessage, ...]]:
    """按模型轮次重建已完成运行中的工具调用和结果。"""

    if not result_message_ids:
        return {}

    tool_call_query = (
        select(AgentToolCall, AgentRun.result_message_id)
        .join(AgentRun, AgentRun.id == AgentToolCall.agent_run_id)
        .where(
            AgentRun.user_id == user_id,
            AgentRun.status == AgentRunStatus.COMPLETED,
            AgentRun.result_message_id.in_(result_message_ids),
            AgentToolCall.status == AgentToolCallStatus.COMPLETED,
        )
        .order_by(
            AgentRun.result_message_id,
            AgentToolCall.model_turn_index,
            AgentToolCall.tool_call_index,
        )
    )

    if after_message_id is not None:
        tool_call_query = tool_call_query.where(
            AgentRun.result_message_id > after_message_id,
        )

    rows = await session.execute(tool_call_query)

    calls_by_message_and_turn: dict[UUID, dict[int, list[AgentToolCall]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for tool_call, result_message_id in rows:
        if result_message_id is None:
            raise RuntimeError("已完成的工具调用缺少 AgentRun 结果消息")
        calls_by_message_and_turn[result_message_id][tool_call.model_turn_index].append(
            tool_call
        )

    tool_messages_by_result: dict[UUID, tuple[BaseMessage, ...]] = {}

    for result_message_id, calls_by_turn in calls_by_message_and_turn.items():
        rebuilt_messages: list[BaseMessage] = []

        for calls in calls_by_turn.values():
            rebuilt_messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name=call.tool_name,
                            args=call.arguments,
                            id=call.tool_call_id,
                            type="tool_call",
                        )
                        for call in calls
                    ],
                )
            )

            for call in calls:
                if call.result is None:
                    raise RuntimeError(f"已完成的工具调用缺少结果：{call.tool_call_id}")
                rebuilt_messages.append(
                    ToolMessage(
                        content=_historical_tool_result(call),
                        tool_call_id=call.tool_call_id,
                        name=call.tool_name,
                        additional_kwargs={"lc_source": "historical_tool_result"},
                    )
                )

        tool_messages_by_result[result_message_id] = tuple(rebuilt_messages)

    return tool_messages_by_result


async def build_chat_context(
    session: AsyncSession,
    user_id: UUID,
    through_message_id: UUID,
    conversation_id: UUID | None = None,
) -> ChatContext:
    """读取最新摘要，以及摘要之后、当前消息之前的完整对话。

    传了 conversation_id 就只看这一段对话；不传保持旧行为（整条流）。
    """

    # 取出最新的摘要
    summary_query = select(ConversationSummary).where(
        ConversationSummary.user_id == user_id,
        ConversationSummary.through_message_id <= through_message_id,
    )
    if conversation_id is not None:
        summary_query = summary_query.where(
            ConversationSummary.conversation_id == conversation_id
        )
    summary = await session.scalar(
        summary_query.order_by(ConversationSummary.id.desc()).limit(1)
    )

    # 该用户所有「已完成、且不晚于当前消息的消息」 的查询语句
    message_query = select(Message).where(
        Message.user_id == user_id,
        Message.status == MessageStatus.COMPLETED,
        Message.id <= through_message_id,
    )
    if conversation_id is not None:
        message_query = message_query.where(
            Message.conversation_id == conversation_id
        )

    # 只要摘要覆盖之后的消息
    if summary is not None:
        message_query = message_query.where(Message.id > summary.through_message_id)

    # 执行查询，得到最新摘要之后所有的消息
    saved_messages = list(
        await session.scalars(
            message_query.order_by(Message.id),
        )
    )
    attachments = list(
        await session.scalars(
            select(MessageAttachment).where(
                MessageAttachment.message_id.in_(
                    [message.id for message in saved_messages]
                )
            )
        )
    ) if saved_messages else []
    attachments_by_message = {
        attachment.message_id: attachment
        for attachment in attachments
        if attachment.message_id is not None
    }

    result_message_ids = [
        message.id
        for message in saved_messages
        if message.role == MessageRole.ASSISTANT
    ]
    tool_messages_by_result = await _load_completed_tool_messages(
        session,
        user_id,
        result_message_ids,
        summary.through_message_id if summary is not None else None,
    )

    model_messages: list[BaseMessage] = []

    if summary:
        model_messages.append(
            HumanMessage(
                f"{SUMMARY_PREFIX}{summary.content}",
                None,
                additional_kwargs={"lc_source": "summarization"},
            )
        )

    for message in saved_messages:
        # 工具调用属于生成这条最终助手消息的 AgentRun，必须紧挨着放在它前面。
        model_messages.extend(tool_messages_by_result.get(message.id, ()))
        model_messages.append(
            to_historical_model_message(
                message,
                attachments_by_message.get(message.id),
            )
        )

    return ChatContext(
        messages=tuple(model_messages),
        summary_id=summary.id if summary else None,
        summary_through_message_id=(summary.through_message_id if summary else None),
    )
