"""低血糖事件预测：问"未来半小时会不会跌破 70"，而不是"值是多少"。

换任务的理由：数值预测里，吃饭打针的影响已经体现在血糖曲线上；但**风险**不同——同样的
当前值，正在往下掉且有 3 单位活性胰岛素，和正往上走且没有，是完全两件事。这个问题上
胰岛素与碳水**可能**真的有信息，这也是本轮要检验的假设。
"""

from __future__ import annotations

import numpy as np

from glucose_forecast.data.hupa_windows import MultimodalWindows

HYPO_MGDL = 70.0


def labels(windows: MultimodalWindows, *, horizon_points: int = 6) -> np.ndarray:
    """未来 horizon_points 个采样点里是否出现过 < 70。"""

    future = windows.future[:, :horizon_points]
    return (future < HYPO_MGDL).any(axis=1)


def roc_auc(truth: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(score, kind="stable")
    ranks = np.empty(score.size, dtype=np.float64)
    ranks[order] = np.arange(1, score.size + 1, dtype=np.float64)
    positive = truth.sum()
    negative = truth.size - positive
    if positive == 0 or negative == 0:
        return float("nan")
    return float((ranks[truth].sum() - positive * (positive + 1) / 2) / (positive * negative))


def average_precision(truth: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(-score, kind="stable")
    ranked = truth[order]
    cumulative = np.cumsum(ranked)
    precision = cumulative / np.arange(1, ranked.size + 1)
    return float((precision * ranked).sum() / max(ranked.sum(), 1))


def threshold_for_rate(score: np.ndarray, rate: float) -> float:
    """在训练集上选阈值，让报警比例接近给定值。"""

    return float(np.quantile(score, 1.0 - rate))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -30, 30)))


def _bce(probability: np.ndarray, truth: np.ndarray) -> float:
    clipped = np.clip(probability, 1e-9, 1 - 1e-9)
    return float(-(truth * np.log(clipped) + (1 - truth) * np.log(1 - clipped)).mean())


class Logistic:
    """L2 正则的逻辑回归，梯度下降实现，避免引入 sklearn。"""

    def __init__(self, *, epochs: int = 800, learning_rate: float = 0.5, l2: float = 1e-3):
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.l2 = l2

    def fit(self, features: np.ndarray, truth: np.ndarray) -> "Logistic":
        self.weights = np.zeros(features.shape[1])
        self.bias = 0.0
        for _ in range(self.epochs):
            probability = _sigmoid(features @ self.weights + self.bias)
            error = probability - truth
            self.weights -= self.learning_rate * (
                features.T @ error / features.shape[0] + self.l2 * self.weights
            )
            self.bias -= self.learning_rate * error.mean()
        return self

    def score(self, features: np.ndarray) -> np.ndarray:
        return features @ self.weights + self.bias


class MlpBinary:
    def __init__(self, *, hidden: int = 24, epochs: int = 400, learning_rate: float = 0.01, seed: int = 0):
        self.hidden = hidden
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.seed = seed

    def fit(self, features: np.ndarray, truth: np.ndarray) -> "MlpBinary":
        generator = np.random.default_rng(self.seed)
        self.w1 = generator.normal(0, 1 / np.sqrt(features.shape[1]), (features.shape[1], self.hidden))
        self.b1 = np.zeros((1, self.hidden))
        self.w2 = generator.normal(0, 1 / np.sqrt(self.hidden), (self.hidden, 1))
        self.b2 = np.zeros((1, 1))
        params = [self.w1, self.b1, self.w2, self.b2]
        moments = [np.zeros_like(part) for part in params]
        speeds = [np.zeros_like(part) for part in params]
        target = truth[:, None]
        for step in range(1, self.epochs + 1):
            hidden = np.tanh(features @ self.w1 + self.b1)
            probability = _sigmoid(hidden @ self.w2 + self.b2)
            grad_out = (probability - target) / features.shape[0]
            grad_w2 = hidden.T @ grad_out
            grad_b2 = grad_out.sum(axis=0, keepdims=True)
            grad_hidden = (grad_out @ self.w2.T) * (1 - hidden**2)
            grad_w1 = features.T @ grad_hidden
            grad_b1 = grad_hidden.sum(axis=0, keepdims=True)
            for index, grad in enumerate((grad_w1, grad_b1, grad_w2, grad_b2)):
                moments[index] = 0.9 * moments[index] + 0.1 * grad
                speeds[index] = 0.999 * speeds[index] + 0.001 * grad**2
                params[index] -= self.learning_rate * (
                    moments[index] / (1 - 0.9**step) / (np.sqrt(speeds[index] / (1 - 0.999**step)) + 1e-8)
                )
            self.w1, self.b1, self.w2, self.b2 = params
        return self

    def score(self, features: np.ndarray) -> np.ndarray:
        hidden = np.tanh(features @ self.w1 + self.b1)
        return (hidden @ self.w2 + self.b2)[:, 0]
