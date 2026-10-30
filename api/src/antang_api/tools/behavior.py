from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.behavioral import record_behavior, record_belief
from antang_api.database import session_factory as default_session_factory
from antang_api.models import AgentRun, Message, MessageRole

LOG_BEHAVIOR_TOOL_NAME = "log_fear_behavior"
RECORD_BELIEF_TOOL_NAME = "record_hypoglycemia_belief"


BehaviorKind = Annotated[
    Literal[
        "extra_check",
        "night_check",
        "comfort_meal",
        "exercise_avoidance",
        "insulin_hold",
        "insulin_change",
    ],
    Field(
        description=(
            "extra_check=额外多测了一次血糖；night_check=夜间或睡前反复确认；"
            "comfort_meal=因为担心而加餐；exercise_avoidance=因担心低血糖而放弃运动或不敢出门；"
            "insulin_hold=因担心低血糖而少打或推迟胰岛素；"
            "insulin_change=自己调整了胰岛素剂量（加量或减量都算），没跟医生说。"
        )
    ),
]
BehaviorCount = Annotated[
    int,
    Field(ge=1, le=50, description="这次提到发生了几次，只说过一次就填 1。"),
]
BehaviorOccurredAt = Annotated[
    datetime | None,
    Field(description="用户明确说出的发生时间，带时区；只说了「今天」就用当前时间。"),
]
BehaviorNote = Annotated[
    str | None,
    Field(
        max_length=200,
        description="用户的原话片段，用于事后核对；不要改写、不要补充用户没说的内容。",
    ),
]


class FearBehaviorRequest(BaseModel):
    """用户主动说出的一次「因为怕低血糖而做（或不做）的事」。"""

    model_config = ConfigDict(extra="forbid")

    kind: BehaviorKind
    count: BehaviorCount = 1
    occurred_at: BehaviorOccurredAt = None
    note: BehaviorNote = None


BeliefCode = Annotated[
    Literal[
        "hypo_causes_death",
        "hypo_causes_coma",
        "hypo_is_losing_control",
        "hypo_reveals_weakness",
        "hypo_is_my_fault",
        "hypo_always_repeats",
        "hypo_cannot_be_detected",
        "hypo_blocks_normal_life",
    ],
    Field(description="只能从这八个节点里选最贴近的一个，不要自创。"),
]
BeliefQuote = Annotated[
    str,
    Field(
        min_length=1,
        max_length=300,
        description="逐字复制用户表达这条信念的原话，一个字都不要改。",
    ),
]
BeliefOccurrence = Annotated[
    Literal["mentioned", "reframed", "denied"],
    Field(
        description=(
            "mentioned=用户新说出这条信念；reframed=用户在这轮里对它有了松动或新的看法；"
            "denied=用户明确否认了这条信念。"
        )
    ),
]
BeliefTriggerContext = Annotated[
    str | None,
    Field(max_length=200, description="当时的情境，例如「今晚一个人睡」「开会前」。"),
]
BeliefIntensity = Annotated[
    int | None,
    Field(
        ge=0,
        le=100,
        description="对话中能看出的当时恐惧强度 0–100；没有依据就留空，不要猜。",
    ),
]


class BeliefRequest(BaseModel):
    """用户在对话里表露的一次低血糖灾难化信念。"""

    model_config = ConfigDict(extra="forbid")

    belief_code: BeliefCode
    quote: BeliefQuote
    occurrence: BeliefOccurrence = "mentioned"
    trigger_context: BeliefTriggerContext = None
    fear_intensity: BeliefIntensity = None


async def _trigger_message_id(
    session: AsyncSession,
    runtime: ToolRuntime[AgentContext],
) -> UUID | None:
    """取触发本轮对话的用户消息，用于把记录回溯到原话。"""

    run = await session.get(AgentRun, runtime.context.run_id)
    if run is None or run.user_id != runtime.context.user_id:
        raise RuntimeError("行为与信念记录需要由当前用户消息的 Core Agent 调用")
    if run.trigger_message_id is None:
        return None
    message = await session.get(Message, run.trigger_message_id)
    if message is None or message.role != MessageRole.USER:
        return None
    return message.id


def build_behavior_tools(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> tuple[BaseTool, BaseTool]:
    """创建行为代理与信念观测两个记录工具。"""

    @tool(LOG_BEHAVIOR_TOOL_NAME)
    async def log_fear_behavior(
        runtime: ToolRuntime[AgentContext],
        kind: BehaviorKind,
        count: BehaviorCount = 1,
        occurred_at: BehaviorOccurredAt = None,
        note: BehaviorNote = None,
    ) -> dict[str, object]:
        """记录用户亲口说出的、因担心低血糖而发生的一次行为。

        用户这一轮说了具体行为时就要记（例如「刚才又多测了两次」「怕夜里低血糖所以
        睡前吃了饼干」「因为怕低血糖没去跑步」「自己把胰岛素减了一个单位」），
        只在回复里回应、不调工具，这条行为就没有留下来。
        不能从语气或推断里编造行为，也不能把建议说成用户做过的事。
        一轮里同一行为只记一次，次数放进 count。
        """

        request = FearBehaviorRequest(
            kind=kind,
            count=count,
            occurred_at=occurred_at,
            note=note,
        )
        if request.note is None:
            raise ValueError("记录行为必须附上用户原话片段")

        async with sessions() as session:
            message_id = await _trigger_message_id(session, runtime)
            log = record_behavior(
                session,
                user_id=runtime.context.user_id,
                kind=request.kind,
                count=request.count,
                occurred_at=request.occurred_at,
                note=request.note,
                source_message_id=message_id,
            )
            await session.commit()
            return {
                "recorded": True,
                "kind": log.kind,
                "count": log.count,
                "occurred_at": log.occurred_at.isoformat(),
            }

    @tool(RECORD_BELIEF_TOOL_NAME)
    async def record_hypoglycemia_belief(
        runtime: ToolRuntime[AgentContext],
        belief_code: BeliefCode,
        quote: BeliefQuote,
        occurrence: BeliefOccurrence = "mentioned",
        trigger_context: BeliefTriggerContext = None,
        fear_intensity: BeliefIntensity = None,
    ) -> dict[str, object]:
        """记录用户表露的一条低血糖灾难化信念。

        只要用户这一轮的话里出现了这类信念（会死、会昏迷、一低就失控、发现不了低血糖、
        永远会再来、是自己没做好、怕被看成软弱、因为怕低血糖不敢过正常生活），就要调这个
        工具把它落库；只在回复里接住情绪、不调工具，等于这条信念没有留下来。
        `quote` 必须逐字复制，不能复述、不能合并两句。一轮里同一条信念出现多次也只记一次。
        如果用户只是陈述一般知识或转述别人，不要记成用户自己的信念。
        """

        request = BeliefRequest(
            belief_code=belief_code,
            quote=quote,
            occurrence=occurrence,
            trigger_context=trigger_context,
            fear_intensity=fear_intensity,
        )
        async with sessions() as session:
            message_id = await _trigger_message_id(session, runtime)
            event = record_belief(
                session,
                user_id=runtime.context.user_id,
                belief_code=request.belief_code,
                quote=request.quote,
                occurrence=request.occurrence,
                trigger_context=request.trigger_context,
                fear_intensity=request.fear_intensity,
                source_message_id=message_id,
            )
            await session.commit()
            return {
                "recorded": True,
                "belief_code": event.belief_code,
                "occurrence": event.occurrence,
            }

    return log_fear_behavior, record_hypoglycemia_belief
