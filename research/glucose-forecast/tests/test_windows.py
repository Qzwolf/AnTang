import numpy as np
import pytest

from glucose_forecast.series import CgmSeries, to_regular_grid
from glucose_forecast.windows import build_windows, history_segments


def _series(values, drop: tuple[int, ...] = ()):
    """构造等间隔 5 分钟的读数；drop 的索引整条缺失（模拟 CGM 断连）。"""
    values = np.asarray(values, dtype=np.float64)
    seconds = np.arange(values.size) * 300
    keep = np.array([index not in set(drop) for index in range(values.size)])
    return CgmSeries(
        subject="t1",
        timestamps=seconds[keep].astype("datetime64[s]").astype("datetime64[ns]"),
        glucose_mgdl=values[keep],
    )


def test_regular_grid_keeps_the_hole_instead_of_shifting():
    grid = to_regular_grid(_series(np.arange(5, dtype=float) + 100, drop=(2,)))
    # 中间少一条读数，网格仍按 5 分钟 5 格铺开，缺口那格标记为未观测
    assert len(grid) == 5
    assert grid.coverage == pytest.approx(0.8)
    assert np.isnan(grid.glucose_mgdl[2])
    assert not grid.observed[2]


def test_implausible_readings_are_dropped():
    grid = to_regular_grid(_series([100, 5, 900, 120]))
    assert np.isfinite(grid.glucose_mgdl[grid.observed]).all()
    assert grid.observed.sum() == 2


def test_only_windows_that_avoid_the_gap_survive():
    values = np.arange(8, dtype=float) + 100
    grid = to_regular_grid(_series(values, drop=(5,)))
    windows = build_windows(grid, history_points=2, horizon_points=2)
    # 跨度 4，起点 0..4；起点 0、1 不碰缺口，2、3、4 都会碰上
    assert len(windows) == 2
    assert windows.origin_index.tolist() == [1, 2]
    for history, forecast in zip(windows.history, windows.future, strict=True):
        assert np.isfinite(history).all() and np.isfinite(forecast).all()


def test_window_shapes_and_origin_index():
    grid = to_regular_grid(_series(np.arange(20, dtype=float) + 100))
    windows = build_windows(grid, history_points=12, horizon_points=3)
    assert windows.history.shape == (20 - 15 + 1, 12)
    assert windows.future.shape == (20 - 15 + 1, 3)
    assert windows.origin_index[0] == 11
    assert windows.origin_time[0] == grid.timestamps[11]


def test_stride_thins_the_windows():
    grid = to_regular_grid(_series(np.arange(24, dtype=float) + 100))
    dense = build_windows(grid, history_points=12, horizon_points=3)
    thin = build_windows(grid, history_points=12, horizon_points=3, stride=3)
    assert len(dense) == 10
    assert len(thin) == 4


def test_short_recording_yields_no_windows():
    grid = to_regular_grid(_series([100, 110, 120]))
    windows = build_windows(grid, history_points=12, horizon_points=3)
    assert len(windows) == 0


def test_history_segments_split_evenly():
    history = np.arange(2 * 72, dtype=float).reshape(2, 72)
    segments = history_segments(history, segments=6)
    assert segments.shape == (2, 6, 12)
    assert segments[0, 0, -1] == 11.0
    assert segments[0, 1, 0] == 12.0


def test_history_segments_rejects_indivisible_length():
    with pytest.raises(ValueError, match="不能被段数"):
        history_segments(np.zeros((1, 70)), segments=6)


def test_unsorted_timestamps_are_rejected():
    timestamps = np.array([0, 600, 300], dtype="int64").astype("datetime64[s]")
    with pytest.raises(ValueError, match="严格升序"):
        CgmSeries(
            subject="t1",
            timestamps=timestamps.astype("datetime64[ns]"),
            glucose_mgdl=np.array([100.0, 110.0, 120.0]),
        )
