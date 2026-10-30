"""在 HUPA-UCM 上做模态消融，输出一张可以直接贴进报告的对比表。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from glucose_forecast.data import hupa_windows
from glucose_forecast.metrics import summarize
from glucose_forecast.multimodal import VARIANTS, Mlp, Ridge, build_features, standardize
from glucose_forecast.splits import subject_holdout


def run(*, horizon_points: int, limit_per_subject: int, seed: int) -> dict[str, dict]:
    windows = hupa_windows.build(horizon_points=horizon_points, limit_per_subject=limit_per_subject)
    target = windows.future[:, horizon_points - 1]
    train_subjects, test_subjects = subject_holdout(sorted(set(windows.subject.tolist())), seed=seed)
    train = np.isin(windows.subject, train_subjects)
    test = np.isin(windows.subject, test_subjects)
    results: dict[str, dict] = {}
    for name, (insulin, carbs, wearable, onboard) in VARIANTS.items():
        features = build_features(
            windows,
            use_insulin=insulin,
            use_carbs=carbs,
            use_wearable=wearable,
            use_onboard=onboard,
        )
        if name == "persistence":
            predicted = windows.history[:, -1]
            reference = target[test]
            scores = summarize(reference[:, None], predicted[test][:, None])
            scores["model"] = "persistence"
            results[name] = scores
            continue
        scaled_train, scaled_test = standardize(features[train], features[test])
        for kind in ("ridge", "mlp"):
            model = Ridge() if kind == "ridge" else Mlp(seed=seed)
            model.fit(scaled_train, target[train])
            predicted = model.predict(scaled_test)
            scores = summarize(target[test][:, None], predicted[:, None])
            scores["model"] = kind
            results[f"{name}/{kind}"] = scores
    return {
        "horizon_points": horizon_points,
        "train_windows": int(train.sum()),
        "test_windows": int(test.sum()),
        "train_subjects": len(train_subjects),
        "test_subjects": len(test_subjects),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="HUPA-UCM 模态消融")
    parser.add_argument("--horizon-points", type=int, nargs="+", default=[6, 12])
    parser.add_argument("--limit-per-subject", type=int, default=400)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    payload = {}
    for horizon in args.horizon_points:
        report = run(horizon_points=horizon, limit_per_subject=args.limit_per_subject, seed=args.seed)
        payload[str(horizon)] = report
        print(
            f"\n== 预测 {horizon * 5} 分钟后 =="
            f"  窗口 训练/测试 = {report['train_windows']}/{report['test_windows']}"
            f"  受试者 {report['train_subjects']}/{report['test_subjects']}"
        )
        print(f"{'模型':38s} {'RMSE':>6s} {'MAE':>6s} {'MARD%':>6s} {'A%':>5s} {'B%':>5s} {'D%':>5s} {'E%':>5s}")
        for key, scores in report["results"].items():
            print(
                f"{key:38s} {scores['rmse_mgdl']:6.2f} {scores['mae_mgdl']:6.2f}"
                f" {scores['mard_percent']:6.1f} {scores['zone_a_percent']:5.1f}"
                f" {scores['zone_b_percent']:5.1f} {scores['zone_d_percent']:5.1f}"
                f" {scores['zone_e_percent']:5.1f}"
            )
    if args.json is not None:
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nreport written:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
