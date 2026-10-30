"""把"某个时刻打了多少、吃了多少"变成随时间衰减的量。

为什么需要这一步：上一轮把事件压成"近 1 小时求和"，那等于说"10 分钟前打 4 单位"和
"50 分钟前打 4 单位"一样，这在生理上完全说不通。这里用最朴素的药动学近似——

- 速效胰岛素 4 小时内线性衰减（`iob_units`，还剩多少单位在起作用）
- 碳水 3 小时内线性吸收（`carbs_absorbed` 已吸收、`carbs_pending` 还没吸收）

不是为了精确，是为了让"时间"这个维度重新出现在特征里。
"""

from __future__ import annotations

import numpy as np

BOLUS_DURATION_MINUTES = 240.0
CARB_DURATION_MINUTES = 180.0


def _minutes_since(grid: np.ndarray, event: np.datetime64) -> np.ndarray:
    return (grid - event) / np.timedelta64(1, "m")


def onboard(
    grid: np.ndarray,
    event_times: np.ndarray,
    amounts: np.ndarray,
    *,
    duration_minutes: float,
) -> np.ndarray:
    """按线性衰减累加每个事件在网格各时刻的剩余量。"""

    total = np.zeros(grid.size, dtype=np.float64)
    for event, amount in zip(event_times, amounts):
        elapsed = _minutes_since(grid, event)
        active = (elapsed >= 0) & (elapsed < duration_minutes)
        total[active] += amount * (1.0 - elapsed[active] / duration_minutes)
    return total


def minutes_since_last(
    grid: np.ndarray, event_times: np.ndarray, *, cap_minutes: float
) -> np.ndarray:
    """离最近一次事件多少分钟；从没发生过就给上限值。"""

    result = np.full(grid.size, cap_minutes, dtype=np.float64)
    for event in event_times:
        elapsed = _minutes_since(grid, event)
        valid = (elapsed >= 0) & (elapsed < result)
        result[valid] = elapsed[valid]
    return result
