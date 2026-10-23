import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from anyio import CancelScope
from langchain.agents.middleware import InputAgentState
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger
from psycopg.errors import UniqueViolation
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.core import (
    CoreAgentGraph,
    render_core_system_prompt,
)
from antang_api.agents.promises import promise_target, unbacked_promise
from antang_api.agents.relative_day import describe_today
from antang_api.agents.runtime import AgentContext, ToolActivity
from antang_api.citations import (
    VISIBLE_TURN_SEPARATOR,
    VisibleCitationTurn,
    load_and_validate_citations,
)
from antang_api.companion_memory import CompanionMemory
from antang_api.context import prepare_chat_context
from antang_api.context.health_profile import render_core_profile
from antang_api.health_profile.glucose import (
    GLUCOSE_CONTEXT_DAYS,
    list_glucose_readings,
    pending_hypo_review,
    render_glucose_context,
    summarize_glucose,
)
from antang_api.database import lock_user_conversation
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
    ProactiveCareSettings,
)
from antang_api.schemas.chat import (
    ActivityPhase,
    AgentActivityEvent,
    ChatStreamEvent,
    ChatSource,
    LiveHeartRateReading,
    MessageCompletedEvent,
    MessageFailedEvent,
    MessageStartedEvent,
    TextDeltaEvent,
)
from antang_api.proactive_care.service import record_user_activity
from antang_api.psychosocial import read_latest_assessment, render_guidance
from antang_api.psychosocial.hypo_review import render_hypo_review_prompt
from antang_api.behavioral import read_fear_summary, render_behavior_context
from antang_api.settings import settings
from antang_api.voice import (
    as_speech_cue_data,
    mark_speech_cue_consumed,
    pending_speech_cue,
    render_speech_cue,
)


class DuplicateClientMessageError(Exception):
    """手机重复提交了同一个 client_message_id。"""


class ActiveAgentRunError(Exception):
    """当前用户已经有一个正在执行的顶层 Agent。"""


class ChatAttachmentError(Exception):
    """附件不存在、已发送，或者不属于当前用户。"""


