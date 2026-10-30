"""换三个随机种子重跑低血糖任务，用来判断"差异"是不是噪声。

一次划分的结论不能当结论——上一轮所有"提升"都在 0.01 量级，正好是种子波动的量级。
"""

import numpy as np

from glucose_forecast import evaluate_hypo

VARIANTS = ("glucose", "glucose+insulin+carbs", "glucose+onboard", "glucose+onboard+wearable")
MODELS = ("logistic", "mlp")
SEEDS = (0, 1, 2)

collected: dict[str, dict[str, list[float]]] = {}
for seed in SEEDS:
    report = evaluate_hypo.run(
        horizon_points=6, limit_per_subject=400, seed=seed, alert_rate=0.02, min_current=80.0
    )
    for key, scores in report["results"].items():
        variant, model = key.split("/")
        if variant not in VARIANTS or model not in MODELS:
            continue
        bucket = collected.setdefault(key, {"auroc": [], "auprc": []})
        bucket["auroc"].append(scores["auroc"])
        bucket["auprc"].append(scores["auprc"])

print(f"{'模型':34s} {'AUROC 均值±标准差':>26s} {'AUPRC 均值±标准差':>26s}")
for key in sorted(collected):
    bucket = collected[key]
    auroc = np.array(bucket["auroc"])
    auprc = np.array(bucket["auprc"])
    print(
        f"{key:34s} {auroc.mean():.3f} ± {auroc.std():.3f}"
        f"            {auprc.mean():.3f} ± {auprc.std():.3f}"
    )
