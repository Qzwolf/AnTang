"""命令行入口：加载数据 → 按受试者划分 → 跑基线 → 打印指标表并写 JSON。

    py -3 -m glucose_forecast.evaluate --dataset t1d-uom --root <path>

评估口径（与领域惯例一致，写进论文时要照抄这一段）：
- 输入 6 小时 / 72 点，输出 1 小时 / 12 点，5 分钟采样
- 窗口内任何缺失整条丢弃，不做插值
- 按受试者划分，测试集受试者完全不参与训练
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from glucose_forecast import baselines as baseline_module
from glucose_forecast.metrics import summarize
from glucose_forecast.paths import dataset_dir
from glucose_forecast.series import RegularGrid
from glucose_forecast.splits import mask_subjects, subject_holdout
from glucose_forecast.windows import (
    DEFAULT_HISTORY_POINTS,
    DEFAULT_HORIZON_POINTS,
    WindowSet,
    build_windows,
    stack_windows,
)

T1D_UOM_ROOT = (
    dataset_dir("T1D-UOM") / "sharpic-ManchesterCSCoordinatedDiabetesStudy-ea52718"
)


def _window_sets(
    dataset: str,
    root: Path | None,
    *,
    history_points: int,
    horizon_points: int,
    max_windows_per_subject: int,
) -> list[WindowSet]:
    if dataset == "t1d-uom":
        from glucose_forecast.data import t1d_uom

        grids: list[RegularGrid] = t1d_uom.grids(root or T1D_UOM_ROOT)
        return [
            build_windows(
                grid, history_points=history_points, horizon_points=horizon_points
            )
            for grid in grids
        ]
    if dataset == "hupa-ucm":
        from glucose_forecast.data import hupa_ucm

        grids: list[RegularGrid] = hupa_ucm.grids(root or hupa_ucm.DEFAULT_ROOT)
        return [
            build_windows(
                grid, history_points=history_points, horizon_points=horizon_points
            )
            for grid in grids
        ]
    if dataset == "ohiot1dm":
        from glucose_forecast.data import ohiot1dm

        grids: list[RegularGrid] = ohiot1dm.grids(root or ohiot1dm.DEFAULT_ROOT)
        return [
            build_windows(
                grid, history_points=history_points, horizon_points=horizon_points
            )
            for grid in grids
        ]
    if dataset == "diadata":
        from glucose_forecast.data import diadata

        # DiaData 有 1720 人、两亿多个点：整份读进内存不现实，所以缓存的是窗口而不是序列。
        cache = diadata.load_or_build(
            root or diadata.DEFAULT_CSV,
            history_points=history_points,
            horizon_points=horizon_points,
            max_windows_per_subject=max_windows_per_subject,
        )
        return cache.as_window_sets()
    raise SystemExit(f"未知数据集：{dataset}（支持 t1d-uom / diadata / ohiot1dm / hupa-ucm）")


def run(
    dataset: str,
    root: Path | None = None,
    *,
    history_points: int = DEFAULT_HISTORY_POINTS,
    horizon_points: int = DEFAULT_HORIZON_POINTS,
    test_ratio: float = 0.3,
    seed: int = 0,
    max_windows_per_subject: int = 200,
) -> dict[str, object]:
    window_sets = _window_sets(
        dataset,
        root,
        history_points=history_points,
        horizon_points=horizon_points,
        max_windows_per_subject=max_windows_per_subject,
    )
    window_sets = [item for item in window_sets if len(item) > 0]
    if not window_sets:
        raise SystemExit("没有任何完整窗口：数据缺测太多，或历史+预测跨度超过了记录时长")

    history, future, subjects = stack_windows(window_sets)
    train_subjects, test_subjects = subject_holdout(
        subjects, test_ratio=test_ratio, seed=seed
    )
    train_mask = mask_subjects(subjects, train_subjects)
    test_mask = mask_subjects(subjects, test_subjects)

    report: dict[str, object] = {
        "dataset": dataset,
        "history_points": history_points,
        "horizon_points": horizon_points,
        "subjects_total": len({*subjects}),
        "subjects_train": train_subjects,
        "subjects_test": test_subjects,
        "windows_train": int(train_mask.sum()),
        "windows_test": int(test_mask.sum()),
        "baselines": [],
    }

    for model in baseline_module.default_baselines():
        model.fit(history[train_mask], future[train_mask])
        predicted = model.predict(history[test_mask])
        if not np.isfinite(predicted).all():
            raise RuntimeError(f"{model.name} 产生了非有限值，不应写进报告")
        summary = summarize(future[test_mask], predicted)
        report["baselines"].append({"name": model.name, **summary})

    return report


def _print_table(report: dict[str, object]) -> None:
    print(
        f"dataset={report['dataset']}  "
        f"history={report['history_points']}  horizon={report['horizon_points']}  "
        f"subjects={report['subjects_total']}  "
        f"windows train/test={report['windows_train']}/{report['windows_test']}"
    )
    header = (
        f"{'baseline':<14}{'RMSE':>8}{'MAE':>8}{'MARD%':>8}"
        f"{'A%':>7}{'B%':>7}{'C%':>7}{'D%':>7}{'E%':>7}"
        f"{'sens':>7}{'prec':>7}{'FA/day':>8}{'lead':>7}"
    )
    print(header)
    print("-" * len(header))
    for row in report["baselines"]:
        print(
            f"{row['name']:<14}{row['rmse_mgdl']:>8.2f}{row['mae_mgdl']:>8.2f}"
            f"{row['mard_percent']:>8.1f}{row['zone_a_percent']:>7.1f}"
            f"{row['zone_b_percent']:>7.1f}{row['zone_c_percent']:>7.1f}"
            f"{row['zone_d_percent']:>7.1f}{row['zone_e_percent']:>7.1f}"
            f"{row['sensitivity']:>7.2f}{row['precision']:>7.2f}"
            f"{row['false_alarms_per_day']:>8.2f}{row['lead_time_minutes']:>7.1f}"
        )
    print()
    print("读法：A+B 高不代表好（persistence 也能很高），要看 D/E 的绝对占比和 sens。")
    print("lead 为负数表示报警晚于真实低血糖出现。")


def main() -> None:
    parser = argparse.ArgumentParser(description="血糖预测基线评测")
    parser.add_argument("--dataset", default="t1d-uom")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="t1d-uom 传数据集根目录；diadata 传 CSV 路径。缺省各用内置默认值。",
    )
    parser.add_argument("--history-points", type=int, default=DEFAULT_HISTORY_POINTS)
    parser.add_argument("--horizon-points", type=int, default=DEFAULT_HORIZON_POINTS)
    parser.add_argument("--test-ratio", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--max-windows-per-subject",
        type=int,
        default=200,
        help="每个受试者最多保留多少条窗口；DiaData 两亿多个点不可能全用。",
    )
    parser.add_argument("--json", type=Path, default=None)
    arguments = parser.parse_args()

    report = run(
        arguments.dataset,
        arguments.root,
        history_points=arguments.history_points,
        horizon_points=arguments.horizon_points,
        test_ratio=arguments.test_ratio,
        seed=arguments.seed,
        max_windows_per_subject=arguments.max_windows_per_subject,
    )
    _print_table(report)
    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"report written: {arguments.json}")


if __name__ == "__main__":
    main()
