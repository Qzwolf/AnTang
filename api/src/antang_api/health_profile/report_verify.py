"""报告截图的第二遍读取：两遍对上才存。

PDF 有文字层，服务端的模板解析能直接读出字段（`report_parse`），不经过模型。
截图没有文字层，只能看图——而同一个模型读同一张图两次，给出的分档/均值可能不一样。

所以截图这条路要读两遍：模型读一遍（它调用工具时给的那些数字），服务端再独立读
一遍，两遍逐项对上才允许入库；对不上就宁可不存，并让患者改用 PDF 或手动记。
宁可少一条记录，也不要在患者的血糖记录里留一个错的数。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Final, Mapping

import httpx

from antang_api.models import MessageAttachment
from antang_api.settings import settings

# 两次读数允许的差：报告的精度是两位小数，超过这个就是真的读得不一样。
_TOLERANCE: Final = 0.011

# 日期、设备这类文字字段没有容差，必须一模一样。
_FIELD_LABELS: Final[dict[str, str]] = {
    "period_start": "监测开始日",
    "period_end": "监测结束日",
    "report_generated_at": "报告生成日",
    "device": "设备名",
    "device_serial": "序列号",
    "mean_mmol": "平均血糖",
    "sd_mmol": "标准差",
    "cv_percent": "变异系数",
    "ehba1c_percent": "预估糖化",
    "tir_percent": "目标范围内占比",
    "tar_percent": "高于目标占比",
    "tbr_percent": "低于目标占比",
    "normal_percent": "正常档占比",
    "low_percent": "偏低档占比",
    "very_low_percent": "很低档占比",
    "high_percent": "偏高档占比",
    "very_high_percent": "很高档占比",
}

RECHECK_PROMPT = """你在核对一张血糖报告截图上的数字，用来防止记账时把数字读错。

只输出一个 JSON 对象，不要解释、不要 markdown 代码块。字段如下，读不到的填 null，
不要猜、不要用「约」「大概」这类词：
period_start / period_end / report_generated_at：ISO 日期，例如 2025-07-28；
device：设备名原文；device_serial：设备序列号或编号；
mean_mmol：平均葡萄糖（mmol/L，不是 mg/dL）；sd_mmol：标准差；
cv_percent：变异系数（百分数，只写数字）；ehba1c_percent：预估糖化血红蛋白；
tir_percent / tar_percent / tbr_percent：目标范围内 / 高于 / 低于的占比；
normal_percent / low_percent / very_low_percent / high_percent / very_high_percent：
五档占比（报告按「正常/偏低/很低/偏高/很高」给的时候）；
lbgi_level：「低血糖风险」这四个并列标签里**被标出的那一档**，只填 高 / 中 / 低 / 最低
之一。报告没标出哪一档、或者你看不出标记落在哪个标签上，就填 null，不要挑一个填。
报告用 mg/dL 作单位时，先除以 18 再填。看不清的数字一定要填 null，不要凑。"""

# 「低血糖风险」四档：报告用中文标签，库里存英文键。四档是并列标签，
# 选中哪个只靠图形位置——所以这一项只能靠「读两遍、两遍一致」来定。
_LBGI_LEVELS: Final[dict[str, str]] = {
    "高": "high",
    "中": "moderate",
    "低": "low",
    "最低": "lowest",
    "high": "high",
    "moderate": "moderate",
    "low": "low",
    "lowest": "lowest",
}


def normalize_lbgi(value: object) -> str | None:
    """把读数里的风险档归一成库里的键；读不出或者不在四档里就当作没读到。"""

    text = _as_text(value)
    return _LBGI_LEVELS.get(text) if text else None



class UnreadableScreenshot(Exception):
    """第二遍什么也没读出来，没法核对。"""


@dataclass(frozen=True, slots=True)
class ReadMismatch:
    """两遍读出来的某一项不一样。"""

    field_name: str

    @property
    def label(self) -> str:
        return _FIELD_LABELS.get(self.field_name, self.field_name)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def compare_reads(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> list[ReadMismatch]:
    """逐项比两遍读数。

    只比第一遍声称读到了的项：第二遍没读出来算「对不上」——宁可让患者重来一次，
    也别把只有一遍支持的数字写进记录。
    """

    mismatched: list[ReadMismatch] = []
    for field_name in _FIELD_LABELS:
        claimed = first.get(field_name)
        if claimed is None or _as_text(claimed) == "":
            continue
        other = second.get(field_name)
        if field_name in {"period_start", "period_end", "report_generated_at"}:
            if _as_text(claimed) != _as_text(other):
                mismatched.append(ReadMismatch(field_name))
            continue
        if field_name in {"device", "device_serial"}:
            if _as_text(claimed) != _as_text(other):
                mismatched.append(ReadMismatch(field_name))
            continue
        claimed_number = _as_number(claimed)
        other_number = _as_number(other)
        if claimed_number is None or other_number is None:
            mismatched.append(ReadMismatch(field_name))
            continue
        if abs(claimed_number - other_number) > _TOLERANCE:
            mismatched.append(ReadMismatch(field_name))
    return mismatched


def _content_part(attachment: MessageAttachment) -> dict[str, object]:
    encoded = base64.b64encode(attachment.data).decode("ascii")
    if attachment.mime_type == "application/pdf":
        return {
            "type": "file",
            "file": {
                "filename": attachment.filename,
                "file_data": f"data:{attachment.mime_type};base64,{encoded}",
            },
        }
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{attachment.mime_type};base64,{encoded}"},
    }


async def read_report_again(
    attachment: MessageAttachment,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    """独立读一遍这份附件里的数字，返回字段字典。

    这是**第二次**读，用来和模型读出来的那遍对账；不和模型的上下文共享任何东西，
    否则两遍会被同一句话带偏。
    """

    owned = client is None
    http = client or httpx.AsyncClient(
        timeout=settings.hachimi_transcribe_timeout_seconds
    )
    try:
        response = await http.post(
            f"{settings.hachimi_openai_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.hachimi_api_key.get_secret_value()}"
            },
            json={
                "model": settings.hachimi_model_name.strip(),
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": RECHECK_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "把这张报告里的数字读成 JSON。"},
                            _content_part(attachment),
                        ],
                    },
                ],
            },
        )
    except httpx.HTTPError as error:
        raise UnreadableScreenshot("第二遍没读到：模型服务连不上。") from error
    finally:
        if owned:
            await http.aclose()
    if response.status_code != httpx.codes.OK:
        raise UnreadableScreenshot("第二遍没读到：模型服务这次没给结果。")
    try:
        body = response.json()
    except ValueError as error:
        raise UnreadableScreenshot("第二遍没读到：返回的内容看不懂。") from error
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise UnreadableScreenshot("第二遍没读到：返回里没有内容。")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise UnreadableScreenshot("第二遍没读到：返回的内容是空的。")
    parsed = _loads(content)
    if not parsed:
        raise UnreadableScreenshot("第二遍没读到任何数字。")
    return parsed


def _loads(raw: str) -> dict[str, object]:
    text = raw.strip()
    if text.startswith("```"):
        # 有些模型会把 JSON 包在代码块里，剥掉再解析。
        text = text.split("```")[1] if "```" in text[3:] else text.lstrip("`")
        text = text.removeprefix("json").strip()
        text = text.split("```")[0].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        loaded: Any = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(key): value for key, value in loaded.items()}