class ChatRetryError(Exception):
    """指定的失败回答不存在，或者当前已经不能重试。"""

    def __init__(
        self,
        code: Literal["retry_message_not_found", "chat_retry_not_allowed"],
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


TOOL_ACTIVITY_PHASES: dict[str, ActivityPhase] = {
    "web_search": "searching",
    "web_fetch": "reading",
    "read_wearable_data": "reading",
    "search_diabetes_knowledge": "reading",
    "read_glucose_readings": "reading",
    "delegate_health_profile": "organizing",
    "manage_care_plan": "organizing",
    "log_fear_behavior": "organizing",
    "record_hypoglycemia_belief": "organizing",
}


@dataclass(frozen=True)
class PreparedChatRun:
    """一次已经写入数据库、可以交给 Core Agent 执行的聊天任务。"""

    user_message_id: UUID
    assistant_message_id: UUID
    run_id: UUID


async def prepare_chat_run(
    session: AsyncSession,
    user_id: UUID,
    client_message_id: UUID,
    content: str,
    attachment_id: UUID | None = None,
) -> PreparedChatRun:
    """原子创建用户消息、待生成消息和 Core Agent 运行记录，提交到数据库"""

    user_message = Message(
        client_message_id=client_message_id,
        user_id=user_id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=content,
    )
    try:
        await lock_user_conversation(session, user_id)
        attachment = None
        if attachment_id is not None:
            attachment = await session.scalar(
                select(MessageAttachment)
                .where(
                    MessageAttachment.id == attachment_id,
                    MessageAttachment.user_id == user_id,
                )
                .with_for_update()
            )
            if attachment is None or attachment.message_id is not None:
                raise ChatAttachmentError("附件不存在或已经发送")
            if not user_message.content:
                user_message.content = (
                    "请帮我解读这份报告。"
                    if attachment.kind == MessageAttachmentKind.REPORT
                    else "请帮我看看这张照片。"
                )
        session.add(user_message)
        await session.flush()
        if attachment is not None:
            attachment.message_id = user_message.id
        await record_user_activity(
            session,
            user_id=user_id,
            message_id=user_message.id,
        )

        assistant_message = Message(
            client_message_id=None,
            user_id=user_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.GENERATING,
            content="",
        )
        session.add(assistant_message)
        await session.flush()

        agent_run = AgentRun(
            user_id=user_id,
            trigger_message_id=user_message.id,
            result_message_id=assistant_message.id,
            parent_run_id=None,
            agent_name="core_agent",
            model=settings.hachimi_model_name,
            status=AgentRunStatus.RUNNING,
        )
        session.add(agent_run)
        await session.flush()
        await session.commit()

        return PreparedChatRun(
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            run_id=agent_run.id,
        )
    except IntegrityError as error:
        await session.rollback()

        if isinstance(error.orig, UniqueViolation):
            constraint_name = error.orig.diag.constraint_name

            if constraint_name == "uq_messages_user_client_message_id":
                raise DuplicateClientMessageError() from error

            if constraint_name == "uq_agent_runs_active_root_user":
                raise ActiveAgentRunError() from error

        raise


async def prepare_chat_retry(
    session: AsyncSession,
    user_id: UUID,
    failed_assistant_message_id: UUID,
) -> PreparedChatRun:
    """为失败或取消的回答重建 Core 运行，继续使用原用户消息。"""

    await lock_user_conversation(session, user_id)

    failed_message = await session.scalar(
        select(Message).where(
            Message.id == failed_assistant_message_id,
            Message.user_id == user_id,
            Message.role == MessageRole.ASSISTANT,
        )
    )
    if failed_message is None:
        raise ChatRetryError("retry_message_not_found", "找不到要重试的回答")
    if failed_message.status not in {
        MessageStatus.FAILED,
        MessageStatus.CANCELLED,
    }:
        raise ChatRetryError("chat_retry_not_allowed", "这条回答当前不能重试")

    original_run = await session.scalar(
        select(AgentRun).where(
            AgentRun.user_id == user_id,
            AgentRun.result_message_id == failed_message.id,
            AgentRun.parent_run_id.is_(None),
            AgentRun.agent_name == "core_agent",
        )
    )
    if original_run is None:
        raise ChatRetryError("retry_message_not_found", "找不到要重试的运行记录")
    if original_run.status not in {
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }:
        raise ChatRetryError("chat_retry_not_allowed", "这条回答当前不能重试")
    if original_run.trigger_message_id is None:
        raise RuntimeError("Core AgentRun 缺少用户消息触发来源")

    later_message = await session.scalar(
        select(Message.id)
        .where(
            Message.user_id == user_id,
            Message.id > failed_message.id,
        )
        .limit(1)
    )
    if later_message is not None:
        raise ChatRetryError(
            "chat_retry_not_allowed",
            "对话已经继续，不能再重试这条回答",
        )

    assistant_message = Message(
        client_message_id=None,
        user_id=user_id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.GENERATING,
        content="",
    )
    session.add(assistant_message)

    try:
        await session.flush()
        agent_run = AgentRun(
            user_id=user_id,
            trigger_message_id=original_run.trigger_message_id,
            result_message_id=assistant_message.id,
            parent_run_id=None,
            agent_name="core_agent",
            model=settings.hachimi_model_name,
            status=AgentRunStatus.RUNNING,
        )
        session.add(agent_run)
        await session.flush()
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if (
            isinstance(error.orig, UniqueViolation)
            and error.orig.diag.constraint_name == "uq_agent_runs_active_root_user"
        ):
            raise ActiveAgentRunError() from error
        raise

    return PreparedChatRun(
        user_message_id=original_run.trigger_message_id,
        assistant_message_id=assistant_message.id,
        run_id=agent_run.id,
    )


async def _load_running_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
) -> tuple[Message, AgentRun]:
    """读取运行中的 AI 消息和 AgentRun，并验证数据库状态。"""

    assistant_message = await session.get(Message, prepared_run.assistant_message_id)
    agent_run = await session.get(AgentRun, prepared_run.run_id)

    if assistant_message is None:
        raise RuntimeError(f"找不到 AI 消息：{prepared_run.assistant_message_id}")

    if agent_run is None:
        raise RuntimeError(f"找不到 AgentRun：{prepared_run.run_id}")

    if assistant_message.status != MessageStatus.GENERATING:
        raise RuntimeError(
            f"AI 消息状态应为 generating，实际为 {assistant_message.status.value}"
        )

    if agent_run.status != AgentRunStatus.RUNNING:
        raise RuntimeError(
            f"AgentRun 状态应为 running，实际为 {agent_run.status.value}"
        )

    return assistant_message, agent_run


