from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from glucose_forecast.data import ohiot1dm

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<patient id="{sid}" weight="99">'
BODY = """
  <glucose_level>
    <event ts="{first}" value="110"/>
    <event ts="{second}" value="115"/>
    <event ts="{second}" value="125"/>
  </glucose_level>
</patient>
"""


def write_subject(root: Path, release: str, split: str, sid: str, first: str, second: str) -> None:
    directory = root / release / split
    directory.mkdir(parents=True, exist_ok=True)
    text = HEADER.format(sid=sid) + BODY.format(first=first, second=second)
    (directory / f"{sid}-ws-{split}.xml").write_text(text, encoding="utf-8")


def test_reads_both_releases_and_merges_subjects(tmp_path: Path) -> None:
    write_subject(tmp_path, "2018", "train", "559", "04-07-2027 00:01:44", "04-07-2027 00:06:44")
    write_subject(tmp_path, "2020", "test", "540", "04-07-2027 00:01:44", "04-07-2027 00:06:44")

    readings = ohiot1dm.load_glucose(tmp_path)

    assert sorted(readings) == ["540", "559"]
    timestamps, glucose = readings["540"]
    assert timestamps.dtype == np.dtype("datetime64[ns]")
    assert datetime(2027, 7, 4, 0, 1, 44) == timestamps[0].astype("datetime64[s]").astype(datetime)
    # 读取层保留原始读数（同一时间戳两条都留着），去重是 to_regular_grid 的职责。
    assert list(glucose) == [110.0, 115.0, 125.0]


def test_grids_keep_gaps_as_missing(tmp_path: Path) -> None:
    write_subject(tmp_path, "2020", "train", "540", "04-07-2027 00:01:44", "04-07-2027 00:31:44")

    grids = ohiot1dm.grids(tmp_path)

    assert len(grids) == 1
    grid = grids[0]
    assert grid.subject == "540"
    assert int(grid.observed.sum()) == 2
    assert int(np.isnan(grid.glucose_mgdl).sum()) > 0
