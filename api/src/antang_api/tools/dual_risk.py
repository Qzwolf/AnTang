"""把「血糖危不危险」和「他自己感没感觉到」一起记下来的工具。

只记一个风险等级会把最要紧的一档弄丢：血糖已经很低、他却毫无感觉。这一档在
复盘时决定该练什么（察觉），也决定当时该说什么（提醒而不是安抚）。
"""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.database import session_factory as default_session_factory
from antang_api.health_profile.glucose import glucose_targets
from antang_api.models import AgentRun, GlucoseReading, Message, MessageRole
from antang_api.psychosocial.dual_risk import (
    LABEL_LABELS,
    PHYSIOLOGICAL_LABELS,
    describe,
    physiological_risk,
    queue_follow_up,
    record_annotation,
)

RECORD_DUAL_RISK_TOOL_NAME = "record_dual_risk"

FeltRisk = Annotated[
    str,
    Field(
        pattern="^(none|some|strong)$",
        description=(
            "none=他说没什么感觉；some=他说有点难受；strong=他说难受得很厉害。"
            "他没说感觉就不要调这个工具，先问一句。"
        ),
    ),
]
ValueMmol = Annotated[
    float | None,
    Field(
        ge=1.0,
        le=33.3,
        description="他刚说的那个血糖值（mmol/L）；如果他报的是 mg/dL，先自己换算再填。",
    ),
]
MeasuredAt = Annotated[
    str | None,
    Field(description="测这个数的时间，ISO 8601（如 2026-11-02T17:46+08:00）；不填就按现在。"),
]
Note = Annotated[
    str | None,
    Field(max_length=200, description="他自己描述感觉的原话片段，用来事后核对。"),
]


async def _trigger_message_id(
    session: AsyncSession, runtime: ToolRuntime[AgentContext]
) -> UUID | None:
    run = await session.get(AgentRun, runtime.context.run_id)
    if run is None or run.trigger_message_id is None:
        return None
    message = await session.get(Message, run.trigger_message_id)
    if message is None or message.role != MessageRole.USER:
        return None
    return message.id


def _parse_when(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"时间「{value}」看不懂，请用 2026-11-02T17:46+08:00 这样的格式。"
        ) from error
    if parsed.tzinfo is None:
        raise ValueError("时间要带时区，比如 2026-11-02T17:46+08:00。")
    return parsed


def build_dual_risk_tool(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建「一起记生理风险和感受」的工具。"""

    @tool(RECORD_DUAL_RISK_TOOL_NAME)
    async def record_dual_risk(
        runtime: ToolRuntime[AgentContext],
        felt_risk: FeltRisk,
        value_mmol: ValueMmol = None,
        glucose_reading_id: str | None = None,
        measured_at: MeasuredAt = None,
        note: Note = None,
    ) -> dict[str, object]:
        """他报了一个血糖数、并且说了自己当时什么感觉时，两边一起记下来。

        这两个要同时看：血糖 4.2 但慌得不行，和血糖 3.1 却毫无感觉，处理方式
        不一样。生理风险由系统按他自己的目标范围和危险阈值算，你只填他说的感觉。
        `value_mmol` 填他刚说的那个数；如果已经有这条读数，也可以给
        `glucose_reading_id`。两个都没给就先别调——先把数字记下来再说。
        """

        async with sessions() as session:
            reading: GlucoseReading | None = None
            if glucose_reading_id is not None:
                reading = await session.get(GlucoseReading, UUID(glucose_reading_id))
                if reading is None or reading.user_id != runtime.context.user_id:
                    raise ValueError("找不到这条血糖读数，先确认是哪一条再记。")
            elif value_mmol is None:
                raise ValueError(
                    "要先把血糖数字记下来（用记血糖那个工具），再一起记感觉。"
                )

            when = reading.measured_at if reading is not None else _parse_when(measured_at)
            if reading is not None:
                value_mg_dl = reading.value_mg_dl
            else:
                assert value_mmol is not None
                value_mg_dl = int(round(value_mmol * 18))
            low, high = await glucose_targets(
                session, user_id=runtime.context.user_id
            )
            physiological = physiological_risk(
                value_mg_dl, target_low_mg_dl=low, target_high_mg_dl=high
            )
            view = describe(physiological, felt_risk)

            if reading is None:
                resolved = await session.execute(
                    select(GlucoseReading)
                    .where(
                        GlucoseReading.user_id == runtime.context.user_id,
                        GlucoseReading.measured_at == when,
                        GlucoseReading.value_mg_dl == value_mg_dl,
                    )
                    .limit(1)
                )
                reading = resolved.scalar_one_or_none()

            annotation = await record_annotation(
                session,
                user_id=runtime.context.user_id,
                measured_at=when,
                value_mg_dl=value_mg_dl,
                physiological_risk=physiological,
                felt_risk=felt_risk,
                glucose_reading_id=reading.id if reading is not None else None,
                source_message_id=await _trigger_message_id(session, runtime),
                note=note or "",
            )
            # 「低了却没感觉」这一档排一次几天后的回访；同一夜只排一次。
            follow_up = await queue_follow_up(
                session,
                user_id=runtime.context.user_id,
                annotation=annotation,
                now=datetime.now(timezone.utc),
            )
            await session.commit()
            return {
                "recorded": True,
                "label": annotation.label,
                "label_text": LABEL_LABELS[view.label],
                "physiological": PHYSIOLOGICAL_LABELS[physiological],
                "felt": view.felt,
                "when": when.isoformat(),
                "linked_reading": reading is not None,
                "follow_up_at": follow_up.due_at.isoformat() if follow_up else None,
            }

    return record_dual_risk


__all__ = [
    "RECORD_DUAL_RISK_TOOL_NAME",
    "build_dual_risk_tool",
]