def extract_agent_result(
    final_state: dict[str, Any],
    input_message_count: int,
    usage_by_step: dict[tuple[int, str], tuple[int, int]] | None = None,
) -> tuple[str, tuple[VisibleCitationTurn, ...], int, int]:
    """从最终状态取得全部可见文字和真实 token 用量。"""

    messages = cast(list[BaseMessage], final_state["messages"])

    if len(messages) <= input_message_count:
        raise RuntimeError("Core Agent 没有生成任何新消息")

    generated_messages = messages[input_message_count:]
    final_message = generated_messages[-1]

    if not isinstance(final_message, AIMessage):
        raise RuntimeError(
            f"Core Agent 最后一条消息类型错误：{type(final_message).__name__}"
        )

    if final_message.tool_calls:
        raise RuntimeError("Core Agent 结束时仍有未执行的工具调用")

    if not final_message.text.strip():
        raise RuntimeError("Core Agent 返回了空回答")

    available_tool_call_ids: set[str] = set()
    visible_turns: list[VisibleCitationTurn] = []
    for message in generated_messages:
        if isinstance(message, ToolMessage) and message.status != "error":
            available_tool_call_ids.add(message.tool_call_id)
        elif isinstance(message, AIMessage) and message.text:
            visible_turns.append(
                VisibleCitationTurn(
                    content=message.text,
                    available_tool_call_ids=frozenset(available_tool_call_ids),
                )
            )

    content = VISIBLE_TURN_SEPARATOR.join(turn.content for turn in visible_turns)

    if not content.strip():
        raise RuntimeError("Core Agent 返回了空回答")

    input_tokens = 0
    output_tokens = 0

    if usage_by_step:
        # 流式路径：每个模型调用只取最后一份用量（见 _record_step_usage），
        # 合并后的 message.usage_metadata 是被放大过的。
        input_tokens = sum(item[0] for item in usage_by_step.values())
        output_tokens = sum(item[1] for item in usage_by_step.values())
        return content, tuple(visible_turns), input_tokens, output_tokens

    for message in generated_messages:
        if not isinstance(message, AIMessage):
            continue

        usage = message.usage_metadata

        if usage is None:
            raise RuntimeError("模型响应缺少 usage_metadata")

        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]

    return content, tuple(visible_turns), input_tokens, output_tokens


def _has_thinking_content(chunk: AIMessageChunk) -> bool:
    """只识别 Anthropic 协议的 thinking 块，不读取或传播其内容。"""

    if not isinstance(chunk.content, list):
        return False

    return any(
        isinstance(block, dict) and block.get("type") == "thinking"
        for block in chunk.content
    )


RETRYABLE_STREAM_ATTEMPTS = 2


def _is_retryable_stream_error(error: BaseException) -> bool:
    """只有网络层断流值得重试；契约错误、解析错误重试也只会再错一次。"""

    return isinstance(error, (httpx.HTTPError, ConnectionError, TimeoutError))


