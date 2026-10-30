from __future__ import annotations

from datetime import timedelta

import pytest

from antang_api.health_profile.cgm_import import (
    CgmParseError,
    detect_unit,
    parse_cgm_csv,
)

HEADER = "时间,血糖,单位,趋势\n"


def test_mmol_rows_are_converted_and_kept_in_order():
    data = (HEADER + "2026-10-20 08:00,5.5,mmol/L,Flat\n2026-10-20 08:05,6.1,mmol/L,Rising\n").encode()

    result = parse_cgm_csv(data)

    assert result.unit == "mmol/L"
    assert [sample.value_mg_dl for sample in result.samples] == [99, 110]
    assert result.samples[0].trend == "Flat"
    assert result.problems == [
        "有 2 行没有时区，已按 +08:00 处理；如果不对，请在导出时选带时区的格式。"
    ]


def test_naive_times_get_the_local_offset_and_a_warning():
    data = ("time,value\n2026-10-20 08:00,100\n").encode()

    result = parse_cgm_csv(data)

    assert result.samples[0].recorded_at.utcoffset() == timedelta(hours=8)
    assert any("没有时区" in problem for problem in result.problems)


def test_epoch_milliseconds_are_understood():
    data = ("timestamp,sgv\n1792425600000,120\n").encode()

    result = parse_cgm_csv(data)

    assert result.samples[0].value_mg_dl == 120
    assert result.samples[0].recorded_at.year == 2026


def test_rows_outside_the_plausible_range_are_reported_not_guessed():
    data = ("time,value\n2026-10-20 08:00,900\n2026-10-20 08:05,5\n2026-10-20 08:10,120\n").encode()

    result = parse_cgm_csv(data)

    assert [sample.value_mg_dl for sample in result.samples] == [120]
    assert len(result.problems) >= 2
    assert any("超出" in problem for problem in result.problems)


def test_duplicate_timestamps_inside_one_file_are_collapsed():
    data = ("time,value\n2026-10-20 08:00,100\n2026-10-20 08:00,104\n").encode()

    result = parse_cgm_csv(data)

    assert len(result.samples) == 1
    assert any("重复" in problem for problem in result.problems)


def test_a_file_without_time_or_value_columns_is_refused():
    with pytest.raises(CgmParseError):
        parse_cgm_csv("name,age\nfoo,1\n".encode())


def test_bom_and_gbk_exports_are_readable():
    text = "时间,血糖\n2026-10-20 09:00,6.2\n"
    data = text.encode("gb18030")

    result = parse_cgm_csv(data)

    assert result.samples[0].value_mg_dl == 112


def test_unit_is_inferred_from_magnitude_when_the_header_is_silent():
    assert detect_unit(["time", "value"], [["x", "5.4"], ["y", "6.1"]], 1) == "mmol/L"
    assert detect_unit(["time", "value"], [["x", "98"], ["y", "140"]], 1) == "mg/dL"


def test_rows_with_a_different_unit_are_skipped_and_counted():
    data = (
        "时间,血糖,单位\n"
        "2026-10-20 08:00,5.5,mmol/L\n"
        "2026-10-20 08:05,220,mg/dL\n"
    ).encode()

    result = parse_cgm_csv(data)

    assert [sample.value_mg_dl for sample in result.samples] == [99]
    assert result.skipped_rows == 1
    assert any("单位是 mg/dL" in problem for problem in result.problems)
