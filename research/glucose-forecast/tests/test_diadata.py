"""DiaData loader 的离线测试：用一份手写的小 CSV，不碰 6.5 GB 的真文件。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from glucose_forecast.data import diadata

HEADER = "ts,PtID,Database,GlucoseCGM"


def _write(tmp_path: Path, rows: list[tuple[str, str, str, float]]) -> Path:
    path = tmp_path / "mdb.csv"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(HEADER + "\n")
        for ts, subject, database, value in rows:
            handle.write(f"{ts},{subject},{database},{value}\n")
    return path


def _dense_subject(
    subject: str, start: str, points: int, base: float = 120.0
) -> list[tuple[str, str, str, float]]:
    timestamps = pd.date_range(start, periods=points, freq="5min")
    return [
        (ts.strftime("%Y-%m-%dT%H:%M:%S.%f"), subject, "SYNTH", base + i * 0.1)
        for i, ts in enumerate(timestamps)
    ]


def test_iter_subject_frames_yields_one_block_per_subject(tmp_path: Path) -> None:
    rows = _dense_subject("001_SYNTH", "2024-01-01 00:00", 20)
    rows += _dense_subject("002_SYNTH", "2024-02-01 00:00", 15, base=140.0)
    path = _write(tmp_path, rows)

    seen = [(subject, len(frame)) for subject, _db, frame in diadata.iter_subject_frames(path)]

    assert seen == [("001_SYNTH", 20), ("002_SYNTH", 15)]


def test_iter_subject_frames_rejects_subjects_that_reappear(tmp_path: Path) -> None:
    interleaved = _dense_subject("001_SYNTH", "2024-01-01 00:00", 5)
    interleaved += _dense_subject("002_SYNTH", "2024-01-01 00:00", 5)
    interleaved += _dense_subject("001_SYNTH", "2024-01-01 01:00", 5)
    path = _write(tmp_path, sorted(interleaved, key=lambda row: row[0]))

    # 排序把两个受试者的行交错在一起：流式读取的前提不成立，必须报错而不是算错。
    with pytest.raises(ValueError, match="不是连续的一段"):
        list(diadata.iter_subject_frames(path))


def test_grid_from_frame_keeps_gaps_as_missing(tmp_path: Path) -> None:
    rows = _dense_subject("001_SYNTH", "2024-01-01 00:00", 6)
    rows += _dense_subject("001_SYNTH", "2024-01-01 02:00", 6)
    path = _write(tmp_path, rows)

    frames = list(diadata.iter_subject_frames(path))
    assert len(frames) == 1
    grid = diadata.grid_from_frame(frames[0][0], frames[0][2])

    # 00:25 之后直接跳到 02:00，中间的格子必须是 NaN，不能插值。
    assert np.isnan(grid.glucose_mgdl[6:18]).all()
    assert grid.observed.sum() == 12
    assert grid.coverage == pytest.approx(12 / len(grid))


def test_grid_from_frame_converts_only_mgdl(tmp_path: Path) -> None:
    rows = _dense_subject("001_SYNTH", "2024-01-01 00:00", 4, base=100.0)
    path = _write(tmp_path, rows)

    _subject, _db, frame = next(iter(diadata.iter_subject_frames(path)))
    grid = diadata.grid_from_frame("001_SYNTH", frame)

    # DiaData 已经是 mg/dL，任何换算都会把 100 变成 1801。
    assert grid.valid_values()[0] == pytest.approx(100.0)


def test_build_window_cache_skips_subjects_without_a_full_window(tmp_path: Path) -> None:
    rows = _dense_subject("001_SYNTH", "2024-01-01 00:00", 90)
    rows += _dense_subject("002_SYNTH", "2024-02-01 00:00", 10)
    path = _write(tmp_path, rows)

    cache = diadata.build_window_cache(
        path, max_windows_per_subject=50, progress=False
    )

    assert {str(item) for item in cache.subject} == {"001_SYNTH"}
    assert cache.history.shape[1] == 72
    assert cache.future.shape[1] == 12
    assert len(cache.future) == len(cache.subject) == len(cache.origin_time)


def test_cache_round_trip(tmp_path: Path) -> None:
    rows = _dense_subject("001_SYNTH", "2024-01-01 00:00", 90)
    path = _write(tmp_path, rows)
    cache = diadata.build_window_cache(
        path, max_windows_per_subject=5, progress=False
    )

    saved = diadata.save_cache(cache, tmp_path / "cache.npz")
    restored = diadata.load_cache(saved)

    assert restored.history.shape == cache.history.shape
    assert restored.subject.tolist() == cache.subject.tolist()
    assert restored.origin_index.tolist() == cache.origin_index.tolist()
    np.testing.assert_allclose(
        restored.history.astype(np.float64), cache.history.astype(np.float64)
    )


def test_window_sets_group_by_subject(tmp_path: Path) -> None:
    rows = _dense_subject("001_SYNTH", "2024-01-01 00:00", 90)
    rows += _dense_subject("002_SYNTH", "2024-02-01 00:00", 90, base=150.0)
    path = _write(tmp_path, rows)
    cache = diadata.build_window_cache(
        path, max_windows_per_subject=10, progress=False
    )

    sets = cache.as_window_sets()

    assert [item.subject for item in sets] == ["001_SYNTH", "002_SYNTH"]
    assert sum(len(item) for item in sets) == len(cache.history)