async def _astream_agent(
    agent: Any,
    agent_input: Any,
    run_config: RunnableConfig,
    runtime_context: AgentContext,
    *,
    attempts: int = RETRYABLE_STREAM_ATTEMPTS,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """把 Agent 的流包一层，只在「还什么都没发出去」时重试上游断流。

    中转站偶尔会在流到一半时断开连接（`RemoteProtocolError`），患者看到的就是
    一句「回复失败」。但如果已经发出过任何事件——文字、状态栏、工具开始信号——
    重试就会让用户看到两遍内容，或者把工具执行两遍（记行为、写承诺都有副作用），
    所以那种情况照原样抛错，让上层把这一轮标记成失败。
    """

    attempt = 0
    while True:
        attempt += 1
        emitted = False
        try:
            async for part in agent.astream(
                agent_input,
                config=run_config,
                context=runtime_context,
                stream_mode=["messages", "custom", "values"],
                version="v2",
            ):
                emitted = True
                yield part
            return
        except Exception as error:
            if emitted or attempt >= attempts or not _is_retryable_stream_error(error):
                raise
            if on_retry is not None:
                on_retry(attempt, error)


def _record_step_usage(
    usage_by_step: dict[tuple[int, str], tuple[int, int]],
    chunk: BaseMessage,
    metadata: dict[str, Any],
) -> None:
    """记下这次模型调用的真实用量。

    中转站在每个流式分片上重复带上「到目前为止的累计用量」，所以同一次调用会看到
    很多次；只保留最后一次，再按调用求和，才等于真实消耗。
    """

    usage = getattr(chunk, "usage_metadata", None)
    step = metadata.get("langgraph_step")

    if usage is None or not isinstance(step, int):
        return

    node = str(metadata.get("langgraph_node") or "")
    usage_by_step[(step, node)] = (usage["input_tokens"], usage["output_tokens"])


def _parse_tool_activity(value: object) -> ToolActivity:
    """验证工具中间件发出的内部事件，避免把未知数据发给 App。"""

    if not isinstance(value, dict) or value.get("event") != "tool_activity":
        raise RuntimeError("Core Agent 发出了未知的自定义流事件")

    tool_call_id = value.get("tool_call_id")
    tool_name = value.get("tool_name")
    status = value.get("status")

    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise RuntimeError("工具活动事件缺少 tool_call_id")
    if not isinstance(tool_name, str) or not tool_name:
        raise RuntimeError("工具活动事件缺少 tool_name")
    if status not in {"started", "completed", "failed", "cancelled"}:
        raise RuntimeError("工具活动事件的 status 无效")

    return cast(ToolActivity, value)


def _is_user_visible_model_event(metadata: dict[str, Any]) -> bool:
    """只允许顶层 Core 模型的文字进入 App，嵌套 Agent 一律不可见。"""

    return (
        metadata.get("langgraph_node") == "model"
        and metadata.get("stream_visibility") == "user"
    )


async def _complete_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
    content: str,
    input_tokens: int,
    output_tokens: int,
    sources: list[ChatSource],
    hypo_review_reading_id: UUID | None = None,
    speech_cue_id: UUID | None = None,
) -> None:
    """保存完整回答，并将 AgentRun 标记为完成。"""

    assistant_message, agent_run = await _load_running_chat_run(
        session,
        prepared_run,
    )
    now = datetime.now(timezone.utc)

    assistant_message.content = content
    assistant_message.sources = [source.model_dump() for source in sources]
    assistant_message.status = MessageStatus.COMPLETED
    assistant_message.completed_at = now

    agent_run.status = AgentRunStatus.COMPLETED
    agent_run.input_tokens = input_tokens
    agent_run.output_tokens = output_tokens
    agent_run.finished_at = now

    tool_names = set(
        (
            await session.execute(
                select(AgentToolCall.tool_name).where(
                    AgentToolCall.agent_run_id == agent_run.id
                )
            )
        ).scalars()
    )
    if unbacked_promise(content, tool_names):
        # 把承诺的时刻一并记下来：凌晨说的「明天十点」按用户的口径算，
        # 否则事后排查时只看到「承诺过」，看不出它到底该在哪一刻发生。
        target = promise_target(content, now=now)
        logger.warning(
            "助手承诺了主动联系但这一轮没有调用计划工具（承诺时刻=%s）",
            target.isoformat() if target is not None else "无法解析",
        )

    if hypo_review_reading_id is not None:
        reviewed_reading = await session.get(GlucoseReading, hypo_review_reading_id)
        if reviewed_reading is not None and reviewed_reading.reviewed_at is None:
            # 只有回答真的写进数据库，这一问才算「问过」；失败或取消的轮次
            # 用户没收到，下次还得再问。mark_hypo_reviewed 自带 commit，
            # 会把「问过」和本轮成败拆成两次事务，所以这里只改对象状态。
            reviewed_reading.reviewed_at = now

    if speech_cue_id is not None:
        # 声音状态同样只在回答落库时才算用掉：失败或取消的轮次用户没读到回复，
        # 这条 cue 留给下一次真正发出去的那一轮。
        await mark_speech_cue_consumed(session, cue_id=speech_cue_id, now=now)

    await session.commit()


