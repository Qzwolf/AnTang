"""CGM 导出文件的确定性解析。

设计原则（见 docs/glucose-data-format.md）：
- 数值必须由代码解析，不让模型"读图猜数字"；模型只在图片报告兜底时参与，且标低置信度。
- 时间必须带时区；没有时区的一律按 +08:00 处理并记一条提示，不静默假设。
- 认不出的行不猜：进 problems，由用户在预览页决定。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

MG_PER_MMOL = 18.0
DEFAULT_OFFSET = timezone(timedelta(hours=8))
MAX_ROWS = 50_000
VALUE_MIN = 20
VALUE_MAX = 600

TIME_NAMES = {"time", "timestamp", "datetime", "date_time", "date", "时间", "测量时间", "记录时间"}
VALUE_NAMES = {"value", "glucose", "glucose_mgdl", "sgv", "血糖", "血糖值", "数值"}
UNIT_NAMES = {"unit", "单位"}
TREND_NAMES = {"trend", "direction", "趋势", "趋势箭头"}
DEVICE_NAMES = {"device", "device_id", "设备", "设备号"}


class CgmParseError(ValueError):
    """文件本身无法当作血糖数据（例如根本不是表格）。"""


@dataclass
class ParsedSample:
    recorded_at: datetime
    value_mg_dl: int
    trend: str | None = None
    device_id: str = ""


@dataclass
class ParseResult:
    samples: list[ParsedSample] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unit: str = "mg/dL"
    naive_times: int = 0
    skipped_rows: int = 0


def _normalize_header(name: str) -> str:
    return re.sub(r"[\s_\-]+", "_", name.strip().lower())


def _find_column(header: list[str], names: set[str]) -> int | None:
    normalized = [name.strip().lower() for name in header]
    for index, name in enumerate(normalized):
        if name in names:
            return index
    for index, name in enumerate(normalized):
        if any(candidate in name for candidate in names if candidate.isascii()):
            return index
    return None


def _strip_bom(text: str) -> str:
    return text.lstrip("\ufeff")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return _strip_bom(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise CgmParseError("文件编码认不出来，请导出为 UTF-8 或 Excel 另存为 CSV。")


def _sniff_delimiter(text: str) -> str:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    for candidate in (",", ";", "\t"):
        if first_line.count(candidate) >= 1:
            return candidate
    return ","


def detect_unit(header: list[str], rows: list[list[str]], value_index: int) -> str:
    unit_index = _find_column(header, UNIT_NAMES)
    if unit_index is not None:
        for row in rows[:20]:
            if unit_index < len(row):
                cell = row[unit_index].lower()
                if "mmol" in cell:
                    return "mmol/L"
                if "mg" in cell:
                    return "mg/dL"
    header_text = " ".join(header).lower()
    if "mmol" in header_text:
        return "mmol/L"
    numbers = []
    for row in rows[:50]:
        if value_index < len(row):
            try:
                numbers.append(float(row[value_index]))
            except ValueError:
                continue
    if numbers and max(numbers) <= 35:
        return "mmol/L"
    return "mg/dL"


def _parse_time(cell: str) -> tuple[datetime, bool]:
    raw = cell.strip()
    if not raw:
        raise ValueError("时间是空的")
    if re.fullmatch(r"\d{10}", raw) or re.fullmatch(r"\d{13}", raw):
        seconds = int(raw) / (1000 if len(raw) == 13 else 1)
        return datetime.fromtimestamp(seconds, tz=UTC), False
    # 先把带偏移的 ISO 格式吃掉（含 Z）；不能按 "+" 切掉，那会把时区一起扔掉。
    iso = raw.replace("/", "-").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=DEFAULT_OFFSET), True
        return parsed, False
    text = raw.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", " ")
    text = text.replace("T", " ").strip()
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d-%Y %H:%M",
    ):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=DEFAULT_OFFSET), True
    raise ValueError(f"时间认不出来：{raw[:32]}")


def _to_mg_dl(value: float, unit: str) -> int:
    if unit == "mmol/L":
        return round(value * MG_PER_MMOL)
    return round(value)


def parse_cgm_csv(data: bytes) -> ParseResult:
    """解析 CSV 导出；认不出的行进 problems，不猜测。"""

    text = _decode(data)
    delimiter = _sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise CgmParseError("这个文件里只有表头或没有数据行。")

    header = rows[0]
    time_index = _find_column(header, TIME_NAMES)
    value_index = _find_column(header, VALUE_NAMES)
    if time_index is None or value_index is None:
        raise CgmParseError(
            "没找到时间列或数值列。请确认表头里有「时间/Time」和「血糖/Value」。"
        )

    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise CgmParseError(f"文件有 {len(body)} 行，超过单次导入上限 {MAX_ROWS} 行。")

    unit = detect_unit(header, body, value_index)
    unit_index = _find_column(header, UNIT_NAMES)
    trend_index = _find_column(header, TREND_NAMES)
    device_index = _find_column(header, DEVICE_NAMES)

    result = ParseResult(unit=unit)
    seen: set[datetime] = set()
    for line_number, row in enumerate(body, start=2):
        if time_index >= len(row) or value_index >= len(row):
            result.problems.append(f"第 {line_number} 行少一列，已跳过。")
            result.skipped_rows += 1
            continue
        if unit_index is not None and unit_index < len(row):
            row_unit = row[unit_index].strip().lower()
            if row_unit and "mmol" in row_unit and unit != "mmol/L":
                result.problems.append(
                    f"第 {line_number} 行单位是 mmol/L，与其他行不一致，已跳过。"
                )
                result.skipped_rows += 1
                continue
            if row_unit and "mg" in row_unit and unit != "mg/dL":
                result.problems.append(
                    f"第 {line_number} 行单位是 mg/dL，与其他行不一致，已跳过。"
                )
                result.skipped_rows += 1
                continue
        try:
            recorded_at, naive = _parse_time(row[time_index])
        except ValueError as error:
            result.problems.append(f"第 {line_number} 行：{error}")
            result.skipped_rows += 1
            continue
        if naive:
            result.naive_times += 1
        try:
            raw_value = float(row[value_index].strip())
        except ValueError:
            result.problems.append(f"第 {line_number} 行：数值不是数字（{row[value_index][:16]}）")
            result.skipped_rows += 1
            continue
        value_mg_dl = _to_mg_dl(raw_value, unit)
        if not VALUE_MIN <= value_mg_dl <= VALUE_MAX:
            result.problems.append(
                f"第 {line_number} 行：{value_mg_dl} mg/dL 超出 {VALUE_MIN}–{VALUE_MAX}，已跳过。"
            )
            result.skipped_rows += 1
            continue
        if recorded_at in seen:
            result.problems.append(f"第 {line_number} 行：同一时间的重复记录，已跳过。")
            result.skipped_rows += 1
            continue
        seen.add(recorded_at)
        result.samples.append(
            ParsedSample(
                recorded_at=recorded_at,
                value_mg_dl=value_mg_dl,
                trend=(row[trend_index].strip() or None)
                if trend_index is not None and trend_index < len(row)
                else None,
                device_id=(row[device_index].strip()[:64])
                if device_index is not None and device_index < len(row)
                else "",
            )
        )

    result.samples.sort(key=lambda sample: sample.recorded_at)
    if result.naive_times:
        result.problems.append(
            f"有 {result.naive_times} 行没有时区，已按 +08:00 处理；如果不对，请在导出时选带时区的格式。"
        )
    return result
