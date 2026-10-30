"""事件级评估：把重叠的窗口还原成"一次低血糖"。

窗口步长 15 分钟、视野 30 分钟，意味着同一个低血糖事件会被 2–3 个窗口各自标成阳性。
按窗口算 AUPRC 时，一次事件被算了三次，指标天然偏乐观，也分不清"抓到三次同一个事件"
和"抓到三个不同事件"。

这里改成事件级：
- **事件**：同一受试者、预测的发作时刻相差不超过 15 分钟的一组阳性窗口。
- **检出**：某次报警（阈值以上的窗口）预测的发作时刻与该事件相差不超过 15 分钟，
  且报警时刻早于发作时刻。
- **提前时间**：事件时刻减去最早一次命中它的报警时刻。
- **误报**：没有命中任何事件的报警，按 45 分钟间隔合并成"一次误报"。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from glucose_forecast.data.windows_common import MultimodalWindows
from glucose_forecast.hypo_task import HYPO_MGDL

__all__ = ["EventReport", "summarise"]

SAMPLE_MINUTES = 5
_EVENT_TOLERANCE_MINUTES = 15.0
_ALERT_GAP_MINUTES = 45.0


def _minutes(times: np.ndarray) -> np.ndarray:
    return times.astype("datetime64[m]").astype(np.float64)


def _cluster(values: np.ndarray, tolerance: float) -> list[list[int]]:
    """把一组时刻按"相邻差 ≤ tolerance"合并成簇，返回每簇的下标。"""

    order = np.argsort(values)
    clusters: list[list[int]] = []
    for position in order:
        if clusters and values[position] - values[clusters[-1][-1]] <= tolerance:
            clusters[-1].append(int(position))
        else:
            clusters.append([int(position)])
    return clusters


@dataclass(frozen=True)
class EventReport:
    events: int
    detected: int
    false_episodes: int
    days: float
    windows_per_event: float
    median_lead_minutes: float

    @property
    def sensitivity(self) -> float:
        return self.detected / self.events if self.events else 0.0

    @property
    def false_alerts_per_day(self) -> float:
        return self.false_episodes / self.days if self.days else 0.0


def summarise(
    windows: MultimodalWindows,
    score: np.ndarray,
    threshold: float,
    *,
    horizon_points: int = 6,
) -> EventReport:
    """按事件统计检出与误报。`windows` 与 `score` 必须逐行对应。"""

    future = windows.future[:, :horizon_points]
    offset = np.argmax(future < HYPO_MGDL, axis=1)
    has_hypo = (future < HYPO_MGDL).any(axis=1)
    onset = _minutes(windows.origin_time) + (offset + 1) * SAMPLE_MINUTES
    origin = _minutes(windows.origin_time)
    alert = score >= threshold

    days = max(windows.origin_time.size * SAMPLE_MINUTES / 60.0 / 24.0, 1e-9)

    events = 0
    detected = 0
    false_episodes = 0
    positive_windows = 0
    leads: list[float] = []

    for subject in np.unique(windows.subject):
        rows = np.flatnonzero(windows.subject == subject)
        subject_hypo = rows[has_hypo[rows]]
        positive_windows += subject_hypo.size

        event_onsets: list[float] = []
        if subject_hypo.size:
            clusters = _cluster(onset[subject_hypo], _EVENT_TOLERANCE_MINUTES)
            event_onsets = [
                float(np.median(onset[subject_hypo[cluster]])) for cluster in clusters
            ]

        matched = [False] * len(event_onsets)
        unmatched: list[float] = []
        for row in rows[alert[rows]]:
            if not has_hypo[row] or not event_onsets:
                unmatched.append(float(origin[row]))
                continue
            distances = [abs(float(onset[row]) - item) for item in event_onsets]
            nearest = int(np.argmin(distances))
            # 报警必须早于发作才叫"预警"；晚于发作的是"事后发现"，按误报处理。
            if distances[nearest] <= _EVENT_TOLERANCE_MINUTES and origin[row] <= event_onsets[nearest]:
                matched[nearest] = True
                leads.append(event_onsets[nearest] - float(origin[row]))
            else:
                unmatched.append(float(origin[row]))

        events += len(event_onsets)
        detected += sum(matched)
        if unmatched:
            false_episodes += len(_cluster(np.array(unmatched), _ALERT_GAP_MINUTES))

    return EventReport(
        events=events,
        detected=detected,
        false_episodes=false_episodes,
        days=float(days),
        windows_per_event=positive_windows / max(events, 1),
        median_lead_minutes=float(np.median(leads)) if leads else 0.0,
    )
