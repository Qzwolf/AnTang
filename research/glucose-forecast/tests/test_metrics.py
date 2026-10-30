import numpy as np
import pytest

from glucose_forecast.baselines import LinearDrift, Persistence, RidgeAutoregression
from glucose_forecast.metrics import (
    clarke_zone,
    clarke_zones,
    hypo_detection,
    mae,
    mard,
    rmse,
    summarize,
    zone_distribution,
)


@pytest.mark.parametrize(
    "reference, predicted, expected",
    [
        (100, 105, "A"),  # 5% 偏差
        (100, 125, "B"),  # 25% 超出 20%，但方向安全
        (100, 115, "A"),
        (60, 60, "A"),  # 双方低血糖
        (60, 120, "D"),  # 漏检低血糖
        (60, 250, "E"),  # 会把低血糖当高血糖处理
        (250, 60, "E"),
        (250, 120, "D"),  # 漏检高血糖
        (250, 260, "A"),  # 4% 偏差
        (100, 50, "C"),  # 真实在范围内，预测低血糖 → 不必要的补充
        (100, 250, "C"),  # 真实在范围内，预测高血糖 → 不必要的胰岛素
        (250, 320, "B"),  # 高血糖区偏差 28% > 20%，但方向安全
    ],
)
def test_clarke_zone_rules(reference, predicted, expected):
    assert clarke_zone(reference, predicted) == expected


def test_clarke_zone_boundary_of_twenty_percent():
    assert clarke_zone(100, 120) == "A"
    assert clarke_zone(100, 120.5) == "B"


def test_zone_distribution_sums_to_one_hundred():
    zones = clarke_zones(np.array([100.0, 60.0, 250.0, 100.0]), np.array([100.0, 250.0, 120.0, 50.0]))
    distribution = zone_distribution(zones)
    assert set(distribution) == {"A", "B", "C", "D", "E"}
    assert sum(distribution.values()) == pytest.approx(100.0)


def test_error_metrics_on_known_values():
    reference = np.array([[100.0, 100.0]])
    predicted = np.array([[110.0, 90.0]])
    assert rmse(reference, predicted) == pytest.approx(10.0)
    assert mae(reference, predicted) == pytest.approx(10.0)
    assert mard(reference, predicted) == pytest.approx(10.0)


def test_mard_ignores_hypoglycaemic_reference_values():
    reference = np.array([[60.0, 120.0]])
    predicted = np.array([[60.0, 132.0]])
    assert mard(reference, predicted) == pytest.approx(10.0)


def test_hypo_detection_counts_and_lead_time():
    reference = np.array([[120.0, 65.0], [120.0, 130.0]])
    predicted = np.array([[120.0, 60.0], [60.0, 130.0]])
    result = hypo_detection(reference, predicted)
    assert result["true_events"] == 1.0
    assert result["sensitivity"] == pytest.approx(1.0)
    assert result["false_alarms"] == 1.0
    # 预测在同一格就命中，提前量为 0
    assert result["lead_time_minutes"] == pytest.approx(0.0)


def test_hypo_detection_lead_time_is_positive_when_alarm_comes_first():
    reference = np.array([[120.0, 120.0, 65.0]])
    predicted = np.array([[120.0, 60.0, 60.0]])
    result = hypo_detection(reference, predicted)
    assert result["lead_time_minutes"] == pytest.approx(5.0)


def test_summarize_reports_every_zone():
    reference = np.full((2, 3), 100.0)
    predicted = np.full((2, 3), 105.0)
    summary = summarize(reference, predicted)
    for key in ("rmse_mgdl", "mae_mgdl", "mard_percent", "zone_a_percent", "sensitivity"):
        assert key in summary
    assert summary["zone_a_percent"] == pytest.approx(100.0)


def test_persistence_repeats_the_last_reading():
    history = np.array([[100.0, 110.0, 120.0]])
    future = np.zeros((1, 4))
    model = Persistence()
    model.fit(history, future)
    assert model.predict(history).tolist() == [[120.0, 120.0, 120.0, 120.0]]


def test_linear_drift_follows_an_upward_ramp():
    history = np.array([[100.0, 110.0, 120.0, 130.0]])
    model = LinearDrift(slope_points=4)
    model.fit(history, np.zeros((1, 3)))
    predicted = model.predict(history)
    assert predicted[0, 0] == pytest.approx(140.0)
    assert predicted[0, 2] == pytest.approx(160.0)


def test_ridge_autoregression_learns_the_identity_map():
    generator = np.random.default_rng(0)
    history = generator.uniform(80, 200, size=(200, 12))
    future = np.repeat(history[:, -1:], 3, axis=1)
    model = RidgeAutoregression(alpha=1e-6)
    model.fit(history, future)
    predicted = model.predict(history)
    assert np.abs(predicted - future).max() < 1.0


def test_predict_before_fit_is_an_error():
    with pytest.raises(RuntimeError, match="先 fit"):
        Persistence().predict(np.zeros((1, 12)))
