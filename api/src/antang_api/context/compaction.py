from uuid import UUID

from langchain.chat_models import BaseChatModel
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    get_buffer_string,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.context.builder import to_historical_model_message
from antang_api.models import (
    ConversationSummary,
    Message,
    MessageRole,
    MessageStatus,
)
from antang_api.settings import settings

SUMMARY_PROMPT_VERSION = "v3"

SUMMARY_SYSTEM_PROMPT = """
你负责把一段较早的长期陪伴对话整理成一份可以反复续写的结构化摘要。
这份摘要会代替原文进入后续对话，所以它的职责不是复述聊天，而是保住「后面还会用到的信息」。

严格按下面的栏目输出，栏目名和顺序不要改，没有内容的栏目写「无」：

[用户稳定信息]
姓名或称呼、糖尿病类型与确诊时长、治疗方案（泵还是针、大致剂量思路）、过敏或禁忌、同住的家人。
只写对话里明确出现过的，不要推断。

[近期事件]
按时间先后写，每条以日期开头（不知道日期就写「某天」）。保留原文里的血糖数值、剂量、时间、场合。
低血糖、补糖、就医、检查、考试、加班、聚餐、运动都写在这一栏。

[未完成的约定]
助手答应过的事（什么时候回访、要问什么、要发的材料），用户答应过的事，以及没有结论就中断的话题。

[担忧与信念]
用户表达过的恐惧、归因方式、自相矛盾的说法，尽量保留用户的短原话（用引号）。
比如「低血糖会死」「是我吃出来的」「我一点都不怕」。

[对话偏好]
用户明确说过喜欢或不喜欢的说法、称呼、话题边界。

[可以忘记的]
已经解决、之后不会再提到的一次性琐事，一句话概括即可。

规则：
- 依据只有「现有摘要」和「后续原始对话」，不补充外部知识，也不给医学建议。
- 「现有摘要」里的信息必须延续；只有新对话明确推翻时才改，并在对应栏目里写清改成了什么。
- 没有提到的不要猜，写「无」或「不确定」，不要用「可能会」「也许」填补空白。
- 引用用户原话时加引号，不要把语气改重，也不要把犹豫改成确定。
- 对话可能由助手开场，也可能出现连续的助手消息；用户只回「对」「好」时，
  要把它所回应的具体问题或提议写进「近期事件」或「未完成的约定」。
- 不保留 [S1] 这类只对原来那次回答有效的来源编号。
- 不要写寒暄和「用户与助手进行了交流」这种空话，每一行都要有具体信息。
""".strip()


class InsufficientMessagesForCompactionError(Exception):
    """当前可以压缩的完整对话数量不足。"""


def to_summary_message(message: Message) -> BaseMessage:
    """移除只属于原回答的来源编号，再交给摘要模型。"""

    return to_historical_model_message(message)


def select_messages_to_compact(
    messages: list[Message],
    messages_to_keep: int,
) -> list[Message]:
    """选择较早的完整对话，并保留指定数量的近期原始消息。"""

    if messages_to_keep < 1:
        raise ValueError("messages_to_keep 必须大于 0")

    cutoff = len(messages) - messages_to_keep

    if cutoff <= 0:
        raise InsufficientMessagesForCompactionError()

    # 摘要边界落在助手消息之后，让近期原文从用户消息开始。
    while cutoff > 0 and messages[cutoff - 1].role != MessageRole.ASSISTANT:
        cutoff -= 1

    if cutoff == 0:
        raise InsufficientMessagesForCompactionError()

    return messages[:cutoff]


async def compact_conversation(
    session: AsyncSession,
    model: BaseChatModel,
    user_id: UUID,
    through_message_id: UUID,
    messages_to_keep: int,
    *,
    conversation_id: UUID | None = None,
) -> ConversationSummary:
    """生成并保存一份新的对话摘要快照。

    读取摘要素材和写入新快照使用两个短事务。中间调用摘要模型时，
    当前会话不持有 PostgreSQL 事务和连接。
    """

    if session.new or session.dirty or session.deleted:
        raise RuntimeError("生成对话摘要前，数据库会话中不能有未提交的修改")

    summary_query = select(ConversationSummary).where(
        ConversationSummary.user_id == user_id,
        ConversationSummary.through_message_id <= through_message_id,
    )
    if conversation_id is not None:
        summary_query = summary_query.where(
            ConversationSummary.conversation_id == conversation_id
        )
    current_summary = await session.scalar(
        summary_query.order_by(ConversationSummary.id.desc()).limit(1)
    )

    message_query = select(Message).where(
        Message.user_id == user_id,
        Message.status == MessageStatus.COMPLETED,
        Message.id <= through_message_id,
    )
    if conversation_id is not None:
        message_query = message_query.where(
            Message.conversation_id == conversation_id
        )

    if current_summary is not None:
        message_query = message_query.where(
            Message.id > current_summary.through_message_id,
        )

    saved_messages = list(
        await session.scalars(
            message_query.order_by(Message.id),
        )
    )

    messages_to_compact = select_messages_to_compact(
        saved_messages,
        messages_to_keep,
    )

    transcript = get_buffer_string(
        [to_summary_message(message) for message in messages_to_compact],
        human_prefix="用户",
        ai_prefix="助手",
    )

    input_parts: list[str] = []

    if current_summary is not None:
        input_parts.append(f"现有摘要（其中的信息必须延续）：\n{current_summary.content}")

    input_parts.append(f"后续原始对话（按时间先后）：\n{transcript}")
    compaction_input = "\n\n".join(input_parts)

    # rollback 会让 ORM 对象过期，所以先复制写入摘要所需的普通值。
    source_summary_id = current_summary.id if current_summary is not None else None
    compacted_through_message_id = messages_to_compact[-1].id

    # 摘要模型可能需要等待较长时间，调用前结束上面的只读事务。
    await session.rollback()

    response = await model.ainvoke(
        [
            SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
            HumanMessage(content=compaction_input),
        ],
        config={
            "metadata": {
                "lc_source": "conversation_compaction",
            }
        },
    )

    summary_content = response.text.strip()

    if not summary_content:
        raise RuntimeError("模型返回了空的对话摘要")

    usage = response.usage_metadata

    if usage is None:
        raise RuntimeError("模型没有返回摘要调用的 token usage")

    summary = ConversationSummary(
        user_id=user_id,
        conversation_id=conversation_id,
        through_message_id=compacted_through_message_id,
        source_summary_id=source_summary_id,
        content=summary_content,
        model=settings.hachimi_model_name,
        prompt_version=SUMMARY_PROMPT_VERSION,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )

    session.add(summary)
    await session.commit()

    return summary
