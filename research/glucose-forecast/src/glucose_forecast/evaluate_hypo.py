"""低血糖事件预测的消融：报警预算固定，比较"加了模态能不能抓得更多"。

评价方式说明：
- 阈值一律在**训练集**上按目标报警率选出，再到测试集上评估，避免用测试集调参。
- 报警率按窗口换算成"每天报警次数"：stride=3 时每 15 分钟一个窗口，一天 96 个窗口。
- AUROC 只看排序，AUPRC 对类别不平衡更敏感，所以两个都报；正例基准率也报出来。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from glucose_forecast.data import hupa_windows, ohio_windows
from glucose_forecast.hypo_task import (
    HYPO_MGDL,
    Logistic,
    MlpBinary,
    average_precision,
    labels,
    roc_auc,
    threshold_for_rate,
)
from glucose_forecast.multimodal import VARIANTS, build_features, standardize
from glucose_forecast.splits import subject_holdout

WINDOWS_PER_DAY = 96  # stride=3，5 分钟一格


def simple_rule(windows: hupa_windows.MultimodalWindows) -> np.ndarray:
    """产品里最常见的规则：现在偏低且正在往下掉。"""

    history = windows.history
    slope = (history[:, -1] - history[:, -7]) / 30.0
    return -(history[:, -1] + 40.0 * slope)  # 越大越危险


def run(
    *,
    horizon_points: int,
    limit_per_subject: int,
    seed: int,
    alert_rate: float,
    min_current: float,
    dataset: str = "hupa",
) -> dict:
    loader = ohio_windows if dataset == "ohio" else hupa_windows
    windows = loader.build(
        horizon_points=horizon_points, limit_per_subject=limit_per_subject
    )
    truth = labels(windows, horizon_points=horizon_points)
    train_subjects, test_subjects = subject_holdout(sorted(set(windows.subject.tolist())), seed=seed)
    # 只在"现在还没低"的窗口上评估：否则任务退化成"识别此刻已经低血糖"。
    onset = windows.history[:, -1] >= min_current
    train = np.isin(windows.subject, train_subjects) & onset
    test = np.isin(windows.subject, test_subjects) & onset

    results: dict[str, dict] = {}
    for name, flags in VARIANTS.items():
        insulin, carbs, wearable, onboard = flags
        features = build_features(
            windows,
            use_insulin=insulin,
            use_carbs=carbs,
            use_wearable=wearable,
            use_onboard=onboard,
        )
        scaled_train, scaled_test = standardize(features[train], features[test])
        if name == "persistence":
            for kind in ("rule",):
                score = simple_rule(windows)
                threshold = threshold_for_rate(score[train], alert_rate)
                results[f"{name}/{kind}"] = _scores(
                    truth[test], score[test], threshold, alert_rate
                )
            continue
        for kind in ("logistic", "mlp"):
            model = Logistic() if kind == "logistic" else MlpBinary(seed=seed)
            model.fit(scaled_train, truth[train])
            score = model.score(scaled_test)
            threshold = threshold_for_rate(model.score(scaled_train), alert_rate)
            results[f"{name}/{kind}"] = _scores(truth[test], score, threshold, alert_rate)
    return {
        "dataset": dataset,
        "horizon_points": horizon_points,
        "horizon_minutes": horizon_points * 5,
        "positive_rate": float(truth[test].mean()),
        "min_current": min_current,
        "train_windows": int(train.sum()),
        "test_windows": int(test.sum()),
        "results": results,
    }


def _scores(truth: np.ndarray, score: np.ndarray, threshold: float, alert_rate: float) -> dict:
    alert = score >= threshold
    hit = alert & truth
    return {
        "auroc": roc_auc(truth, score),
        "auprc": average_precision(truth, score),
        "sensitivity": float(hit.sum() / max(truth.sum(), 1)),
        "precision": float(hit.sum() / max(alert.sum(), 1)),
        "alerts_per_day": float(alert.mean() * WINDOWS_PER_DAY),
        "alert_rate": alert_rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="低血糖事件预测消融")
    parser.add_argument("--horizon-points", type=int, nargs="+", default=[6, 12])
    parser.add_argument("--limit-per-subject", type=int, default=400)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--alert-rate", type=float, default=0.04)
    parser.add_argument("--min-current", type=float, default=80.0)
    parser.add_argument("--dataset", choices=("hupa", "ohio"), default="hupa")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    payload = {}
    for horizon in args.horizon_points:
        report = run(
            horizon_points=horizon,
            limit_per_subject=args.limit_per_subject,
            seed=args.seed,
            alert_rate=args.alert_rate,
            min_current=args.min_current,
            dataset=args.dataset,
        )
        payload[str(horizon)] = report
        print(
            f"\n[{report['dataset']}] 任务：未来 {report['horizon_minutes']} 分钟内 <{HYPO_MGDL:.0f} mg/dL"
            f" （正例占 {report['positive_rate']:.1%}，报警预算 {args.alert_rate * 100:.0f}%"
            f" ≈ {args.alert_rate * WINDOWS_PER_DAY:.1f} 次/天）=="
        )
        print(f"{'模型':34s} {'AUROC':>6s} {'AUPRC':>6s} {'sens':>6s} {'prec':>6s} {'报警/天':>7s}")
        for key, scores in report["results"].items():
            print(
                f"{key:34s} {scores['auroc']:6.3f} {scores['auprc']:6.3f}"
                f" {scores['sensitivity']:6.3f} {scores['precision']:6.3f}"
                f" {scores['alerts_per_day']:7.2f}"
            )
    if args.json is not None:
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nreport written:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
