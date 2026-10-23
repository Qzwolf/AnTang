"""血糖读数的只读工具。

患者自己在 App 里记的读数已经每轮放进提示词（近 7 天摘要 + 最近 5 条），
这个工具是给「想再往前看一点」用的：模型可以按天数和条数要明细。
不写库、不判读——判读留给对话本身。
"""

from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.database import session_factory as default_session_factory
from antang_api.health_profile.glucose import (
    GLUCOSE_CONTEXT_LABELS,
    list_glucose_readings,
    summarize_glucose,
    to_mmol,
)

READ_GLUCOSE_TOOL_NAME = "read_glucose_readings"


class GlucoseReadRequest(BaseModel):
    """要看多长窗口、多少条患者自己记的血糖。"""

    model_config = ConfigDict(extra="forbid")

    days: Annotated[
        int,
        Field(
            ge=1,
            le=90,
            description="往前看多少天，默认 7 天；患者说「这两个月」时才用大值。",
        ),
    ] = 7
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="最多返回多少条明细，默认 10 条。"),
    ] = 10


def build_glucose_read_tool(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建读取血糖明细的工具。"""

    @tool(READ_GLUCOSE_TOOL_NAME)
    async def read_glucose_readings(
        request: GlucoseReadRequest,
        runtime: ToolRuntime[AgentContext],
    ) -> dict[str, object]:
        """读取患者自己在 App 里记的血糖读数。

        当患者问到具体的数字（「我上周那次多少」「这两天是不是有点飘」），
        而上下文里的摘要不够时调用。数值单位是 mmol/L。
        这些读数由患者手动录入，可能漏记，不是连续监测；不要说成 CGM 曲线。
        """

        async with sessions() as session:
            summary = await summarize_glucose(
                session,
                user_id=runtime.context.user_id,
                days=request.days,
            )
            readings = await list_glucose_readings(
                session,
                user_id=runtime.context.user_id,
                days=request.days,
                limit=request.limit,
            )

        if summary["count"] == 0:
            return {
                "days": request.days,
                "count": 0,
                "note": "这段时间患者没有记血糖。",
            }

        average = summary["average_mg_dl"]
        minimum = summary["minimum_mg_dl"]
        maximum = summary["maximum_mg_dl"]
        return {
            "days": request.days,
            "count": summary["count"],
            "average_mmol": None if average is None else to_mmol(round(average)),
            "minimum_mmol": None if minimum is None else to_mmol(minimum),
            "maximum_mmol": None if maximum is None else to_mmol(maximum),
            "below_70_count": summary["below_70_count"],
            "readings": [
                {
                    "measured_at": reading.measured_at.isoformat(),
                    "value_mmol": to_mmol(reading.value_mg_dl),
                    "context": GLUCOSE_CONTEXT_LABELS.get(
                        reading.context, reading.context
                    ),
                    "source": reading.source,
                    "note": reading.note,
                }
                for reading in readings
            ],
            "note": "患者自己记的，可能漏记，不是连续监测。",
        }

    return read_glucose_readings
