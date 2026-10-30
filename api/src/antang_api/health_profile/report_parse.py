"""从「动态葡萄糖评估报告」这类 PDF 里读出字段。

为什么不用大模型读：同一家医院导出的报告版式是固定的，模板正则能稳定读出数字，
而模型每次读同一份都可能给出不同的数（20 份实测里就出现过把分档编出来的情况）。
这里只认版式：读不出就直说读不出，不猜、不补。

限制也写在这里：只处理**文字层**能提取的 PDF（不是扫描件/照片），且只认真实印出来
的字段——报告没印低血糖风险档位、没印某一档百分比，就留空。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO

from pypdf import PdfReader

# pypdf 遇到嵌入式 Type1 字体时会逐字警告（一条 PDF 能刷上百行），
# 而我们的判断依据是「能不能读出来」，不是这些警告。
logging.getLogger("pypdf").setLevel(logging.ERROR)


class UnreadableReport(Exception):
    """这份 PDF 不是我们能认的报告，或者文字层读不出来。"""


@dataclass(frozen=True, slots=True)
class ParsedReport:
    period_start: date
    period_end: date
    generated_at: date | None
    device: str
    device_serial: str
    hospital: str
    mean_mmol: float | None = None
    sd_mmol: float | None = None
    cv_percent: float | None = None
    ehba1c_percent: float | None = None
    normal_percent: float | None = None
    low_percent: float | None = None
    very_low_percent: float | None = None
    high_percent: float | None = None
    very_high_percent: float | None = None
    # 读不到的字段名，界面要照实说明，而不是显示成 0。
    missing: tuple[str, ...] = field(default=())

    def metrics(self) -> dict[str, float | None]:
        return {
            "mean_mmol": self.mean_mmol,
            "sd_mmol": self.sd_mmol,
            "cv_percent": self.cv_percent,
            "ehba1c_percent": self.ehba1c_percent,
            "normal_percent": self.normal_percent,
            "low_percent": self.low_percent,
            "very_low_percent": self.very_low_percent,
            "high_percent": self.high_percent,
            "very_high_percent": self.very_high_percent,
        }


_FACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("mean_mmol", "平均葡萄糖", "mmol/L"),
    ("sd_mmol", "标准差", "mmol/L"),
    ("cv_percent", "变异系数", "%"),
    ("ehba1c_percent", "预估糖化", "%"),
    ("normal_percent", "正常档", "%"),
    ("low_percent", "偏低档", "%"),
    ("very_low_percent", "很低档", "%"),
    ("high_percent", "偏高档", "%"),
    ("very_high_percent", "很高档", "%"),
)


def render_parsed_facts(parsed: ParsedReport) -> str:
    """把模板解析出来的字段写成给模型看的「既成事实」。

    模型看到这段就不必自己去数一遍：数字是程序从文件里读的，比看图稳。
    """

    lines = [
        f"监测区间 {parsed.period_start.isoformat()} 至 {parsed.period_end.isoformat()}",
    ]
    if parsed.device:
        serial = f"（{parsed.device_serial}）" if parsed.device_serial else ""
        lines.append(f"设备 {parsed.device}{serial}")
    if parsed.generated_at is not None:
        lines.append(f"报告生成于 {parsed.generated_at.isoformat()}")
    if parsed.hospital:
        lines.append(f"出具单位 {parsed.hospital}")
    for field_name, label, unit in _FACT_FIELDS:
        value: float | None = getattr(parsed, field_name)
        if value is None:
            continue
        lines.append(f"{label} {value:g}{unit}")
    return "；".join(lines)


_DATE = r"(\d{4})\s*[/\-年]\s*(\d{1,2})\s*[/\-月]\s*(\d{1,2})"
_PERIOD = re.compile(rf"监测时间\s*[:：]?\s*{_DATE}\s*-{_DATE}")
_GENERATED = re.compile(rf"报告生成时间\s*[:：]?\s*{_DATE}")
_DEVICE = re.compile(r"设备型号\s*[:：]\s*([^\n]{1,40})")
_SERIAL = re.compile(r"设备名称\s*[:：]\s*([^\n]{1,40})")
_HOSPITAL = re.compile(r"^([^\n]{2,30}(?:医院|中心|门诊部))$", re.MULTILINE)
_NUMBER = r"(\d{1,3}(?:\.\d{1,2})?)"
_EHBA1C = re.compile(rf"预估糖化血红蛋白[^\n]{{0,12}}\n?\s*{_NUMBER}\s*%")
_SD = re.compile(rf"葡萄糖标准差[^\n]{{0,12}}\n?\s*{_NUMBER}\s*mmol/L")
_MEAN = re.compile(rf"平均葡萄糖(?:值)?[^\n]{{0,12}}\n?\s*{_NUMBER}\s*mmol/L")
_CV = re.compile(rf"变异系数[^\n]{{0,12}}\n?\s*{_NUMBER}\s*%")


def _band(text: str, label: str) -> float | None:
    """读「很高 2.3%」这种一行一档的写法；后面跟着时间（3h40min）也认。"""

    match = re.search(rf"{label}\s*{_NUMBER}\s*%", text)
    return float(match.group(1)) if match else None


def _number(match: re.Match[str] | None) -> float | None:
    return float(match.group(1)) if match else None


def _to_date(match: re.Match[str], offset: int) -> date:
    year = int(match.group(offset))
    month = int(match.group(offset + 1))
    day = int(match.group(offset + 2))
    return date(year, month, day)


def normalize_text(raw: str) -> str:
    """把 PDF 里常见的康熙部首（⾼ ⾎ ⻛）还原成正常汉字，并把全角符号收掉。"""

    return unicodedata.normalize("NFKC", raw)


def parse_report_text(raw: str) -> ParsedReport:
    """从提取出来的文字里读字段。认不出来就抛 UnreadableReport。"""

    text = normalize_text(raw)
    period = _PERIOD.search(text)
    if period is None:
        raise UnreadableReport(
            "这份文件里没有找到「监测时间」，看着不像动态葡萄糖评估报告。"
        )
    start = _to_date(period, 1)
    end = _to_date(period, 4)
    if end < start:
        raise UnreadableReport("报告上的监测时间起止倒过来了，先核对一下这份文件。")

    generated = None
    generated_match = _GENERATED.search(text)
    if generated_match is not None:
        generated = _to_date(generated_match, 1)

    device = _DEVICE.search(text)
    serial = _SERIAL.search(text)
    hospital = _HOSPITAL.search(text)
    parsed = ParsedReport(
        period_start=start,
        period_end=end,
        generated_at=generated,
        # 序列号里常被排版塞了空格（L T2410FE3P），去掉。
        device=_squash(device.group(1)) if device else "",
        device_serial=_squash(serial.group(1)) if serial else "",
        hospital=_squash(hospital.group(1)) if hospital else "",
        mean_mmol=_number(_MEAN.search(text)),
        sd_mmol=_number(_SD.search(text)),
        cv_percent=_number(_CV.search(text)),
        ehba1c_percent=_number(_EHBA1C.search(text)),
        normal_percent=_band(text, "正常"),
        low_percent=_band(text, "低"),
        very_low_percent=_band(text, "很低"),
        high_percent=_band(text, "高"),
        very_high_percent=_band(text, "很高"),
    )
    if all(value is None for value in parsed.metrics().values()):
        raise UnreadableReport(
            "这份文件里没有读出任何一个指标，可能是扫描件或其它模板。"
        )
    return parsed


def _squash(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def extract_pdf_text(data: bytes) -> str:
    """取出 PDF 的文字层。扫描件/图片型 PDF 会返回空字符串。"""

    if not data:
        raise UnreadableReport("文件是空的，先确认选对了文件。")
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as error:  # pypdf 对坏文件会抛各种异常，统一成一句话
        raise UnreadableReport("这个 PDF 打不开，可能没下载完整。") from error
    if reader.is_encrypted:
        raise UnreadableReport("这个 PDF 加了密码，读不了。")
    try:
        text = "".join((page.extract_text() or "") for page in reader.pages)
    except Exception as error:
        raise UnreadableReport("这个 PDF 的文字读不出来，可能是扫描件。") from error
    if text.strip() == "":
        raise UnreadableReport(
            "这个 PDF 里没有文字层（大概率是扫描件或截图），读不出数字。"
        )
    return text


def parse_report_pdf(data: bytes) -> ParsedReport:
    return parse_report_text(extract_pdf_text(data))
