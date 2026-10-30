"""序列模型：直接在 72 点血糖曲线上做卷积，不手工构造特征。

手工特征那一轮（19 个数值特征 + 四模态求和）在 HUPA 上跑出了"多模态没有增益"，
在 Ohio 上跑出了增益。两种结果都可能来自特征工程，而不是来自模态本身。所以这里换一条
完全不同的路线：把历史曲线直接喂给一个小卷积网络，让模型自己学形状（下降斜率、
夜间平台、餐后尖峰），再看加不加胰岛素/碳水/心率通道有没有区别。

保持和前面完全相同的评测协议：按受试者划分、报警率对齐在训练集上选阈值、
报 AUROC / AUPRC / 灵敏度 / 精确率 / 每天报警次数。
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

from glucose_forecast.data.windows_common import MultimodalWindows
from glucose_forecast.hypo_task import (
    HYPO_MGDL,
    average_precision,
    labels,
    roc_auc,
    threshold_for_rate,
)

__all__ = [
    "CGN",
    "CHANNEL_SETS",
    "evaluate",
    "feature_matrix",
    "predict",
    "train_model",
]

CHANNEL_SETS: dict[str, tuple[str, ...]] = {
    "glucose": (),
    "glucose+insulin+carbs": ("bolus_units", "carbs_grams"),
    "glucose+onboard": ("bolus_units", "carbs_grams", "iob_units", "carbs_absorbed"),
    "glucose+onboard+wearable": (
        "bolus_units",
        "carbs_grams",
        "iob_units",
        "carbs_absorbed",
        "heart_rate_bpm",
        "steps",
    ),
}

# 血糖按 180 mg/dL、60 的尺度缩放；通道按各自的经验尺度缩放，避免大数值通道主导梯度。
_SCALE = {
    "bolus_units": 2.0,
    "carbs_grams": 30.0,
    "iob_units": 3.0,
    "carbs_absorbed": 15.0,
    "heart_rate_bpm": 30.0,
    "steps": 60.0,
}


def feature_matrix(
    windows: MultimodalWindows, channels: tuple[str, ...]
) -> np.ndarray:
    """把一条曲线 + 若干通道拼成 (n, 1 + len(channels), 72) 的张量。"""

    glucose = ((windows.history - 180.0) / 60.0).astype(np.float32)[:, None, :]
    if not channels:
        return glucose
    stacked = [glucose]
    for name in channels:
        values = windows.channels[name][:, -windows.history.shape[1] :]
        stacked.append((values / _SCALE[name]).astype(np.float32)[:, None, :])
    return np.concatenate(stacked, axis=1)


class CGN(nn.Module):
    """三层卷积 + 全局池化。参数量在几千量级，和 12 个人这个数据规模相称。"""

    def __init__(self, in_channels: int) -> None:
        super().__init__()
        self.body = nn.Sequential(
            nn.Conv1d(in_channels, 16, 5, padding=2),
            nn.ReLU(),
            nn.Conv1d(16, 24, 5, padding=2),
            nn.ReLU(),
            nn.Conv1d(24, 24, 3, padding=1),
            nn.ReLU(),
        )
        self.head = nn.Linear(24 * 2, 1)

    def forward(self, batch: torch.Tensor) -> torch.Tensor:
        features = self.body(batch)
        pooled = torch.cat([features.mean(dim=2), features.amax(dim=2)], dim=1)
        return self.head(pooled).squeeze(1)


def train_model(
    features: np.ndarray,
    truth: np.ndarray,
    *,
    seed: int,
    epochs: int = 30,
    batch_size: int = 128,
    learning_rate: float = 3e-3,
) -> CGN:
    torch.manual_seed(seed)
    model = CGN(features.shape[1])
    optimiser = torch.optim.Adam(model.parameters(), lr=learning_rate)
    positive = max(int(truth.sum()), 1)
    weight = torch.tensor((len(truth) - positive) / positive, dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=weight)
    data = torch.from_numpy(features)
    target = torch.from_numpy(truth.astype(np.float32))
    generator = torch.Generator().manual_seed(seed)
    for _ in range(epochs):
        order = torch.randperm(len(data), generator=generator)
        for start in range(0, len(order), batch_size):
            index = order[start : start + batch_size]
            optimiser.zero_grad()
            loss = loss_fn(model(data[index]), target[index])
            loss.backward()
            optimiser.step()
    model.eval()
    return model


def predict(model: CGN, features: np.ndarray) -> np.ndarray:
    with torch.no_grad():
        logits = model(torch.from_numpy(features))
    return logits.numpy().astype(np.float64)


def evaluate(
    windows: MultimodalWindows,
    *,
    horizon_points: int = 6,
    alert_rate: float = 0.04,
    seed: int = 0,
    test_subjects: list[str],
    epochs: int = 30,
    min_current: float = 80.0,
) -> list[dict]:
    """按受试者划分训练与测试；阈值在训练集上按报警率取，再原样用到测试集。

    `min_current` 与手工特征那一轮保持一致：只评估"现在还不低、但接下来可能掉下去"的窗口。
    不筛会变成两个不同的任务（正例率 4.5% 对 1.4%），AUPRC 不可比。
    """

    keep = windows.history[:, -1] >= min_current
    windows = windows.subset(keep)
    truth = labels(windows, horizon_points=horizon_points)
    is_test = np.isin(windows.subject, test_subjects)
    if is_test.all() or not is_test.any():
        raise ValueError("按受试者划分后训练集或测试集为空")
    days = horizon_points * 5 / (60 * 24)
    rows = []
    for name, channels in CHANNEL_SETS.items():
        features = feature_matrix(windows, channels)
        model = train_model(
            features[~is_test], truth[~is_test], seed=seed, epochs=epochs
        )
        train_score = predict(model, features[~is_test])
        score = predict(model, features[is_test])
        threshold = threshold_for_rate(train_score, alert_rate)
        alert = score >= threshold
        hit = alert & truth[is_test]
        rows.append(
            {
                "model": "seq/" + name,
                "auroc": roc_auc(truth[is_test], score),
                "auprc": average_precision(truth[is_test], score),
                "sensitivity": float(hit.sum() / max(truth[is_test].sum(), 1)),
                "precision": float(hit.sum() / max(alert.sum(), 1)),
                "alerts_per_day": float(alert.sum() / (len(alert) * days)),
                "params": sum(p.numel() for p in model.parameters()),
                "positive_rate": float(truth[is_test].mean()),
                "hypo_mgdl": HYPO_MGDL,
            }
        )
    return rows
