"""把 Ohio 的手工特征结果与卷积序列模型放在一张表里比。

同时汇总三个种子，报均值±标准差，避免拿单次结果当结论。
"""

from __future__ import annotations

import json

import numpy as np

from glucose_forecast import seq_model
from glucose_forecast.data import hupa_windows, ohio_windows
from glucose_forecast.paths import data_root
from glucose_forecast.splits import subject_holdout

DATA = data_root()
HORIZON = 6  # 30 分钟
ALERT_RATE = 0.04


def summarise_handmade(seeds: tuple[int, ...]) -> None:
    rows: dict[str, dict[str, list[float]]] = {}
    for seed in seeds:
        path = DATA / f"hypo-ohio-seed{seed}.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        block = report[str(HORIZON)]
        for name, row in block["results"].items():
            slot = rows.setdefault(name, {"auroc": [], "auprc": []})
            slot["auroc"].append(row["auroc"])
            slot["auprc"].append(row["auprc"])
    print("== 手工特征（Ohio，30 分钟，三个种子）==")
    print(f"{'model':38} {'AUROC':>16} {'AUPRC':>16}")
    for name, slot in rows.items():
        auroc = np.array(slot["auroc"])
        auprc = np.array(slot["auprc"])
        print(
            f"{name:38} {auroc.mean():.3f}±{auroc.std():.3f}   "
            f"{auprc.mean():.3f}±{auprc.std():.3f}"
        )


def run_sequence(loader, label: str, seeds: tuple[int, ...]) -> None:
    windows = loader.build(limit_per_subject=400)
    subjects = sorted(set(windows.subject.tolist()))
    _, test_subjects = subject_holdout(subjects, seed=0)
    collected: dict[str, dict[str, list[float]]] = {}
    for seed in seeds:
        rows = seq_model.evaluate(
            windows,
            horizon_points=HORIZON,
            alert_rate=ALERT_RATE,
            seed=seed,
            test_subjects=test_subjects,
        )
        for row in rows:
            slot = collected.setdefault(
                row["model"], {"auroc": [], "auprc": [], "sens": [], "prec": [], "params": []}
            )
            slot["auroc"].append(row["auroc"])
            slot["auprc"].append(row["auprc"])
            slot["sens"].append(row["sensitivity"])
            slot["prec"].append(row["precision"])
            slot["params"].append(row["params"])
    print(f"\n== 序列模型（{label}，30 分钟，{len(seeds)} 个种子）==")
    print(f"{'model':38} {'AUROC':>16} {'AUPRC':>16} {'sens':>14} {'prec':>14} {'参数':>7}")
    for name, slot in collected.items():
        auroc = np.array(slot["auroc"])
        auprc = np.array(slot["auprc"])
        sens = np.array(slot["sens"])
        prec = np.array(slot["prec"])
        print(
            f"{name:38} {auroc.mean():.3f}±{auroc.std():.3f}   "
            f"{auprc.mean():.3f}±{auprc.std():.3f}   "
            f"{sens.mean():.3f}±{sens.std():.3f}   "
            f"{prec.mean():.3f}±{prec.std():.3f}   {slot['params'][0]:>7}"
        )


def main() -> int:
    summarise_handmade((0, 1, 2))
    run_sequence(ohio_windows, "OhioT1DM", (0, 1, 2))
    run_sequence(hupa_windows, "HUPA-UCM", (0, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
