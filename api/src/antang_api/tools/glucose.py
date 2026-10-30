"""血糖读数的只读工具。

患者自己在 App 里记的读数已经每轮放进提示词（近 7 天摘要 + 最近 5 条），
这个工具是给「想再往前看一点」用的：模型可以按天数和条数要明细。
不写库、不判读——判读留给对话本身。
"""

from datetime import UTC, date, datetime, timedelta
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.database import session_factory as default_session_factory
from antang_api.health_profile.glucose import (
    GLUCOSE_CONTEXT_LABELS,
    GLUCOSE_DEVICE_LABELS,
    list_glucose_readings,
    measured_time_label,
    record_glucose_reading,
    record_glucose_report as save_glucose_report,
    delete_glucose_report as drop_glucose_report,
    summarize_glucose,
    to_mmol,
)
from antang_api.models import AgentRun, GlucoseReport, Message, MessageAttachment, MessageRole
from antang_api.health_profile.report_parse import (
    ParsedReport,
    UnreadableReport,
    parse_report_pdf,
)
from antang_api.health_profile.report_verify import (
    UnreadableScreenshot,
    compare_reads,
    normalize_lbgi,
    read_report_again,
)

logger = logging.getLogger(__name__)

# 第二遍读数：把附件再读一次，返回字段字典（键名和工具参数一致）。
Recheck = Callable[[MessageAttachment], Awaitable[dict[str, object]]]

READ_GLUCOSE_TOOL_NAME = "read_glucose_readings"
RECORD_GLUCOSE_TOOL_NAME = "record_glucose_readings"
RECORD_GLUCOSE_REPORT_TOOL_NAME = "record_glucose_report"
DELETE_GLUCOSE_REPORT_TOOL_NAME = "delete_glucose_report"

# 患者发来的报告里能读出的一条读数：化验单一般几个点，CGM 报告最多一天几十个点。
MAX_REPORT_READINGS = 120
# 报告可能是补传的，但再久以前的数字意义不大，也容易和别的记录打架。
OLDEST_REPORT_DAYS = 400

GlucoseReportSource = Literal["cgm", "lab", "fingerstick", "manual"]
# 工具里只收「点得出牌子」的三种：没点名的留空，别拿 other 占位。
GlucoseMeter = Literal["roche", "abbott", "sannuo"]
GlucoseReportDevice = Literal["roche", "abbott", "sannuo", "other"]
GlucoseReportContext = Literal[
    "fasting", "pre_meal", "post_meal", "bedtime", "night", "other"
]



class GlucoseReadRequest(BaseModel):
    """要看多长窗口、多少条患者自己记的血糖。"""

    model_config = ConfigDict(extra="forbid")

    days: Annotated[
        int,
        Field(
            ge=1,
            le=90,
            description="往前看多少天，默认 7 天；患者说「这两个月」时才用大值。",
        ),
    ] = 7
    limit: Annotated[
        int,
        Field(ge=1, le=50, description="最多返回多少条明细，默认 10 条。"),
    ] = 10


