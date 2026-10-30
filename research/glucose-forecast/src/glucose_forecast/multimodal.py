"""多模态消融：在 HUPA-UCM 上量"加一个模态到底有没有用"。

模型刻意保持简单——线性岭回归与一个小 MLP——因为这里的任务是**比较模态**，不是刷
SOTA。要回答的问题是：把胰岛素、碳水、可穿戴分别加进来，误差掉了多少，还是没掉。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from glucose_forecast.data.hupa_windows import CHANNEL_NAMES, MultimodalWindows

HISTORY_MINUTES = 5


def _slope(values: np.ndarray, points: int) -> np.ndarray:
    span = values[:, -points:]
    minutes = np.arange(span.shape[1]) * HISTORY_MINUTES
    centered = minutes - minutes.mean()
    return (span - span.mean(axis=1, keepdims=True)) @ centered / (centered @ centered)


def build_features(
    windows: MultimodalWindows,
    *,
    use_insulin: bool = False,
    use_carbs: bool = False,
    use_wearable: bool = False,
    use_onboard: bool = False,
) -> np.ndarray:
    history = windows.history
    glucose = history[:, -1]
    columns = [
        glucose,
        history[:, -3:].mean(axis=1),
        history[:, -6:].mean(axis=1),
        history[:, -12:].mean(axis=1),
        history[:, -12:].std(axis=1),
        history[:, -12:].min(axis=1),
        _slope(history, 6),
        _slope(history, 12),
        glucose - history[:, -7],
        glucose - history[:, -13],
    ]
    if use_insulin:
        bolus = windows.channels["bolus_units"]
        columns += [
            bolus[:, -6:].sum(axis=1),
            bolus[:, -12:].sum(axis=1),
            bolus[:, -36:].sum(axis=1),
            (bolus[:, -12:] > 0).sum(axis=1).astype(np.float64),
        ]
    if use_carbs:
        carbs = windows.channels["carbs_grams"]
        columns += [
            carbs[:, -6:].sum(axis=1),
            carbs[:, -12:].sum(axis=1),
            carbs[:, -36:].sum(axis=1),
        ]
    if use_onboard:
        iob = windows.channels["iob_units"]
        absorbed = windows.channels["carbs_absorbed"]
        since_bolus = windows.channels["minutes_since_bolus"]
        since_carbs = windows.channels["minutes_since_carbs"]
        columns += [
            iob[:, -1],
            iob[:, -1] - iob[:, -13],
            absorbed[:, -1] - absorbed[:, -13],
            since_bolus[:, -1],
            since_carbs[:, -1],
        ]
    if use_wearable:
        heart = windows.channels["heart_rate_bpm"]
        steps = windows.channels["steps"]
        columns += [
            heart[:, -12:].mean(axis=1),
            _slope(heart, 12),
            steps[:, -12:].sum(axis=1),
            steps[:, -36:].sum(axis=1),
        ]
    hours = (
        windows.origin_time.astype("datetime64[h]").astype(np.int64) % 24
    ).astype(np.float64)
    columns += [np.sin(2 * np.pi * hours / 24), np.cos(2 * np.pi * hours / 24)]
    return np.stack(columns, axis=1)


def standardize(train: np.ndarray, test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = train.mean(axis=0)
    scale = train.std(axis=0)
    scale[scale == 0] = 1.0
    return (train - mean) / scale, (test - mean) / scale


@dataclass
class Ridge:
    alpha: float = 1.0

    def fit(self, features: np.ndarray, target: np.ndarray) -> "Ridge":
        design = np.hstack([np.ones((features.shape[0], 1)), features])
        penalty = self.alpha * np.eye(design.shape[1])
        penalty[0, 0] = 0.0
        self.coef_ = np.linalg.solve(design.T @ design + penalty, design.T @ target)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        design = np.hstack([np.ones((features.shape[0], 1)), features])
        return design @ self.coef_


@dataclass
class Mlp:
    hidden: int = 24
    epochs: int = 260
    learning_rate: float = 0.01
    seed: int = 0

    def fit(self, features: np.ndarray, target: np.ndarray) -> "Mlp":
        generator = np.random.default_rng(self.seed)
        mean, scale = target.mean(), target.std() or 1.0
        y = ((target - mean) / scale)[:, None]
        self.w1 = generator.normal(0, 1 / np.sqrt(features.shape[1]), (features.shape[1], self.hidden))
        self.b1 = np.zeros((1, self.hidden))
        self.w2 = generator.normal(0, 1 / np.sqrt(self.hidden), (self.hidden, 1))
        self.b2 = np.zeros((1, 1))
        params = [self.w1, self.b1, self.w2, self.b2]
        moments = [np.zeros_like(part) for part in params]
        speeds = [np.zeros_like(part) for part in params]
        for step in range(1, self.epochs + 1):
            hidden = np.tanh(features @ self.w1 + self.b1)
            output = hidden @ self.w2 + self.b2
            residual = output - y
            grad_out = residual / features.shape[0]
            grad_w2 = hidden.T @ grad_out
            grad_b2 = grad_out.sum(axis=0, keepdims=True)
            grad_hidden = (grad_out @ self.w2.T) * (1 - hidden**2)
            grad_w1 = features.T @ grad_hidden
            grad_b1 = grad_hidden.sum(axis=0, keepdims=True)
            for index, grad in enumerate((grad_w1, grad_b1, grad_w2, grad_b2)):
                moments[index] = 0.9 * moments[index] + 0.1 * grad
                speeds[index] = 0.999 * speeds[index] + 0.001 * grad**2
                fixed = moments[index] / (1 - 0.9**step)
                square = speeds[index] / (1 - 0.999**step)
                params[index] -= self.learning_rate * fixed / (np.sqrt(square) + 1e-8)
            self.w1, self.b1, self.w2, self.b2 = params
        self.mean, self.scale = mean, scale
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        hidden = np.tanh(features @ self.w1 + self.b1)
        return (hidden @ self.w2 + self.b2)[:, 0] * self.scale + self.mean



VARIANTS = {
    "persistence": (False, False, False, False),
    "glucose": (False, False, False, False),
    "glucose+insulin+carbs": (True, True, False, False),
    "glucose+onboard": (False, False, False, True),
    "glucose+onboard+wearable": (False, False, True, True),
}

assert "iob_units" in CHANNEL_NAMES and "carbs_absorbed" in CHANNEL_NAMES
