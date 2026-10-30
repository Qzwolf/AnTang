"""报告 PDF 的字段识别。

夹具全部是**合成文本**：真实的患者报告不进仓库。版式照天津医科大学总医院那份
「动态葡萄糖评估报告」的排版写（标签、数字、单位、康熙部首、序号空格），
因为正则就是照这个版式写的。
"""

import pytest

from antang_api.health_profile.report_parse import (
    UnreadableReport,
    extract_pdf_text,
    parse_report_pdf,
    parse_report_text,
)

# 照真实报告的排版（含 pypdf 抽出来时那些康熙部首 ⾼ ⾎ ⻛ 和序列号里的空格）。
REPORT_TEXT = """
天津医科大学总医院
⻚码1/3
监测时间:2025/07/28-2025/07/31报告⽣成时间:2025/07/31
动态⾎糖评估报告
设备信息
设备型号:硅基动感Pro
设备名称:L T2410FE3P
eHbA1c1
预估糖化⾎红蛋⽩
5.34%
SD3
葡萄糖标准差
0.9mmol/L
MG2
平均葡萄糖值
5.81mmol/L
CV4
变异系数
15.53%
⾎糖数据
正常99.6%(23h54min)
低0.4%(6min)
很⾼(>13.9mmol/L)
"""


def test_it_reads_the_numbers_the_report_prints() -> None:
    parsed = parse_report_text(REPORT_TEXT)

    assert parsed.period_start.isoformat() == "2025-07-28"
    assert parsed.period_end.isoformat() == "2025-07-31"
    assert parsed.generated_at is not None
    assert parsed.generated_at.isoformat() == "2025-07-31"
    assert parsed.hospital == "天津医科大学总医院"
    assert parsed.device == "硅基动感Pro"
    # 报告把序列号排成了「L T2410FE3P」，读出来要去掉空格。
    assert parsed.device_serial == "LT2410FE3P"
    assert parsed.ehba1c_percent == pytest.approx(5.34)
    assert parsed.sd_mmol == pytest.approx(0.9)
    assert parsed.mean_mmol == pytest.approx(5.81)
    assert parsed.cv_percent == pytest.approx(15.53)
    assert parsed.normal_percent == pytest.approx(99.6)
    assert parsed.low_percent == pytest.approx(0.4)


def test_bands_the_report_does_not_print_stay_empty() -> None:
    parsed = parse_report_text(REPORT_TEXT)

    # 这份报告的首页只印了「正常」「低」两档；很高/高只有图例，不是数字。
    assert parsed.very_low_percent is None
    assert parsed.high_percent is None
    assert parsed.very_high_percent is None


def test_a_kangxi_radical_does_not_hide_a_band() -> None:
    # 「⾼」是康熙部首（U+2FBA），pypdf 抽这种字体的文字时常给出部首而不是汉字；
    # 不归一化，正则永远匹配不上，分档就静默丢了。
    text = REPORT_TEXT + "很⾼3.2%(0h46min)\n"

    parsed = parse_report_text(text)

    assert parsed.very_high_percent == pytest.approx(3.2)


def test_a_file_without_a_monitoring_period_is_refused() -> None:
    with pytest.raises(UnreadableReport) as error:
        parse_report_text("这是一份化验单，上面没有监测时间。")

    assert "监测时间" in str(error.value)


def test_a_report_with_numbers_we_cannot_read_is_refused() -> None:
    text = "监测时间:2025/07/28-2025/07/31\n动态血糖评估报告\n"

    with pytest.raises(UnreadableReport) as error:
        parse_report_text(text)

    assert "没有读出任何一个指标" in str(error.value)


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(UnreadableReport) as error:
        extract_pdf_text(b"")

    assert "空的" in str(error.value)


def test_something_that_is_not_a_pdf_is_refused() -> None:
    with pytest.raises(UnreadableReport) as error:
        parse_report_pdf(b"this is not a pdf at all")

    assert "打不开" in str(error.value) or "读不出来" in str(error.value)