def build_glucose_read_tool(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建读取血糖明细的工具。"""

    @tool(READ_GLUCOSE_TOOL_NAME)
    async def read_glucose_readings(
        request: GlucoseReadRequest,
        runtime: ToolRuntime[AgentContext],
    ) -> dict[str, object]:
        """读取患者自己在 App 里记的血糖读数。

        当患者问到具体的数字（「我上周那次多少」「这两天是不是有点飘」），
        而上下文里的摘要不够时调用。数值单位是 mmol/L。
        这些读数由患者手动录入，可能漏记，不是连续监测；不要说成 CGM 曲线。
        """

        async with sessions() as session:
            summary = await summarize_glucose(
                session,
                user_id=runtime.context.user_id,
                days=request.days,
            )
            readings = await list_glucose_readings(
                session,
                user_id=runtime.context.user_id,
                days=request.days,
                limit=request.limit,
            )

        if summary["count"] == 0:
            return {
                "days": request.days,
                "count": 0,
                "note": "这段时间患者没有记血糖。",
            }

        average = summary["average_mg_dl"]
        minimum = summary["minimum_mg_dl"]
        maximum = summary["maximum_mg_dl"]
        low_target = summary["target_low_mg_dl"]
        high_target = summary["target_high_mg_dl"]
        return {
            "days": request.days,
            "count": summary["count"],
            "average_mmol": None if average is None else to_mmol(round(average)),
            "minimum_mmol": None if minimum is None else to_mmol(minimum),
            "maximum_mmol": None if maximum is None else to_mmol(maximum),
            "target_low_mmol": to_mmol(low_target),
            "target_high_mmol": to_mmol(high_target),
            "below_target_count": summary["below_target_count"],
            "in_target_count": summary["in_target_count"],
            "above_target_count": summary["above_target_count"],
            "readings": [
                {
                    "measured_at": measured_time_label(
                        reading.measured_at, reading.measured_precision
                    ),
                    "approximate": reading.measured_precision == "daypart",
                    "value_mmol": to_mmol(reading.value_mg_dl),
                    "context": GLUCOSE_CONTEXT_LABELS.get(
                        reading.context, reading.context
                    ),
                    "source": reading.source,
                    "device": GLUCOSE_DEVICE_LABELS.get(reading.source_device)
                    or None,
                    "note": reading.note,
                }
                for reading in readings
            ],
            "note": "患者自己记的，可能漏记，不是连续监测。",
        }

    return read_glucose_readings


class GlucosePoint(BaseModel):
    """报告里读到的一条血糖：什么时候、多少、什么场景。"""

    model_config = ConfigDict(extra="forbid")

    measured_at: Annotated[
        str,
        Field(
            description=(
                "测量时间，必须带时区，例如 2026-10-24T07:10:00+08:00。"
                "报告只写了日期（如 10-20）时用当天早上 07:00 并说明是日期精度。"
            )
        ),
    ]
    value_mmol: Annotated[
        float,
        Field(
            ge=1.0,
            le=40.0,
            description="血糖值，单位 mmol/L。报告写的是 mg/dL 时先除以 18。",
        ),
    ]
    context: Annotated[
        GlucoseReportContext,
        Field(
            default="other",
            description="空腹/餐前/餐后/睡前/夜间，报告没写就用 other。",
        ),
    ] = "other"
    note: Annotated[
        str | None,
        Field(default=None, description="一条备注，例如「报告日均值」「化验单」"),
    ] = None
    time_precision: Annotated[
        Literal["exact", "daypart"],
        Field(
            default="exact",
            description=(
                "这条时间有多准。患者或报告给了钟点就 exact；患者只说得出"
                "「早上 / 中午 / 下午 / 晚上 / 半夜」这种时段就填 daypart，"
                "并把 measured_at 写成该时段的代表时刻（早上 07:00、中午 12:00、"
                "下午 15:00、晚上 20:00、半夜 02:00），分钟必须是 00。"
                "宁可按大概记，也不要自己编一个几点几分。"
            ),
        ),
    ] = "exact"


async def _report_attachment_id(
    session: AsyncSession, run_id: UUID
) -> UUID | None:
    """这一轮用户消息里如果恰好挂了一份 PDF，就是这份速览的原文件。

    只在「恰好一份」时认：一条消息里挂了两份 PDF，谁是谁说不清，宁可不指。
    """

    attachment = await _run_report_attachment(session, run_id)
    return attachment.id if attachment is not None else None


async def _run_report_attachment(
    session: AsyncSession, run_id: UUID
) -> MessageAttachment | None:
    """这一轮用户消息里挂着的那份报告（PDF 或截图）。

    同样只在「恰好一份」时认：两份一起发过来，谁对应哪段数字说不清，就不猜。
    """

    trigger = await session.scalar(
        select(AgentRun.trigger_message_id).where(AgentRun.id == run_id)
    )
    if trigger is None:
        return None
    found = (
        await session.scalars(
            select(MessageAttachment)
            .where(
                MessageAttachment.message_id == trigger,
                MessageAttachment.mime_type.in_(
                    ("application/pdf", "image/jpeg", "image/png")
                ),
            )
            .order_by(MessageAttachment.id)
        )
    ).all()
    return found[0] if len(found) == 1 else None


def _parsed_fields(parsed: ParsedReport) -> dict[str, object]:
    """模板解析出来的字段，键名和工具参数一致，后面按同一套规则校验。"""

    return {
        "period_start": parsed.period_start,
        "period_end": parsed.period_end,
        "report_generated_at": parsed.generated_at,
        "device": parsed.device,
        "device_serial": parsed.device_serial,
        "mean_mmol": parsed.mean_mmol,
        "sd_mmol": parsed.sd_mmol,
        "cv_percent": parsed.cv_percent,
        "ehba1c_percent": parsed.ehba1c_percent,
        # 这类报告只按五档印占比，TIR/TAR/TBR 不单独出现：不拿五档拼三档。
        "tir_percent": None,
        "tar_percent": None,
        "tbr_percent": None,
        "normal_percent": parsed.normal_percent,
        "low_percent": parsed.low_percent,
        "very_low_percent": parsed.very_low_percent,
        "high_percent": parsed.high_percent,
        "very_high_percent": parsed.very_high_percent,
        # 「低血糖风险」是四个并列标签，选中哪个只靠图形位置，文字层读不出来。
        "lbgi_level": "",
    }


async def _agreed_risk_level(
    recheck: Recheck,
    attachment: MessageAttachment,
) -> str:
    """报告的「低血糖风险」只能靠读两遍定。

    这一项在 PDF 里是四个并列的中文标签，画面上靠图形标记标出被选中的那档——
    文字层拿不到这个标记（既没有字体名也没有填色信息），模板解析只能读到四个标签本身。
    所以这一项单独交给模型独立读两次：两次都读出同一档才留，否则留空，
    界面上干脆不显示这一行。宁可不显示，也不要标错患者属于哪一档风险。
    """

    reads: list[str] = []
    for _ in range(2):
        try:
            read = await recheck(attachment)
        except UnreadableScreenshot:
            return ""
        level = normalize_lbgi(read.get("lbgi_level"))
        if level is None:
            return ""
        reads.append(level)
    return reads[0] if reads[0] == reads[1] else ""


def _date_field(raw: object, label: str) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(
            f"{label}要写成 2024-05-30 这种格式：先问清监测的起止日期，再存这份报告。"
        ) from error


def _float_field(raw: object) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None



def build_glucose_write_tool(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建把报告里的血糖写进记录的工具。"""

    @tool(RECORD_GLUCOSE_TOOL_NAME)
    async def record_glucose_readings(
        runtime: ToolRuntime[AgentContext],
        readings: Annotated[
            list[GlucosePoint],
            Field(description="从报告或化验单里读出来的每一条血糖，一次全给。"),
        ],
        source: Annotated[
            GlucoseReportSource,
            Field(
                description=(
                    "这批数字从哪来：CGM 报告或传感器导出用 cgm，医院化验单用 lab，"
                    "患者念自己血糖仪的数字用 fingerstick。**没有默认值：患者没说清、"
                    "你也问不出来源时，填 manual（其它），不要猜成 cgm。**"
                )
            ),
        ],
        device: Annotated[
            GlucoseMeter | None,
            Field(
                default=None,
                description=(
                    "患者**点名牌子的**血糖仪：罗氏 roche / 雅培 abbott / 三诺 sannuo。"
                    "他没说牌子就留空（None）——不要用「其它血糖仪」占位，"
                    "空着才是真话，填 other 等于替患者编了台机器。"
                ),
            ),
        ] = None,
    ) -> dict[str, object]:
        """把患者发来的报告里的血糖数值写进血糖记录。

        患者发 CGM 报告/化验单/血糖记录截图，或者念出一串数字，并且希望「记下来、
        以后能看」的时候调用。只写库、不判读：解释和安慰在对话里说，不要让患者
        因为你念了一遍数字就以为已经存好了。
        **患者只报了一个数字、没说什么时候测的、也没说怎么测的时候，先问清楚再调
        这个工具**：先问「什么时候测的」，再问「怎么测的（指尖血 / 医院抽血 /
        手环传感器）」，一次只问一件。患者说「记不清」就把时段记成大概时间
        （time_precision=daypart），不要自己编钟点。
        写完后告诉患者「已经记进血糖记录，记录页能看到，也可以撤回」，不要编造
        没读到的数字，读不清的就不记并说明读不清。
        """

        now = datetime.now(UTC)
        oldest = now - timedelta(days=OLDEST_REPORT_DAYS)
        future_limit = now + timedelta(minutes=2)

        cleaned: list[tuple[datetime, GlucosePoint]] = []
        seen: set[tuple[str, int]] = set()
        skipped = 0
        for raw in readings[:MAX_REPORT_READINGS]:
            # 正常调用时框架已经校验过参数；这里再兜一层，是为了让「模型给了半个
            # 字段」这种情况只丢那一条，不至于整次记录都失败。
            try:
                point = raw if isinstance(raw, GlucosePoint) else GlucosePoint.model_validate(raw)
            except (TypeError, ValueError):
                skipped += 1
                continue
            try:
                measured_at = datetime.fromisoformat(point.measured_at)
            except ValueError:
                skipped += 1
                continue
            if measured_at.tzinfo is None:
                skipped += 1
                continue
            if measured_at > future_limit or measured_at < oldest:
                skipped += 1
                continue
            if point.time_precision == "daypart" and measured_at.minute:
                # 只说了「早上」，却给了一个 07:23 这种钟点，等于替患者编了精度。
                # 整批退回让他按整点重来，而不是悄悄改成整点。
                raise ValueError(
                    "只说了「早上 / 晚上」这类时段的时间，要记成该时段的整点"
                    "（早上 07:00、中午 12:00、下午 15:00、晚上 20:00、半夜 02:00），"
                    "分钟必须是 00；不要把编出来的钟点当成患者说的。"
                )
            value_mg_dl = round(point.value_mmol * 18)
            key = (measured_at.isoformat(), value_mg_dl)
            if key in seen:
                # 报告里同一条被重复读出来很常见，重复的只留一条。
                skipped += 1
                continue
            seen.add(key)
            cleaned.append((measured_at, point))

        if not cleaned:
            return {
                "recorded": False,
                "count": 0,
                "skipped": skipped,
                "note": (
                    "一条也没记：需要带时区的测量时间和 mmol/L 数值。"
                    "请先问清时间，再说要不要记。"
                ),
            }

        async with sessions() as session:
            for measured_at, point in cleaned:
                await record_glucose_reading(
                    session,
                    user_id=runtime.context.user_id,
                    value_mg_dl=round(point.value_mmol * 18),
                    measured_at=measured_at,
                    context=point.context,
                    source=source,
                    source_device=device or "",
                    measured_precision=point.time_precision,
                    note=point.note,
                )
            await session.commit()

        rough = sum(1 for _, point in cleaned if point.time_precision == "daypart")
        note = "已经写进血糖记录，患者能在记录页看到，也能逐条撤回。"
        if rough:
            note += f"其中 {rough} 条只有时段（早上/晚上这种），记的是大概时间，回答里要说一句。"
        return {
            "recorded": True,
            "count": len(cleaned),
            "skipped": skipped,
            "source": source,
            "approximate": rough,
            "first_at": cleaned[0][0].isoformat(),
            "last_at": cleaned[-1][0].isoformat(),
            "note": note,
        }

    return record_glucose_readings


def build_glucose_report_tool(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
    *,
    recheck: Recheck = read_report_again,
) -> BaseTool:
    """创建把「一份报告说了什么」存进记录的工具。

    报告常常只有 AGP 图谱和汇总指标（均值、SD、CV、TIR/TAR/TBR、eHbA1c），没有
    逐点读数。硬拆成读数会变成一堆编出来的时间点，所以这一条只存指标本身。

    `recheck` 是截图那条路的第二次读数：模板解析认不出的文件，要让服务端自己再读
    一遍和模型对账，两遍对上才存。
    """

    @tool(RECORD_GLUCOSE_REPORT_TOOL_NAME)
    async def record_glucose_report(
        runtime: ToolRuntime[AgentContext],
        period_start: Annotated[
            str,
            Field(description="监测区间开始日，ISO 日期，例如 2024-05-30。"),
        ],
        period_end: Annotated[
            str,
            Field(description="监测区间结束日，ISO 日期，例如 2024-06-11。"),
        ],
        device: Annotated[
            str,
            Field(default="", description="报告上的设备名，例如 硅基动感Pro。没有就留空。"),
        ] = "",
        device_serial: Annotated[
            str,
            Field(
                default="",
                description="设备序列号/编号，例如 LT2410FE3P。报告上没有就留空。",
            ),
        ] = "",
        report_generated_at: Annotated[
            str | None,
            Field(
                default=None,
                description="报告生成日期，ISO 日期；报告上写了才填。",
            ),
        ] = None,
        mean_mmol: Annotated[
            float | None,
            Field(default=None, description="平均葡萄糖，mmol/L。报告没写就留空。"),
        ] = None,
        sd_mmol: Annotated[
            float | None,
            Field(default=None, description="标准差，mmol/L。"),
        ] = None,
        cv_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="变异系数，百分数，例如 23.27。"),
        ] = None,
        ehba1c_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="预估糖化血红蛋白，百分数。"),
        ] = None,
        tir_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="目标范围内时间占比，百分数。"),
        ] = None,
        tar_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="高于目标范围占比，百分数。"),
        ] = None,
        tbr_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="低于目标范围占比，百分数。"),
        ] = None,
        normal_percent: Annotated[
            float | None,
            Field(
                default=None,
                ge=0,
                le=100,
                description="分档里的「正常/目标范围内」占比，百分数。报告分五档时才填。",
            ),
        ] = None,
        low_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="分档里的「低」占比，百分数。"),
        ] = None,
        very_low_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="分档里的「很低」占比，百分数。"),
        ] = None,
        high_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="分档里的「高」占比，百分数。"),
        ] = None,
        very_high_percent: Annotated[
            float | None,
            Field(default=None, ge=0, le=100, description="分档里的「很高」占比，百分数。"),
        ] = None,
        lbgi_level: Annotated[
            Literal["", "high", "moderate", "low", "lowest"],
            Field(
                default="",
                description=(
                    "报告上「低血糖风险」的档位：high=高、moderate=中、low=低、"
                    "lowest=最低；报告没写就留空。"
                ),
            ),
        ] = "",
        note: Annotated[
            str | None,
            Field(default=None, description="一句备注，例如报告来自哪家医院。"),
        ] = None,
    ) -> dict[str, object]:
        """把一份 CGM/化验报告的关键指标存进记录页，患者以后能回看。

        患者发来报告、你读完之后调用一次，把区间和报告上写着的指标记下来：
        记录页会显示「这份报告说了什么」，而不是我们编出来的曲线。
        只记报告上真的写了的项，没写的留空，不要用估算值补齐，也不要自己算
        平均值或 TIR 填进去。报告如果按「正常/低/很低/高/很高」五档给占比，
        就填 normal/low/very_low/high/very_high 五档（三档的 TAR/TBR 由系统加，
        你不用算）；报告写了「低血糖风险 高/中/低/最低」，就填 lbgi_level。
        存完告诉患者「这份报告我记在血糖记录里了」。
        """

        async with sessions() as session:
            # 数字从哪来，决定了能不能信：
            # 1) 报告 PDF 且模板解析成功 → 用程序读出来的，模型给的一律不看；
            # 2) 截图/认不出的 PDF → 模型读一遍、服务端再独立读一遍，逐项对上才存；
            # 3) 患者自己念的数字（没有附件）→ 按他说的存。
            attachment = await _run_report_attachment(session, runtime.context.run_id)
            parsed: ParsedReport | None = None
            if attachment is not None and attachment.mime_type == "application/pdf":
                try:
                    parsed = parse_report_pdf(attachment.data)
                except UnreadableReport:
                    parsed = None

            if parsed is not None and attachment is not None:
                fields = _parsed_fields(parsed)
                # 只有「低血糖风险」这一档文字层读不出，单独让模型读两遍定。
                fields["lbgi_level"] = await _agreed_risk_level(recheck, attachment)
                verified = "pdf-template"
                source_note = "报告上的数字是按原始 PDF 的模板读出来的，不是模型猜的。"
                if fields["lbgi_level"]:
                    source_note += (
                        "低血糖风险那一档我另外读了两遍，两遍一致才留。"
                    )
            else:
                fields = {
                    "period_start": period_start,
                    "period_end": period_end,
                    "report_generated_at": report_generated_at,
                    "device": device,
                    "device_serial": device_serial,
                    "mean_mmol": mean_mmol,
                    "sd_mmol": sd_mmol,
                    "cv_percent": cv_percent,
                    "ehba1c_percent": ehba1c_percent,
                    "tir_percent": tir_percent,
                    "tar_percent": tar_percent,
                    "tbr_percent": tbr_percent,
                    "normal_percent": normal_percent,
                    "low_percent": low_percent,
                    "very_low_percent": very_low_percent,
                    "high_percent": high_percent,
                    "very_high_percent": very_high_percent,
                    "lbgi_level": lbgi_level,
                }
                verified = "typed"
                source_note = ""
                if attachment is not None:
                    try:
                        second = await recheck(attachment)
                    except UnreadableScreenshot as error:
                        raise ValueError(
                            "这张报告我没读准。为了不把错的数字记进你的记录，这条先"
                            "不存：麻烦把原始 PDF 发我（记录页 → 血糖 → 传感器 → "
                            "导入报告 PDF），或者你自己手动记一条。"
                        ) from error
                    mismatches = compare_reads(fields, second)
                    if mismatches:
                        logger.warning(
                            "报告截图两遍读数对不上（run=%s，字段=%s）",
                            runtime.context.run_id,
                            [item.field_name for item in mismatches],
                        )
                        raise ValueError(
                            "这张截图我读了两遍，两遍对不上（"
                            + "、".join(item.label for item in mismatches[:3])
                            + "）。为了不把错的数字记进你的记录，这条先不存："
                            "麻烦把原始 PDF 发我（记录页 → 血糖 → 传感器 → 导入报告 PDF），"
                            "或者你自己手动记一条。"
                        )
                    verified = "cross-checked"
                    source_note = "截图我读了两遍，两遍一致才存。"

            start = _date_field(fields["period_start"], "监测开始日")
            end = _date_field(fields["period_end"], "监测结束日")
            if start is None or end is None:
                raise ValueError(
                    "日期要写成 2024-05-30 这种格式：先问清监测的起止日期，再存这份报告。"
                )
            if end < start:
                raise ValueError(
                    "监测区间的结束日不能早于开始日。先跟患者确认这两个日期，再存。"
                )
            if end > datetime.now(UTC).date() + timedelta(days=1):
                raise ValueError(
                    "结束日在将来，这份报告的时间还没到。先问清是哪一段时间的报告。"
                )
            generated = _date_field(
                fields["report_generated_at"], "报告生成日期"
            )
            if generated is not None and generated > datetime.now(UTC).date() + timedelta(
                days=1
            ):
                raise ValueError("报告生成日期在将来，先跟患者核对一遍。")

            numbers = {
                key: _float_field(fields[key])
                for key in (
                    "mean_mmol",
                    "sd_mmol",
                    "cv_percent",
                    "ehba1c_percent",
                    "tir_percent",
                    "tar_percent",
                    "tbr_percent",
                    "normal_percent",
                    "low_percent",
                    "very_low_percent",
                    "high_percent",
                    "very_high_percent",
                )
            }
            if all(value is None for value in numbers.values()):
                raise ValueError(
                    "一个指标都没给：至少要读出一个（平均值、TIR、eHbA1c 都可以）。"
                    "读不清就跟患者说你没读清，不要存空壳。"
                )

            report = await save_glucose_report(
                session,
                user_id=runtime.context.user_id,
                period_start=start,
                period_end=end,
                device=str(fields["device"] or "")[:64],
                device_serial=str(fields["device_serial"] or "")[:32],
                report_generated_at=generated,
                lbgi_level=str(fields["lbgi_level"] or ""),
                note=(note or None) and note[:200],
                # 患者这一轮发的那份文件就是它的原文件，记下来「打开原报告」才有得开。
                source_attachment_id=attachment.id if attachment else None,
                **numbers,
            )
            await session.commit()
            report_id = str(report.id)

        return {
            "recorded": True,
            "id": report_id,
            "verified": verified,
            "period": f"{start.isoformat()}~{end.isoformat()}",
            "note": (
                "这份报告的速览已经存进血糖记录，患者能在记录页看到；"
                "解释的时候按报告自己的目标范围说。" + source_note
            ),
        }

    return record_glucose_report


