"""统一的数据形状：任何 loader 解析完都要落到这里，模型与指标只认这两个类型。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_MINUTES = 5
SAMPLE_SECONDS = SAMPLE_MINUTES * 60
MGDL_PER_MMOLL = 18.0182

# 生理上限：低于 20 或高于 600 mg/dL 的读数几乎都是设备故障或单位错误。
PLAUSIBLE_MIN_MGDL = 20.0
PLAUSIBLE_MAX_MGDL = 600.0


@dataclass(frozen=True)
class CgmSeries:
    """一个受试者的原始 CGM 读数（不一定等间隔，可能重复、可能缺）。"""

    subject: str
    timestamps: np.ndarray  # datetime64[ns]
    glucose_mgdl: np.ndarray  # float64

    def __post_init__(self) -> None:
        if self.timestamps.dtype != np.dtype("datetime64[ns]"):
            raise TypeError(f"{self.subject}: timestamps 必须是 datetime64[ns]")
        if self.timestamps.ndim != 1:
            raise ValueError(f"{self.subject}: timestamps 必须是一维")
        if self.glucose_mgdl.shape != self.timestamps.shape:
            raise ValueError(
                f"{self.subject}: 读数与时间戳长度不一致 "
                f"({self.glucose_mgdl.size} vs {self.timestamps.size})"
            )
        if len(self) > 1 and not np.all(np.diff(self.timestamps) >= np.timedelta64(0, "s")):
            raise ValueError(f"{self.subject}: 时间戳必须严格升序，不排序就等于悄悄改数据")

    def __len__(self) -> int:
        return int(self.glucose_mgdl.size)

    @property
    def span_days(self) -> float:
        if len(self) < 2:
            return 0.0
        return float((self.timestamps[-1] - self.timestamps[0]) / np.timedelta64(1, "D"))


@dataclass(frozen=True)
class EventSeries:
    """胰岛素、碳水等离散事件。amount 的单位由 kind 决定（U / g）。"""

    subject: str
    kind: str
    timestamps: np.ndarray
    amount: np.ndarray

    def __post_init__(self) -> None:
        if self.timestamps.shape != self.amount.shape:
            raise ValueError(f"{self.subject}/{self.kind}: 事件时间与剂量长度不一致")

    def __len__(self) -> int:
        return int(self.amount.size)

    def total(self) -> float:
        return float(self.amount.sum())


@dataclass(frozen=True)
class RegularGrid:
    """把原始读数吸附到固定 5 分钟网格后的结果；缺口用 NaN 表示而不是插值填平。

    缺口为什么保留：CGM 断连 2 小时之后线性插值出来的曲线是假的，用它训练等于
    教模型学插值器。窗口构造时按 NaN 丢弃这些窗口，是更诚实的做法。
    """

    subject: str
    timestamps: np.ndarray  # datetime64[ns]，等间隔
    glucose_mgdl: np.ndarray  # float64，缺失为 NaN
    observed: np.ndarray  # bool，该格是否真的有读数

    def __len__(self) -> int:
        return int(self.glucose_mgdl.size)

    @property
    def coverage(self) -> float:
        return float(self.observed.mean()) if len(self) else 0.0

    def valid_values(self) -> np.ndarray:
        return self.glucose_mgdl[self.observed]


def clean_readings(
    timestamps: np.ndarray,
    glucose: np.ndarray,
    *,
    subject: str,
    low: float = PLAUSIBLE_MIN_MGDL,
    high: float = PLAUSIBLE_MAX_MGDL,
) -> tuple[np.ndarray, np.ndarray]:
    """丢掉非有限值与超出生理范围的读数，并去重（同一时间戳保留均值）。"""

    finite = np.isfinite(glucose)
    timestamps, glucose = timestamps[finite], glucose[finite]
    in_range = (glucose >= low) & (glucose <= high)
    timestamps, glucose = timestamps[in_range], glucose[in_range]
    if timestamps.size == 0:
        raise ValueError(f"{subject}: 清洗后没有任何可用读数")
    unique, inverse = np.unique(timestamps, return_inverse=True)
    if unique.size != timestamps.size:
        sums = np.zeros(unique.size, dtype=np.float64)
        counts = np.zeros(unique.size, dtype=np.float64)
        np.add.at(sums, inverse, glucose)
        np.add.at(counts, inverse, 1.0)
        glucose = sums / counts
        timestamps = unique
    return timestamps, glucose


def to_regular_grid(
    series: CgmSeries,
    *,
    sample_seconds: int = SAMPLE_SECONDS,
    clean: bool = True,
) -> RegularGrid:
    """把不等间隔读数吸附到固定网格；同一格多条读数取均值。"""

    timestamps = series.timestamps
    glucose = series.glucose_mgdl.astype(np.float64, copy=False)
    if clean:
        timestamps, glucose = clean_readings(timestamps, glucose, subject=series.subject)

    step = np.timedelta64(sample_seconds, "s")
    origin = timestamps[0]
    total = int((timestamps[-1] - origin) / step) + 1
    index = np.rint((timestamps - origin) / step).astype(np.int64)
    index = np.clip(index, 0, total - 1)

    sums = np.zeros(total, dtype=np.float64)
    counts = np.zeros(total, dtype=np.float64)
    np.add.at(sums, index, glucose)
    np.add.at(counts, index, 1.0)

    observed = counts > 0
    values = np.full(total, np.nan, dtype=np.float64)
    values[observed] = sums[observed] / counts[observed]
    grid_times = origin + np.arange(total) * step
    return RegularGrid(
        subject=series.subject,
        timestamps=grid_times.astype("datetime64[ns]"),
        glucose_mgdl=values,
        observed=observed,
    )
