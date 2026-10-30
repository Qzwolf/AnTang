"""指标：回归误差 + Clarke 误差网格 + 按事件的低血糖检出。

为什么要三套：RMSE 只回答「平均差多少」，Clarke 分区回答「这个偏差临床上会不会
导致错误处置」，事件指标回答「真正需要报警的那几次，报出来了吗、早了多久」。
只报第一套的论文在这个领域非常多，也正是它们看起来都很好的原因。
"""

from __future__ import annotations

import numpy as np

HYPO_MGDL = 70.0
HYPER_MGDL = 180.0
ZONES = ("A", "B", "C", "D", "E")


def rmse(reference: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean((reference - predicted) ** 2)))


def mae(reference: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(reference - predicted)))


def mard(reference: np.ndarray, predicted: np.ndarray) -> float:
    """平均绝对相对偏差（%）。参考值过小时不稳定，故只用 >= 70 mg/dL 的点。"""
    mask = reference >= HYPO_MGDL
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs(predicted[mask] - reference[mask]) / reference[mask]) * 100.0)


def clarke_zone(reference: float, predicted: float) -> str:
    """Clarke 误差网格（1987）的六格简化规则。

    - A：偏差在 20% 内；或两者都在低血糖区（< 70）
    - B：偏差超过 20%，但不会导致有害处置
    - C：不必要的处置（真实在范围内却预测低血糖 → 多吃；真实在范围内却预测高血糖 → 多打）
    - D：危险的漏检（真实低血糖却预测正常；真实高血糖却预测正常）
    - E：相反的处置（真实低血糖预测高血糖 → 打胰岛素）

    这是文献中最常见的简化版；发表前应与参考实现（如 PyPI `clarke-error-grid`）对拍，
    并在方法里写明判据。
    """
    if reference < HYPO_MGDL:
        if predicted < HYPO_MGDL:
            return "A"
        if predicted <= HYPER_MGDL:
            return "D"
        return "E"
    if reference <= HYPER_MGDL:
        if predicted < HYPO_MGDL:
            return "C"
        if predicted <= HYPER_MGDL:
            return "A" if abs(predicted - reference) <= 0.2 * reference else "B"
        return "C"
    if predicted < HYPO_MGDL:
        return "E"
    if predicted <= HYPER_MGDL:
        return "D"
    return "A" if abs(predicted - reference) <= 0.2 * reference else "B"


def clarke_zones(reference: np.ndarray, predicted: np.ndarray) -> np.ndarray:
    pairs = zip(reference.ravel().tolist(), predicted.ravel().tolist(), strict=True)
    return np.array([clarke_zone(ref, pred) for ref, pred in pairs], dtype="<U1")


def zone_distribution(zones: np.ndarray) -> dict[str, float]:
    total = zones.size
    if total == 0:
        return {zone: float("nan") for zone in ZONES}
    return {zone: float((zones == zone).sum() / total * 100.0) for zone in ZONES}


def hypo_detection(
    reference: np.ndarray,
    predicted: np.ndarray,
    *,
    threshold: float = HYPO_MGDL,
    sample_minutes: int = 5,
) -> dict[str, float]:
    """把「未来 1 小时里会不会低血糖」当成一次二分类，逐窗口统计。

    reference / predicted 形状都是 (n, horizon)。
    """
    if reference.shape != predicted.shape:
        raise ValueError("reference 与 predicted 形状必须一致")
    true_event = (reference < threshold).any(axis=1)
    pred_event = (predicted < threshold).any(axis=1)

    tp = int((true_event & pred_event).sum())
    fn = int((true_event & ~pred_event).sum())
    fp = int((~true_event & pred_event).sum())
    tn = int((~true_event & ~pred_event).sum())

    observed_days = reference.shape[0] * predicted.shape[1] * sample_minutes / (60 * 24)
    return {
        "true_events": float(tp + fn),
        "sensitivity": float(tp / (tp + fn)) if tp + fn else float("nan"),
        "precision": float(tp / (tp + fp)) if tp + fp else float("nan"),
        "false_alarms": float(fp),
        "false_alarms_per_day": float(fp / observed_days) if observed_days else float("nan"),
        "specificity": float(tn / (tn + fp)) if tn + fp else float("nan"),
        "lead_time_minutes": _mean_lead_time(reference, predicted, threshold, sample_minutes),
    }


def _mean_lead_time(
    reference: np.ndarray, predicted: np.ndarray, threshold: float, sample_minutes: int
) -> float:
    """预测到低血糖比真实低血糖提前了多少分钟（只在两者都报了的窗口上算）。"""
    leads: list[float] = []
    for ref_row, pred_row in zip(reference, predicted, strict=True):
        ref_hits = np.flatnonzero(ref_row < threshold)
        pred_hits = np.flatnonzero(pred_row < threshold)
        if ref_hits.size == 0 or pred_hits.size == 0:
            continue
        leads.append(float((ref_hits[0] - pred_hits[0]) * sample_minutes))
    return float(np.mean(leads)) if leads else float("nan")


def summarize(
    reference: np.ndarray, predicted: np.ndarray, *, sample_minutes: int = 5
) -> dict[str, float]:
    zones = clarke_zones(reference, predicted)
    distribution = zone_distribution(zones)
    summary = {
        "rmse_mgdl": rmse(reference, predicted),
        "mae_mgdl": mae(reference, predicted),
        "mard_percent": mard(reference, predicted),
        "zone_a_percent": distribution["A"],
        "zone_b_percent": distribution["B"],
        "zone_c_percent": distribution["C"],
        "zone_d_percent": distribution["D"],
        "zone_e_percent": distribution["E"],
    }
    summary.update(hypo_detection(reference, predicted, sample_minutes=sample_minutes))
    return summary
