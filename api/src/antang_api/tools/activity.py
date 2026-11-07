"""对话里补记运动与睡眠。

和「记这一餐」同样的约束：只记患者说过的事实，时间必须是真实发生时间（昨晚没睡好，
记的是昨晚），不推断、不算消耗、不评价够不够。运动时长要患者自己说；只说「动了一下」
就先问多久。
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.database import session_factory as default_session_factory
from antang_api.models import ActivityLog, AgentRun, Message, MessageRole, SleepLog

RECORD_ACTIVITY_TOOL_NAME = "record_activity"
RECORD_SLEEP_TOOL_NAME = "record_sleep"

# 补记往前 30 天；未来最多容忍 2 分钟（手机时钟误差）。
BACKFILL_LIMIT = timedelta(days=30)
FUTURE_TOLERANCE = timedelta(minutes=2)

ActivityKind = Annotated[
    Literal["walk", "run", "cycle", "strength", "swim", "yoga", "other"],
    Field(description="走路 / 跑步 / 骑行 / 力量 / 游泳 / 瑜伽 / 其它。"),
]
ActivityMinutes = Annotated[
    int,
    Field(
        ge=1,
        le=600,
        description="这次运动持续多少分钟。患者没说就先问，不要自己编一个数。",
    ),
]
ActivityIntensity = Annotated[
    Literal["light", "moderate", "vigorous"],
    Field(description="轻 / 中等 / 挺累；患者没说就按「中等」。"),
]
PerformedAt = Annotated[
    datetime,
    Field(
        description=(
            "这次运动真实发生的时刻，带时区。上午去走的，写上午那个时刻。"
        )
    ),
]
FellAsleepAt = Annotated[
    datetime, Field(description="入睡时刻，带时区。跨零点也照实写。")
]
WokeAt = Annotated[datetime, Field(description="醒来时刻，带时区。")]
LogNote = Annotated[
    str | None,
    Field(max_length=200, description="患者原话片段或当时情形，可选。"),
]


def _as_datetime(value: datetime | str) -> datetime:
    """接受 ISO 字符串：模型和测试都可能直接给字符串。

    正常链路里 LangChain 会先按 schema 解析，但工具被直接调用时不会，
    这里兜一层，免得「是不是带了时区」的判断因为类型不对而变成 AttributeError。
    """

    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _check_time(value: datetime | str, now: datetime, what: str) -> datetime:
    moment = _as_datetime(value)
    if moment.tzinfo is None:
        raise ValueError("时间要带时区，例如 2026-11-06T22:30:00+08:00")
    if moment > now + FUTURE_TOLERANCE:
        raise ValueError(f"这个{what}时间还没到，确认一下是不是记错了")
    if moment < now - BACKFILL_LIMIT:
        raise ValueError("只支持补记最近 30 天内的记录")
    return moment.astimezone(UTC)


async def _trigger_message_id(
    session: AsyncSession,
    runtime: ToolRuntime[AgentContext],
) -> UUID | None:
    """把记录回溯到触发它的那条用户消息。"""

    run = await session.get(AgentRun, runtime.context.run_id)
    if run is None or run.user_id != runtime.context.user_id:
        raise RuntimeError("记录必须由当前用户消息的 Core Agent 调用")
    if run.trigger_message_id is None:
        return None
    message = await session.get(Message, run.trigger_message_id)
    if message is None or message.role != MessageRole.USER:
        return None
    return message.id


def build_activity_tools(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> tuple[BaseTool, BaseTool]:
    """创建「记这次运动」「记这晚睡眠」两个工具。"""

    @tool(RECORD_ACTIVITY_TOOL_NAME)
    async def record_activity(
        runtime: ToolRuntime[AgentContext],
        kind: ActivityKind,
        minutes: ActivityMinutes,
        performed_at: PerformedAt,
        intensity: ActivityIntensity = "moderate",
        note: LogNote = None,
    ) -> dict[str, object]:
        """把患者做过的一次运动记下来（只记录，不算消耗、不评价够不够）。

        患者说了「出去走了走」但没说多久时不要编：先问一句大概多少分钟，再调用。
        患者怕低血糖不敢运动、或者只肯在屋里动，也照实记——这条记录本身就是有用的事实。
        只有这个工具返回 recorded=true 才能说「已经记下来了」。
        """

        now = datetime.now(UTC)
        performed_at = _check_time(performed_at, now, "运动")
        async with sessions() as session:
            await _trigger_message_id(session, runtime)
            log = ActivityLog(
                user_id=runtime.context.user_id,
                performed_at=performed_at,
                kind=kind,
                minutes=minutes,
                intensity=intensity,
                note=(note or "").strip() or None,
            )
            session.add(log)
            await session.commit()
            return {
                "recorded": True,
                "kind": log.kind,
                "minutes": log.minutes,
                "intensity": log.intensity,
                "performed_at": log.performed_at.isoformat(),
            }

    @tool(RECORD_SLEEP_TOOL_NAME)
    async def record_sleep(
        runtime: ToolRuntime[AgentContext],
        fell_asleep_at: FellAsleepAt,
        woke_at: WokeAt,
        note: LogNote = None,
    ) -> dict[str, object]:
        """把患者睡的一觉记下来（只记几点睡、几点醒，不做睡眠分期）。

        只说「睡得不好」而没有时间时，先问大概几点睡、几点醒，再调用。
        醒来时间必须晚于入睡时间，一段不超过 24 小时；超出会报错，照报错里的话问清楚。
        只有这个工具返回 recorded=true 才能说「已经记下来了」。
        """

        now = datetime.now(UTC)
        fell_asleep_at = _check_time(fell_asleep_at, now, "入睡")
        woke_at = _check_time(woke_at, now, "醒来")
        if woke_at <= fell_asleep_at:
            raise ValueError("醒来时间要晚于入睡时间：确认一下是前一晚几点睡、几点醒")
        if woke_at - fell_asleep_at > timedelta(hours=24):
            raise ValueError("这一段超过 24 小时了：是不是把两晚记成了一次？")
        async with sessions() as session:
            await _trigger_message_id(session, runtime)
            log = SleepLog(
                user_id=runtime.context.user_id,
                fell_asleep_at=fell_asleep_at,
                woke_at=woke_at,
                note=(note or "").strip() or None,
            )
            session.add(log)
            await session.commit()
            return {
                "recorded": True,
                "fell_asleep_at": log.fell_asleep_at.isoformat(),
                "woke_at": log.woke_at.isoformat(),
                "hours": round(
                    (log.woke_at - log.fell_asleep_at).total_seconds() / 3600, 1
                ),
            }

    return record_activity, record_sleep


__all__ = [
    "RECORD_ACTIVITY_TOOL_NAME",
    "RECORD_SLEEP_TOOL_NAME",
    "BACKFILL_LIMIT",
    "FUTURE_TOLERANCE",
    "build_activity_tools",
]
