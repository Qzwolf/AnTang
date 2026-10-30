"""只在"最近一小时真的打过胰岛素/吃过碳水"的窗口上比一次。

全体窗口里只有约 15% 有胰岛素事件，把它们的效应摊到全部窗口上会被稀释；
真正要问的是：**在事件发生的那些窗口上，加这个模态有没有用。**
"""

import numpy as np

from glucose_forecast.data import hupa_windows
from glucose_forecast.metrics import rmse
from glucose_forecast.multimodal import Ridge, build_features, standardize
from glucose_forecast.splits import subject_holdout

HORIZON = 12
windows = hupa_windows.build(horizon_points=HORIZON, limit_per_subject=400)
target = windows.future[:, HORIZON - 1]
train_subjects, test_subjects = subject_holdout(sorted(set(windows.subject.tolist())), seed=0)
train = np.isin(windows.subject, train_subjects)
test = np.isin(windows.subject, test_subjects)

bolus_recent = windows.channels["bolus_units"][:, -12:].sum(axis=1)
carbs_recent = windows.channels["carbs_grams"][:, -12:].sum(axis=1)


def evaluate(name, insulin, carbs, wearable, onboard, mask):
    features = build_features(
        windows,
        use_insulin=insulin,
        use_carbs=carbs,
        use_wearable=wearable,
        use_onboard=onboard,
    )
    scaled_train, scaled_test = standardize(features[train], features[test])
    model = Ridge().fit(scaled_train, target[train])
    predicted = model.predict(scaled_test)
    subset = mask[test]
    return rmse(target[test][subset], predicted[subset]), int(subset.sum())


for label, mask in (
    ("最近一小时有胰岛素", bolus_recent > 0),
    ("最近一小时有碳水", carbs_recent > 0),
    ("最近一小时既无胰岛素也无碳水", (bolus_recent == 0) & (carbs_recent == 0)),
):
    print(f"\n== {label} ==")
    for name, flags in (
        ("glucose", (False, False, False, False)),
        ("glucose+insulin+carbs", (True, True, False, False)),
        ("glucose+onboard", (False, False, False, True)),
    ):
        error, count = evaluate(name, *flags, mask)
        print(f"  {name:22s} RMSE {error:6.2f}  窗口 {count}")
