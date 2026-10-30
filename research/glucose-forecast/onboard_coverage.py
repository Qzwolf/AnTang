"""确认 iob / carbs_absorbed 不是全零——否则"没提升"可能只是特征没接上。"""


from glucose_forecast.data import hupa_windows

windows = hupa_windows.build(limit_per_subject=200)
for name in ("iob_units", "carbs_absorbed", "minutes_since_bolus", "minutes_since_carbs"):
    values = windows.channels[name][:, -1]
    print(
        f"{name:20s} 非零占比 {(values > 0).mean():6.1%}"
        f"  均值 {values.mean():7.2f}  最大 {values.max():7.2f}"
    )
