"""主动关怀的「每周最多联系几次」闸门。

`psychosocial.support.DOSAGE_BY_QUADRANT` 一直只是写进提示词的一句「建议」，
模型心情好时照样可以一周发十条。这里把它变成真的闸门：数一数近七天真正
发出去过几条主动消息，到上限就把任务推到「有额度的那一天」再试。

为什么按消息数而不是按任务数：患者感受到的是消息，任务只是内部记账；同一
个任务重排十次但只发出一条，不该算十次。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from antang_api.agents.proactive_care import PROACTIVE_CARE_AGENT_NAME
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageStatus,
    ProactiveCareTask,
)
from antang_api.psychosocial.support import PsychosocialProfile

WEEK = timedelta(days=7)

# 没做过心理社会评估时按中间档算：不比「怕低血糖但又感觉得到」的人更频繁，
# 也不因为没数据就把人晾一周。评估一旦有了结果，就按四象限的剂量走。
DEFAULT_WEEKLY_LIMIT = 3

# 额度刚空出来的那一秒就立刻发容易被察觉成「定时炸弹」，但也不能因此拖过
# 任务过期时间；这里只留一分钟余量，让简历次到期时间时不会落在过去。
_TAIL = timedelta(minutes=1)


@dataclass(frozen=True)
class WeeklyQuota:
    """近七天主动联系的额度；`resumes_at` 是下一次有额度的最早时刻。"""

    limit: int
    used: int
    resumes_at: datetime | None

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


def _counted_contacts(now: datetime):
    """近七天真正发出去的主动消息，按时间升序。"""

    contact = aliased(Message)
    return (
        select(contact.created_at)
        .join(AgentRun, AgentRun.result_message_id == contact.id)
        .join(ProactiveCareTask, AgentRun.trigger_care_task_id == ProactiveCareTask.id)
        .where(
            AgentRun.user_id == ProactiveCareTask.user_id,
            AgentRun.parent_run_id.is_(None),
            AgentRun.agent_name == PROACTIVE_CARE_AGENT_NAME,
            AgentRun.status == AgentRunStatus.COMPLETED,
            contact.user_id == ProactiveCareTask.user_id,
            contact.status == MessageStatus.COMPLETED,
            contact.created_at > now - WEEK,
        )
        .order_by(contact.created_at)
    )


async def weekly_quota(
    session: AsyncSession,
    *,
    user_id,
    now: datetime,
    profile: PsychosocialProfile | None,
) -> WeeklyQuota:
    """算出这个患者本周还剩几次主动联系，以及什么时候会腾出下一次。"""

    limit = (
        profile.weekly_contact_limit if profile is not None else DEFAULT_WEEKLY_LIMIT
    )
    contacts = list(
        (
            await session.scalars(
                _counted_contacts(now).where(ProactiveCareTask.user_id == user_id)
            )
        ).all()
    )
    used = len(contacts)
    if used < limit:
        return WeeklyQuota(limit=limit, used=used, resumes_at=None)
    # 最早的那条掉出七天窗口时，正好腾出一个名额。
    resumes_at = contacts[used - limit] + WEEK
    if resumes_at <= now:
        resumes_at = now + _TAIL
    return WeeklyQuota(limit=limit, used=used, resumes_at=resumes_at)
