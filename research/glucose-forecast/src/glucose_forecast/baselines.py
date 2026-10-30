"""三个基线。没有赢过这三个的东西不叫模型。

1. persistence —— 未来等于最后一个读数。这个领域最常被省略、又最难打败的基线。
2. drift —— 用最近若干点的斜率线性外推，裁到生理范围内。
3. ridge_ar —— 用整段历史做特征，逐预测步各训一个岭回归。

三者都要先 fit（哪怕 persistence 只是记下预测长度），这样评测循环里没有特例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

GLUCOSE_FLOOR = 40.0
GLUCOSE_CEILING = 400.0


class Forecaster(Protocol):
    name: str

    def fit(self, history: np.ndarray, future: np.ndarray) -> None: ...

    def predict(self, history: np.ndarray) -> np.ndarray: ...


def _clip(values: np.ndarray) -> np.ndarray:
    return np.clip(values, GLUCOSE_FLOOR, GLUCOSE_CEILING)


@dataclass
class Persistence:
    name: str = "persistence"
    horizon: int = 0

    def fit(self, history: np.ndarray, future: np.ndarray) -> None:
        self.horizon = int(future.shape[1])

    def predict(self, history: np.ndarray) -> np.ndarray:
        if self.horizon == 0:
            raise RuntimeError("先 fit 再 predict")
        return _clip(np.repeat(history[:, -1:], self.horizon, axis=1))


@dataclass
class LinearDrift:
    """用最后 slope_points 个点的最小二乘斜率外推。"""

    slope_points: int = 6
    name: str = "linear_drift"
    horizon: int = 0

    def fit(self, history: np.ndarray, future: np.ndarray) -> None:
        self.horizon = int(future.shape[1])

    def predict(self, history: np.ndarray) -> np.ndarray:
        if self.horizon == 0:
            raise RuntimeError("先 fit 再 predict")
        window = history[:, -self.slope_points :]
        steps = np.arange(window.shape[1], dtype=np.float64)
        steps -= steps.mean()
        denominator = float(np.sum(steps**2)) or 1.0
        slope = (window - window.mean(axis=1, keepdims=True)) @ steps / denominator
        deltas = slope[:, None] * np.arange(1, self.horizon + 1)[None, :]
        return _clip(history[:, -1:] + deltas)


@dataclass
class RidgeAutoregression:
    """逐预测步的岭回归：特征 = 标准化后的整段历史。

    用闭式解（正规方程）而不是 sklearn，是为了把这个仓库的依赖压到 numpy。
    """

    alpha: float = 1.0
    name: str = "ridge_ar"
    weights: list[np.ndarray] = field(default_factory=list)
    mean: float = 0.0
    scale: float = 1.0

    def _design(self, history: np.ndarray) -> np.ndarray:
        normalized = (history - self.mean) / self.scale
        ones = np.ones((normalized.shape[0], 1), dtype=np.float64)
        return np.concatenate([normalized, ones], axis=1)

    def fit(self, history: np.ndarray, future: np.ndarray) -> None:
        if history.shape[0] < 2:
            raise ValueError("岭回归至少需要 2 个窗口")
        self.mean = float(history.mean())
        self.scale = float(history.std()) or 1.0
        design = self._design(history)
        gram = design.T @ design
        penalty = self.alpha * np.eye(gram.shape[0])
        penalty[-1, -1] = 0.0  # 不惩罚截距
        self.weights = []
        for step in range(future.shape[1]):
            target = (future[:, step] - self.mean) / self.scale
            coefficients = np.linalg.solve(gram + penalty, design.T @ target)
            self.weights.append(coefficients)

    def predict(self, history: np.ndarray) -> np.ndarray:
        if not self.weights:
            raise RuntimeError("先 fit 再 predict")
        design = self._design(history)
        stacked = np.stack([design @ weight for weight in self.weights], axis=1)
        return _clip(stacked * self.scale + self.mean)


def default_baselines() -> list[Forecaster]:
    return [Persistence(), LinearDrift(), RidgeAutoregression()]
