"""事件级评估：把重叠窗口还原成"一次低血糖"，并按每天允许几次误报来定阈值。

窗口级"4% 报警率"不是产品能理解的预算：一小时 16 个窗口，4% 就是一天十几次报警。
这里改成按**每天最多几次误报**在训练集上选阈值，再看测试集上能抓到多少事件。
"""

from __future__ import annotations

import numpy as np
from sklearn.neural_network import MLPClassifier

from glucose_forecast import multimodal, seq_model
from glucose_forecast.data import hupa_windows, ohio_windows
from glucose_forecast.data.windows_common import MultimodalWindows
from glucose_forecast.evaluate_hypo import simple_rule
from glucose_forecast.event_eval import summarise
from glucose_forecast.hypo_task import HYPO_MGDL, labels
from glucose_forecast.splits import mask_subjects, subject_holdout

HORIZON = 6
MIN_CURRENT = 80.0
TARGETS = (1.0, 3.0)


def _subset(windows: MultimodalWindows) -> MultimodalWindows:
    return windows.subset(windows.history[:, -1] >= MIN_CURRENT)


def _threshold_for_false_rate(
    windows: MultimodalWindows, score: np.ndarray, target: float
) -> float:
    """二分找阈值，使训练集上的误报次数不超过 target 次/天。"""

    low, high = float(score.min()), float(score.max())
    for _ in range(24):
        middle = (low + high) / 2
        rate = summarise(windows, score, middle, horizon_points=HORIZON).false_alerts_per_day
        if rate > target:
            low = middle
        else:
            high = middle
    return high


def _scores(
    family: str,
    windows: MultimodalWindows,
    truth: np.ndarray,
    train_rows: np.ndarray,
    test_rows: np.ndarray,
    seed: int,
) -> np.ndarray:
    score = np.zeros(len(truth))
    if family == "rule":
        return simple_rule(windows)
    if family == "handmade":
        features = multimodal.build_features(windows)
        train_x, test_x = multimodal.standardize(
            features[train_rows], features[test_rows]
        )
        model = MLPClassifier(hidden_layer_sizes=(32,), max_iter=400, random_state=seed)
        model.fit(train_x, truth[train_rows])
        score[train_rows] = model.predict_proba(train_x)[:, 1]
        score[test_rows] = model.predict_proba(test_x)[:, 1]
        return score
    features = seq_model.feature_matrix(windows, ())
    model = seq_model.train_model(features[train_rows], truth[train_rows], seed=seed)
    score[train_rows] = seq_model.predict(model, features[train_rows])
    score[test_rows] = seq_model.predict(model, features[test_rows])
    return score


def evaluate_family(
    label: str, loader, seeds: tuple[int, ...]
) -> list[tuple[str, float, float, float, float]]:
    windows = _subset(loader.build(limit_per_subject=400))
    truth = labels(windows, horizon_points=HORIZON)
    subjects = sorted(set(windows.subject.tolist()))
    _, test_subjects = subject_holdout(subjects, seed=0)
    is_test = mask_subjects(windows.subject, test_subjects)
    train_rows = np.flatnonzero(~is_test)
    test_rows = np.flatnonzero(is_test)
    train_windows = windows.subset(~is_test)
    test_windows = windows.subset(is_test)

    rows: list[tuple[str, float, float, float, float]] = []
    for family in ("rule", "handmade", "sequence"):
        for target in TARGETS:
            sensitivities: list[float] = []
            observed: list[float] = []
            leads: list[float] = []
            events: list[int] = []
            for seed in seeds:
                score = _scores(family, windows, truth, train_rows, test_rows, seed)
                threshold = _threshold_for_false_rate(
                    train_windows, score[train_rows], target
                )
                report = summarise(
                    test_windows, score[test_rows], threshold, horizon_points=HORIZON
                )
                sensitivities.append(report.sensitivity)
                observed.append(report.false_alerts_per_day)
                leads.append(report.median_lead_minutes)
                events.append(report.events)
            rows.append(
                (
                    f"{label}/{family}",
                    target,
                    float(np.mean(sensitivities)),
                    float(np.mean(observed)),
                    float(np.mean(leads)),
                )
            )
    return rows


def main() -> int:
    rows = evaluate_family("Ohio", ohio_windows, (0, 1, 2))
    rows += evaluate_family("HUPA", hupa_windows, (0, 1))
    print(f"任务：未来 30 分钟 <{HYPO_MGDL:.0f} mg/dL，只看当前 ≥{MIN_CURRENT:.0f} 的窗口")
    print(f"{'model':22} {'目标误报/天':>10} {'事件检出':>8} {'实际误报/天':>11} {'提前(分钟)':>10}")
    for name, target, sensitivity, observed, lead in rows:
        print(
            f"{name:22} {target:>10.1f} {sensitivity:>8.3f} "
            f"{observed:>11.2f} {lead:>10.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
