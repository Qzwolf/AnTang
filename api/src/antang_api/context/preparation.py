from collections.abc import Sequence
from dataclasses import dataclass, replace
from uuid import UUID

from langchain.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.context.builder import (
    ChatContext,
    build_chat_context,
)
from antang_api.context.compaction import compact_conversation
from antang_api.context.token_count import count_input_tokens
from antang_api.context.tool_results import clear_old_tool_results


class ContextBudgetExceededError(Exception):
    """压缩后的上下文仍然达到配置的 token 限制。"""

    def __init__(
        self,
        input_tokens: int,
        token_limit: int,
    ) -> None:
        self.input_tokens = input_tokens
        self.token_limit = token_limit

        super().__init__(
            f"压缩后上下文仍有 {input_tokens} tokens，达到限制 {token_limit}"
        )


@dataclass(frozen=True)
class PreparedChatContext:
    """已经完成计数和必要压缩的 Core Agent 上下文。"""

    context: ChatContext
    input_tokens: int
    was_compacted: bool
    cleared_tool_result_count: int


async def _clear_tool_results_when_needed(
    context: ChatContext,
    input_tokens: int,
    model: BaseChatModel,
    system_prompt: str,
    tools: Sequence[BaseTool] | None,
    cleanup_trigger_tokens: int,
    recent_tool_results_to_keep: int,
) -> tuple[ChatContext, int, int]:
    """达到清理阈值时替换旧工具结果，并重新精确计数。"""

    if input_tokens < cleanup_trigger_tokens:
        return context, input_tokens, 0

    cleared = clear_old_tool_results(
        context.messages,
        recent_tool_results_to_keep,
    )
    cleared_context = replace(context, messages=cleared.messages)
    cleared_input_tokens = await count_input_tokens(
        model,
        system_prompt,
        cleared_context.messages,
        tools,
    )

    return cleared_context, cleared_input_tokens, cleared.cleared_count


async def prepare_chat_context(
    session: AsyncSession,
    model: BaseChatModel,
    system_prompt: str,
    user_id: UUID,
    through_message_id: UUID,
    compaction_trigger_tokens: int,
    recent_messages_to_keep: int,
    tool_result_cleanup_trigger_tokens: int,
    recent_tool_results_to_keep: int,
    tools: Sequence[BaseTool] | None = None,
    conversation_id: UUID | None = None,
) -> PreparedChatContext:
    """组装上下文，先清理旧工具结果，必要时再压缩对话一次。"""

    if tool_result_cleanup_trigger_tokens >= compaction_trigger_tokens:
        raise ValueError("工具结果清理阈值必须小于对话压缩阈值")

    # 组装旧摘要 + 原文
    context = await build_chat_context(
        session,
        user_id,
        through_message_id,
        conversation_id,
    )

    # SQLAlchemy 的 SELECT 也会自动开启事务。上下文已经转换成普通的
    # LangChain 消息后立即结束只读事务，避免等待模型接口时占用数据库连接。
    await session.rollback()

    # 计算输入 token 数
    input_tokens = await count_input_tokens(
        model,
        system_prompt,
        context.messages,
        tools,
    )
    (
        context,
        input_tokens,
        cleared_tool_result_count,
    ) = await _clear_tool_results_when_needed(
        context,
        input_tokens,
        model,
        system_prompt,
        tools,
        tool_result_cleanup_trigger_tokens,
        recent_tool_results_to_keep,
    )

    # 清理之后仍未达到摘要阈值，无需压缩对话。
    if input_tokens < compaction_trigger_tokens:
        return PreparedChatContext(
            context=context,
            input_tokens=input_tokens,
            was_compacted=False,
            cleared_tool_result_count=cleared_tool_result_count,
        )

    # 根据旧摘要和原文生成一份摘要，提交到数据库的 conversation_summary表里
    await compact_conversation(
        session,
        model,
        user_id,
        through_message_id,
        recent_messages_to_keep,
        conversation_id=conversation_id,
    )

    # 用户新摘要重建上下文
    rebuilt_context = await build_chat_context(
        session,
        user_id,
        through_message_id,
        conversation_id,
    )

    # 与第一次组装一样，精确计数走外部接口前先释放数据库连接。
    await session.rollback()

    rebuilt_input_tokens = await count_input_tokens(
        model,
        system_prompt,
        rebuilt_context.messages,
        tools,
    )
    (
        rebuilt_context,
        rebuilt_input_tokens,
        rebuilt_cleared_count,
    ) = await _clear_tool_results_when_needed(
        rebuilt_context,
        rebuilt_input_tokens,
        model,
        system_prompt,
        tools,
        tool_result_cleanup_trigger_tokens,
        recent_tool_results_to_keep,
    )

    # 摘要之后token还是太多，摘要失败
    if rebuilt_input_tokens >= compaction_trigger_tokens:
        raise ContextBudgetExceededError(
            rebuilt_input_tokens,
            compaction_trigger_tokens,
        )

    return PreparedChatContext(
        context=rebuilt_context,
        input_tokens=rebuilt_input_tokens,
        was_compacted=True,
        cleared_tool_result_count=rebuilt_cleared_count,
    )
