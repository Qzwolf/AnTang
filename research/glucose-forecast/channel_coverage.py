"""看看四个模态在 HUPA-UCM 里到底有多少"真的记了"。"""


from glucose_forecast.data import hupa_windows

windows = hupa_windows.build(limit_per_subject=200)
total = windows.history.shape[0]
for name in ("bolus_units", "carbs_grams", "heart_rate_bpm", "steps"):
    values = windows.channels[name][:, -12:]
    present = (values > 0).any(axis=1).mean()
    print(f"{name:16s} 有值的窗口占比 {present:6.1%}  均值 {values.sum(axis=1).mean():7.2f}")
print(f"窗口总数 {total}  受试者 {len(set(windows.subject))}")