async def _fail_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
    partial_content: str,
    error: Exception,
) -> None:
    """保存执行失败状态和已经发送给手机的部分文字。"""

    await session.rollback()
    assistant_message, agent_run = await _load_running_chat_run(
        session,
        prepared_run,
    )
    now = datetime.now(timezone.utc)

    assistant_message.content = partial_content
    assistant_message.sources = []
    assistant_message.status = MessageStatus.FAILED
    assistant_message.completed_at = now

    agent_run.status = AgentRunStatus.FAILED
    agent_run.error_type = type(error).__name__
    agent_run.error_message = str(error)
    agent_run.finished_at = now

    if "quota" in str(error).lower():
        # 中转站余额不足时回 403 insufficient_user_quota，患者这一轮一个字都收不到。
        # 单独记一条，值班的人不用去翻 403 原文才知道该充值了。
        logger.bind(
            run_id=str(prepared_run.run_id),
            user_id=str(agent_run.user_id),
        ).warning("chat_run_out_of_quota")

    await session.execute(
        update(AgentToolCall)
        .where(
            AgentToolCall.agent_run_id == prepared_run.run_id,
            AgentToolCall.status == AgentToolCallStatus.RUNNING,
        )
        .values(
            status=AgentToolCallStatus.FAILED,
            error_type=type(error).__name__,
            error_message=str(error),
            finished_at=now,
        )
    )

    await session.commit()


async def _cancel_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
    partial_content: str,
) -> None:
    """客户端断开连接时保存取消状态和已经生成的部分文字。"""

    await session.rollback()
    assistant_message, agent_run = await _load_running_chat_run(
        session,
        prepared_run,
    )
    now = datetime.now(timezone.utc)

    assistant_message.content = partial_content
    assistant_message.sources = []
    assistant_message.status = MessageStatus.CANCELLED
    assistant_message.completed_at = now

    agent_run.status = AgentRunStatus.CANCELLED
    agent_run.finished_at = now

    await session.execute(
        update(AgentToolCall)
        .where(
            AgentToolCall.agent_run_id == prepared_run.run_id,
            AgentToolCall.status == AgentToolCallStatus.RUNNING,
        )
        .values(
            status=AgentToolCallStatus.CANCELLED,
            error_type="CancelledError",
            error_message="聊天运行被取消",
            finished_at=now,
        )
    )

    await session.commit()


