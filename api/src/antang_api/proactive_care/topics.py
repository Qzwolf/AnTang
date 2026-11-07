"""话题冷却：同一个话题，刚主动提过就不再提。

为什么需要它：每周总量、夜间静默、连续没人回就降频，管的是「一共有多少」和
「什么时候」。但同一件事连着两三天反复问，哪怕总量没超，患者感受到的仍然是
「被盯着」——尤其心率这类会自动重复出现的健康事件：同一段偏快的心率窗口过去
之后，第二天又一段，如果每次都弹一次「你现在感觉怎么样」，很快就变成背景噪音，
真正要紧的那一次反而被忽略。

所以这一层只回答一个问题：**这个话题最近是不是刚说过？** 说过就往后推。

作用范围刻意收窄：
- 只作用于健康事件（目前就是心率那条规则）。日常问候有它自己的间隔与额度，
  计划回访是患者自己答应过的事，压着不发等于我们毁约——这两类不在冷却范围内。
- 冷却按「规则 + 方向」算，不按具体事件算：`低心率` 与 `高心率` 是两个话题，
  同一条规则的下一次低心率事件才会被它挡住。
- 患者回了话也不提前解除冷却。理由：冷却要挡的是「提醒本身的密度」，不是
  「他没理我」；把回话当成可以再发一次的许可，正好是最容易变成催促的写法。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import ProactiveCareTask, ProactiveCareTaskStatus

TOPIC_COOLDOWN = timedelta(days=3)


def topic_prefix(event_key: str | None) -> str | None:
    """把一条健康事件键折算成话题前缀。

    事件键的形状是 `版本:方向:事件开始时间`（见 `heart_rate_shadow`），例如
    `heart_rate_v1:low:2026-11-02T03:00:00+00:00`。话题只保留前两段：
    同一版本、同一方向的事件算同一个话题，时间不参与。
    """

    if event_key is None:
        return None
    parts = event_key.split(":", 2)
    if len(parts) < 3 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}:{parts[1]}:"


async def topic_cooldown_until(
    session: AsyncSession,
    *,
    user_id: UUID,
    prefix: str | None,
    now: datetime,
) -> datetime | None:
    """同一话题还在冷却期内时，返回可以再提的时间；否则返回 None。"""

    if prefix is None:
        return None
    last_sent_at = await session.scalar(
        select(func.max(ProactiveCareTask.finished_at)).where(
            ProactiveCareTask.user_id == user_id,
            ProactiveCareTask.status == ProactiveCareTaskStatus.COMPLETED,
            ProactiveCareTask.finished_at.is_not(None),
            ProactiveCareTask.health_event_key.like(f"{prefix}%"),
        )
    )
    if last_sent_at is None:
        return None
    resumes_at = last_sent_at + TOPIC_COOLDOWN
    return resumes_at if resumes_at > now else None