def _squash(text: str) -> str:
    return "".join(text.split())


async def _patient_agreed(
    session: AsyncSession,
    runtime: ToolRuntime[AgentContext],
    consent_quote: str,
) -> bool:
    """患者这一轮真的说了这句话，才算同意删。

    删记录是不可逆的写操作，所以「同意」不能由模型自述：`consent_quote` 必须是这一轮
    患者原话的一部分（去空白后比对）。模型要是自己编一句「好，删吧」，这里对不上。
    """

    quote = _squash(consent_quote)
    if not quote:
        return False
    run = await session.get(AgentRun, runtime.context.run_id)
    if run is None or run.trigger_message_id is None:
        return False
    message = await session.get(Message, run.trigger_message_id)
    if message is None or message.role != MessageRole.USER:
        return False
    return quote in _squash(message.content or "")


def build_glucose_report_delete_tool(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建「患者同意后删掉一份报告速览」的工具。"""

    @tool(DELETE_GLUCOSE_REPORT_TOOL_NAME)
    async def delete_glucose_report(
        runtime: ToolRuntime[AgentContext],
        period_start: Annotated[
            str,
            Field(description="要删的报告的监测开始日，ISO 日期，例如 2025-07-28。"),
        ],
        period_end: Annotated[
            str,
            Field(description="要删的报告的监测结束日，ISO 日期，例如 2025-07-31。"),
        ],
        consent_quote: Annotated[
            str,
            Field(
                description=(
                    "患者这一轮表示同意删除的原话，逐字复制（例如「别人的，删了吧」）。"
                    "必须是患者自己说要删，不能是你替他说的。"
                )
            ),
        ],
    ) -> dict[str, object]:
        """删掉之前存进记录的一份报告速览。

        用之前必须先问一次：「要删掉 07-28 至 07-31 那份报告吗？」——患者明确说要删，
        才把区间和他的原话一起传进来。患者没说话、只是说「这份是别人的」但没有说删，
        就不要调，先问一句。
        不能替患者决定删哪一份：区间要和他说的那份对得上。删完说一句「删了」，不要报 id。
        删掉的只是我们替他存的摘要；他当时发进对话的那条消息和文件还在聊天里。
        """

        async with sessions() as session:
            if not await _patient_agreed(session, runtime, consent_quote):
                raise ValueError(
                    "删记录得患者自己点头：先问一句「要删掉这份报告吗？」，"
                    "等他说了要删，再把他的原话放进 consent_quote 调一次。"
                )

            start = _date_field(period_start, "监测开始日")
            end = _date_field(period_end, "监测结束日")
            if start is None or end is None:
                raise ValueError(
                    "要删哪一份得写清区间：把报告的开始日和结束日写成 2025-07-28 这种格式。"
                )

            found = await session.scalar(
                select(GlucoseReport)
                .where(
                    GlucoseReport.user_id == runtime.context.user_id,
                    GlucoseReport.period_start == start,
                    GlucoseReport.period_end == end,
                )
                .limit(1)
            )
            if found is None:
                raise ValueError(
                    f"记录里没有 {start.isoformat()} 至 {end.isoformat()} 这份报告。"
                    "先问清是哪一份（哪段时间的、哪台设备的），别删错。"
                )

            attachment = (
                await session.get(MessageAttachment, found.source_attachment_id)
                if found.source_attachment_id is not None
                else None
            )
            # 从导入页传进来的文件没挂在任何聊天消息上，删了就真没了；
            # 聊天里发的那份要留着——那是患者自己说的话，不能替他抹掉。
            orphan_attachment = (
                attachment
                if attachment is not None and attachment.message_id is None
                else None
            )

            removed = await drop_glucose_report(
                session, user_id=runtime.context.user_id, report_id=found.id
            )
            if not removed:
                raise ValueError("这份报告没删成功，让患者自己去记录页删一下试试。")
            if orphan_attachment is not None:
                await session.delete(orphan_attachment)
            await session.commit()

        return {
            "deleted": True,
            "period": f"{start.isoformat()}~{end.isoformat()}",
            "file_removed": orphan_attachment is not None,
        }

    return delete_glucose_report