async def stream_chat_run(
    session: AsyncSession,
    model: BaseChatModel,
    agent: CoreAgentGraph,
    tools: Sequence[BaseTool],
    companion_memory: CompanionMemory,
    user_id: UUID,
    prepared_run: PreparedChatRun,
    live_heart_rate: LiveHeartRateReading | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """准备上下文、执行 Core Agent，并持续产生聊天流事件。"""

    started_at = time.perf_counter()
    streamed_parts: list[str] = []
    terminal_event: MessageCompletedEvent | MessageFailedEvent | None = None
    current_activity: ActivityPhase | None = None
    active_tool_phases: dict[str, ActivityPhase] = {}
    has_completed_tool = False
    run_log = logger.bind(
        run_id=str(prepared_run.run_id),
        user_id=str(user_id),
        model=settings.hachimi_model_name,
    )
    run_log.bind(status=AgentRunStatus.RUNNING.value).info("chat_run_started")

    try:
        yield MessageStartedEvent(
            user_message_id=prepared_run.user_message_id,
            assistant_message_id=prepared_run.assistant_message_id,
            run_id=prepared_run.run_id,
        )

        current_activity = "thinking"
        yield AgentActivityEvent(phase=current_activity)

        health_profile_context = await render_core_profile(
            session,
            user_id,
        )
        # 这一段决定「怎么说」：同一个风险提示对不同恐惧程度、不同察觉能力的人要换说法。
        psychosocial_context = render_guidance(
            await read_latest_assessment(session, user_id),
        )
        # 行为与信念是「这个人实际做了什么、相信什么」，用来决定追问方向。
        behavior_context = render_behavior_context(
            await read_fear_summary(session, user_id=user_id, days=28),
        )
        # 日期口径必须显式告诉模型：凌晨一点说的「明天」是用户口中的明天，
        # 不是日历上的次日，否则承诺的回访会晚一整天。
        behavior_context = "\n".join(
            part
            for part in (describe_today(datetime.now(UTC)), behavior_context)
            if part
        )
        # 患者自己记的血糖：模型只能从文字里猜，所以把近 7 天的读数摊开给它，
        # 并注明「自己记的、可能漏记」，免得被当成 CGM 曲线下判断。
        glucose_context = render_glucose_context(
            days=GLUCOSE_CONTEXT_DAYS,
            summary=await summarize_glucose(
                session,
                user_id=user_id,
                days=GLUCOSE_CONTEXT_DAYS,
            ),
            recent=await list_glucose_readings(
                session,
                user_id=user_id,
                days=GLUCOSE_CONTEXT_DAYS,
                limit=5,
            ),
        )
        # 复盘只问一次：读取最近 24 小时里偏低、还没问过的那一条。
        # prepare_chat_context 会 rollback，之后再碰 ORM 对象就会过期，
        # 所以这里立刻把开场白定下来，只留 reading.id。
        hypo_review_reading = await pending_hypo_review(
            session,
            user_id=user_id,
            now=datetime.now(UTC),
        )
        hypo_review_reading_id = (
            hypo_review_reading.id if hypo_review_reading is not None else None
        )
        hypo_review_context = (
            render_hypo_review_prompt(
                reported_by_user=hypo_review_reading.source == "manual",
            )
            if hypo_review_reading is not None
            else ""
        )
        # 声音状态只对紧接着的这句话有效：取最近一条没用过、没过期的。
        # 和复盘一样，读完之后 prepare_chat_context 会 rollback，所以立刻算好
        # 提示词，只留 cue.id，等回答落库时再打「用过了」。
        speech_cue = await pending_speech_cue(
            session,
            user_id=user_id,
            now=datetime.now(UTC),
        )
        speech_cue_id = speech_cue.id if speech_cue is not None else None
        speech_cue_context = (
            render_speech_cue(as_speech_cue_data(speech_cue))
            if speech_cue is not None
            else ""
        )
        try:
            companion_memory_context = await companion_memory.prepare_context(
                session,
                user_id,
                prepared_run.user_message_id,
            )
        except Exception as error:
            # 记忆是增强项：写入或召回失败不该让患者收不到任何回答。
            # 现场有过 `Fact extraction failed` 把整轮聊天打成失败。
            await session.rollback()
            logger.bind(
                user_id=str(user_id),
                error_type=type(error).__name__,
            ).opt(exception=True).warning("companion_memory_unavailable")
            companion_memory_context = ""
        active_plans = list(
            await session.scalars(
                select(CarePlan)
                .where(
                    CarePlan.user_id == user_id,
                    CarePlan.status == CarePlanStatus.ACTIVE,
                )
                .order_by(CarePlan.follow_up_at, CarePlan.id)
            )
        )
        care_plan_context = json.dumps(
            [
                {
                    "plan_id": str(plan.id),
                    "summary": plan.summary,
                    "follow_up_at": plan.follow_up_at.isoformat(),
                    "revision": plan.revision,
                }
                for plan in active_plans
            ],
            ensure_ascii=False,
        )
        care_settings = await session.get(ProactiveCareSettings, user_id)
        if care_settings is None:
            raise RuntimeError("用户缺少主动关怀设置")
        current_time_context = json.dumps(
            {
                "timezone": care_settings.timezone,
                "now": datetime.now(ZoneInfo(care_settings.timezone)).isoformat(),
            },
            ensure_ascii=False,
        )
        live_heart_rate_context = ""
        if live_heart_rate is not None:
            measured_at = live_heart_rate.measured_at
            if measured_at.tzinfo is None:
                # 手机没带时区时按用户自己的时区解释，不凭空当成 UTC。
                measured_at = measured_at.replace(
                    tzinfo=ZoneInfo(care_settings.timezone),
                )
            live_heart_rate_context = json.dumps(
                {
                    "beats_per_minute": live_heart_rate.beats_per_minute,
                    "measured_at": measured_at.isoformat(),
                    "device_name": live_heart_rate.device_name,
                },
                ensure_ascii=False,
            )
        rendered_system_prompt = render_core_system_prompt(
            health_profile_context,
            companion_memory_context=companion_memory_context,
            care_plan_context=care_plan_context,
            current_time_context=current_time_context,
            live_heart_rate_context=live_heart_rate_context,
            psychosocial_context=psychosocial_context,
            behavior_context=behavior_context,
            glucose_context=glucose_context,
            hypo_review_context=hypo_review_context,
            speech_cue_context=speech_cue_context,
        )

        prepared_context = await prepare_chat_context(
            session=session,
            model=model,
            system_prompt=rendered_system_prompt,
            user_id=user_id,
            through_message_id=prepared_run.user_message_id,
            compaction_trigger_tokens=settings.context_compaction_trigger_tokens,
            recent_messages_to_keep=settings.context_recent_messages_to_keep,
            tool_result_cleanup_trigger_tokens=(
                settings.context_tool_result_cleanup_trigger_tokens
            ),
            recent_tool_results_to_keep=(settings.context_recent_tool_results_to_keep),
            tools=tools,
        )
        run_log.bind(
            context_tokens=prepared_context.input_tokens,
            compacted=prepared_context.was_compacted,
            cleared_tool_result_count=(prepared_context.cleared_tool_result_count),
        ).info("chat_context_prepared")

        input_message_count = len(prepared_context.context.messages)
        final_state: dict[str, Any] | None = None
        run_config: RunnableConfig = {
            "configurable": {"thread_id": str(prepared_run.run_id)},
            "metadata": {
                "run_id": str(prepared_run.run_id),
                "user_id": str(user_id),
                "stream_visibility": "user",
            },
        }
        runtime_context = AgentContext(
            user_id=user_id,
            run_id=prepared_run.run_id,
            input_message_count=input_message_count,
            rendered_system_prompt=rendered_system_prompt,
        )
        last_text_model_step: int | None = None
        # 中转站会在**每个**流式分片上重复带上累计用量，LangChain 合并分片时会把
        # 它们相加，直接用合并后的 usage_metadata 会把它放大十几倍（实测 6.6k 的
        # 上下文被记成 12 万）。所以按模型调用（langgraph_step）只保留最后一次看到
        # 的用量——那才是这一次调用的真实值。
        usage_by_step: dict[tuple[int, str], tuple[int, int]] = {}
        agent_input = cast(
            InputAgentState,
            {"messages": list(prepared_context.context.messages)},
        )

        def _log_stream_retry(attempt: int, error: BaseException) -> None:
            run_log.bind(
                attempt=attempt,
                error_type=type(error).__name__,
            ).warning("chat_stream_retry")

        async for stream_part in _astream_agent(
            agent,
            agent_input,
            run_config,
            runtime_context,
            on_retry=_log_stream_retry,
        ):
            stream_type = stream_part["type"]

            if stream_type == "messages":
                # stream_part的格式为： {"type": "messages", "data": (chunk, metadata)}
                chunk, metadata = cast(
                    tuple[BaseMessage, dict[str, Any]],
                    stream_part["data"],
                )

                _record_step_usage(usage_by_step, chunk, metadata)

                if not _is_user_visible_model_event(metadata):
                    continue

                if not isinstance(chunk, AIMessageChunk):
                    raise TypeError(f"流式消息类型错误：{type(chunk).__name__}")

                model_step = metadata.get("langgraph_step")

                if not isinstance(model_step, int):
                    raise RuntimeError("模型流事件缺少 langgraph_step")

                if _has_thinking_content(chunk):
                    thinking_phase: ActivityPhase = (
                        "organizing" if has_completed_tool else "thinking"
                    )
                    if thinking_phase != current_activity:
                        current_activity = thinking_phase
                        yield AgentActivityEvent(phase=thinking_phase)

                delta = chunk.text

                if delta:
                    if (
                        last_text_model_step is not None
                        and last_text_model_step != model_step
                    ):
                        streamed_parts.append(VISIBLE_TURN_SEPARATOR)
                        yield TextDeltaEvent(delta=VISIBLE_TURN_SEPARATOR)

                    last_text_model_step = model_step
                    # App 收到普通文字后会隐藏状态栏；后续的
                    # reasoning 或工具事件必须可以重新显示它。
                    current_activity = None
                    streamed_parts.append(delta)
                    yield TextDeltaEvent(delta=delta)

            elif stream_type == "custom":
                activity = _parse_tool_activity(stream_part["data"])
                tool_call_id = activity["tool_call_id"]
                tool_name = activity["tool_name"]
                status = activity["status"]
                phase = TOOL_ACTIVITY_PHASES.get(tool_name)

                if phase is None:
                    raise RuntimeError(f"工具缺少活动阶段配置：{tool_name}")

                if status == "started":
                    active_tool_phases[tool_call_id] = phase
                    next_activity = phase
                else:
                    active_tool_phases.pop(tool_call_id, None)
                    if status == "completed":
                        has_completed_tool = True

                    if active_tool_phases:
                        next_activity = list(active_tool_phases.values())[-1]
                    elif status == "completed":
                        next_activity = "organizing"
                    else:
                        # 终止事件会立即让 App 清除状态栏。
                        current_activity = None
                        continue

                if next_activity != current_activity:
                    current_activity = next_activity
                    yield AgentActivityEvent(phase=next_activity)

            elif stream_type == "values":
                # {"type": "values","data": {完整的 LangGraph 状态字典}}
                final_state = cast(dict[str, Any], stream_part["data"])
            else:
                raise RuntimeError(f"收到未知的 LangGraph 流类型：{stream_type}")

        if final_state is None:
            raise RuntimeError("LangGraph 没有返回最终状态")

        content, visible_turns, input_tokens, output_tokens = extract_agent_result(
            final_state,
            input_message_count,
            usage_by_step,
        )
        streamed_content = "".join(streamed_parts)

        if streamed_content != content:
            raise RuntimeError("流式文字与最终回答内容不一致")

        sources = await load_and_validate_citations(
            session,
            agent_run_id=prepared_run.run_id,
            content=content,
            visible_turns=visible_turns,
        )

        await _complete_chat_run(
            session=session,
            prepared_run=prepared_run,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            sources=sources,
            hypo_review_reading_id=hypo_review_reading_id,
            speech_cue_id=speech_cue_id,
        )
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        run_log.bind(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status=AgentRunStatus.COMPLETED.value,
        ).info("chat_run_finished")
        terminal_event = MessageCompletedEvent(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            sources=sources,
        )
    except (asyncio.CancelledError, GeneratorExit):
        # Starlette 取消流任务后，普通 await 也可能继续收到取消信号。
        # shield 保证终止状态先写入数据库。
        with CancelScope(shield=True):
            await _cancel_chat_run(
                session,
                prepared_run,
                "".join(streamed_parts),
            )
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            run_log.bind(
                latency_ms=latency_ms,
                status=AgentRunStatus.CANCELLED.value,
            ).warning("chat_run_cancelled")
        raise
    except Exception as error:
        await _fail_chat_run(
            session=session,
            prepared_run=prepared_run,
            partial_content="".join(streamed_parts),
            error=error,
        )
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        run_log.bind(
            latency_ms=latency_ms,
            status=AgentRunStatus.FAILED.value,
            error_type=type(error).__name__,
        ).error("chat_run_failed")
        terminal_event = MessageFailedEvent(error_type=type(error).__name__)

    if terminal_event is None:
        raise RuntimeError("聊天流缺少结束事件")

    # 结束事件在 try 块外发送。手机在此刻断开时，数据库里的完成状态仍然有效。
    yield terminal_event


async def recover_interrupted_chat_runs(session: AsyncSession) -> None:
    """API 启动时只恢复由用户消息触发、属于本进程的运行。"""

    runs = list(
        await session.scalars(
            select(AgentRun).where(
                AgentRun.status == AgentRunStatus.RUNNING,
                AgentRun.trigger_care_task_id.is_(None),
            )
        )
    )
    tool_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .join(AgentRun, AgentRun.id == AgentToolCall.agent_run_id)
            .where(
                AgentToolCall.status == AgentToolCallStatus.RUNNING,
                AgentRun.trigger_care_task_id.is_(None),
            )
        )
    )

    if not runs and not tool_calls:
        return

    now = datetime.now(timezone.utc)

    for run in runs:
        run.status = AgentRunStatus.FAILED
        run.error_type = "ServerRestarted"
        run.error_message = "服务重启，中断了正在执行的 AgentRun"
        run.finished_at = now

        if run.result_message_id is not None:
            message = await session.get(Message, run.result_message_id)

            if message is not None and message.status == MessageStatus.GENERATING:
                message.status = MessageStatus.FAILED
                message.completed_at = now

    for tool_call in tool_calls:
        tool_call.status = AgentToolCallStatus.FAILED
        tool_call.error_type = "ServerRestarted"
        tool_call.error_message = "服务重启，中断了正在执行的工具调用"
        tool_call.finished_at = now

    await session.commit()

    for run in runs:
        logger.bind(
            run_id=str(run.id),
            user_id=str(run.user_id),
            model=run.model,
            status=AgentRunStatus.FAILED.value,
            error_type="ServerRestarted",
        ).warning("chat_run_recovered_after_restart")

    for tool_call in tool_calls:
        logger.bind(
            agent_run_id=str(tool_call.agent_run_id),
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            status=AgentToolCallStatus.FAILED.value,
            error_type="ServerRestarted",
        ).warning("tool_call_recovered_after_restart")
