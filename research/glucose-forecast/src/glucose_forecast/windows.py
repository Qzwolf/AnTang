"""把连续网格切成「过去 N 点 → 未来 M 点」的窗口。

默认 6 小时历史（72 点）预测 1 小时（12 点），并把历史切成 6 段、每段 12 点 ——
段长等于预测长度，这样任务就是「用前一段预测下一段」，也是给多模态编码器
（每段一个向量）预留的形状。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from glucose_forecast.series import RegularGrid

DEFAULT_HISTORY_POINTS = 72  # 6 小时 @ 5 分钟
DEFAULT_HORIZON_POINTS = 12  # 1 小时 @ 5 分钟
DEFAULT_SEGMENTS = 6  # 每段 12 点


@dataclass(frozen=True)
class WindowSet:
    """一批可训练的窗口。subject 用于按受试者划分，绝不跨人拼接。"""

    subject: str
    history: np.ndarray  # (n, history_points)
    future: np.ndarray  # (n, horizon_points)
    origin_time: np.ndarray  # (n,) datetime64[ns]，历史最后一个点的时间
    origin_index: np.ndarray  # (n,) int，在网格中的下标

    def __len__(self) -> int:
        return int(self.history.shape[0])

    def __post_init__(self) -> None:
        if self.history.ndim != 2 or self.future.ndim != 2:
            raise ValueError("history / future 必须是二维")
        if self.history.shape[0] != self.future.shape[0]:
            raise ValueError("history 与 future 的窗口数不一致")
        if not (np.isfinite(self.history).all() and np.isfinite(self.future).all()):
            raise ValueError("窗口里不允许出现 NaN；构造时应已丢弃含缺口的窗口")


def build_windows(
    grid: RegularGrid,
    *,
    history_points: int = DEFAULT_HISTORY_POINTS,
    horizon_points: int = DEFAULT_HORIZON_POINTS,
    stride: int = 1,
) -> WindowSet:
    """滑窗；任何落在缺口上的窗口整条丢弃。"""

    if history_points < 1 or horizon_points < 1 or stride < 1:
        raise ValueError("history_points / horizon_points / stride 必须为正")

    values = grid.glucose_mgdl
    total = values.size
    span = history_points + horizon_points
    if total < span:
        return _empty(grid.subject, history_points, horizon_points)

    # 前缀和快速判断窗口内是否有缺失：NaN 的数量 = 窗口长度 - 有效数量。
    missing = np.isnan(values).astype(np.int32)
    prefix = np.concatenate(([0], np.cumsum(missing)))
    starts = np.arange(0, total - span + 1, stride)
    window_missing = prefix[starts + span] - prefix[starts]
    keep = window_missing == 0
    starts = starts[keep]
    if starts.size == 0:
        return _empty(grid.subject, history_points, horizon_points)

    rows = starts[:, None] + np.arange(span)[None, :]
    windows = values[rows]
    history = windows[:, :history_points]
    future = windows[:, history_points:]
    origin_index = starts + history_points - 1
    return WindowSet(
        subject=grid.subject,
        history=history,
        future=future,
        origin_time=grid.timestamps[origin_index],
        origin_index=origin_index,
    )


def _empty(subject: str, history_points: int, horizon_points: int) -> WindowSet:
    return WindowSet(
        subject=subject,
        history=np.empty((0, history_points), dtype=np.float64),
        future=np.empty((0, horizon_points), dtype=np.float64),
        origin_time=np.empty(0, dtype="datetime64[ns]"),
        origin_index=np.empty(0, dtype=np.int64),
    )


def stack_windows(sets: list[WindowSet]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """把多个受试者的窗口拼起来，同时返回每条窗口来自哪个受试者。"""

    if not sets:
        raise ValueError("没有可拼接的窗口集合")
    history = np.concatenate([item.history for item in sets], axis=0)
    future = np.concatenate([item.future for item in sets], axis=0)
    subjects: list[str] = []
    for item in sets:
        subjects.extend([item.subject] * len(item))
    return history, future, subjects


def history_segments(
    history: np.ndarray, *, segments: int = DEFAULT_SEGMENTS
) -> np.ndarray:
    """把 (n, H) 的历史切成 (n, segments, H/segments)，供分段编码器逐段取向量。"""

    if history.ndim != 2:
        raise ValueError("history 必须是二维 (n, H)")
    points = history.shape[1]
    if points % segments != 0:
        raise ValueError(f"历史长度 {points} 不能被段数 {segments} 整除")
    return history.reshape(history.shape[0], segments, points // segments)
